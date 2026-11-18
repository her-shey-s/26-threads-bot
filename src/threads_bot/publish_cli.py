"""Scheduled publishing entry point: `python -m threads_bot.publish_cli`.

Default mode is dry-run (no network). Live mode needs `--mode live` and the
THREADS_USER_ID / THREADS_ACCESS_TOKEN environment variables.
"""

import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

from .drafts import load_queue
from .publish_loop import Limits, read_history, run_once, TERMINAL_STATUSES
from .publisher import AuthError, DryRunPublisher, ThreadsPublisher

DEFAULT_HISTORY = {"dry-run": Path("data/dry-run-history.jsonl"), "live": Path("content/history.jsonl")}
EXIT_CODES = {
    "idle": 0, "paused": 0, "waiting": 0, "published": 0,
    "rejected": 1, "retry": 1, "failed": 1, "uncertain": 1, "auth_error": 3,
}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Threads 예약 게시 (기본: dry-run)")
    parser.add_argument("command", nargs="?", choices=["run", "check"], default="run")
    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    parser.add_argument("--queue", type=Path, default=Path("content/queue"))
    parser.add_argument("--history", type=Path, help="기본: dry-run은 data/, live는 content/history.jsonl")
    parser.add_argument("--at", help="Timezone-aware ISO timestamp (기본: 현재 시각)")
    parser.add_argument("--max-per-day", type=int, default=3)
    parser.add_argument("--min-interval-minutes", type=int, default=120)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)

    history = args.history or DEFAULT_HISTORY[args.mode]
    try:
        at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        if at.utcoffset() is None:
            raise ValueError
    except ValueError:
        print("--at은 시간대가 포함된 ISO 8601 시각이어야 합니다.", file=sys.stderr)
        return 2

    if args.command == "check":
        return _check(args.queue, history, at)

    if args.mode == "live":
        try:
            publisher = ThreadsPublisher(
                os.environ.get("THREADS_USER_ID", ""), os.environ.get("THREADS_ACCESS_TOKEN", "")
            )
        except AuthError as error:
            print(f"[LIVE] 설정 오류: {error}", file=sys.stderr)
            return 2
    else:
        publisher = DryRunPublisher()

    limits = Limits(
        max_per_day=args.max_per_day,
        min_interval=timedelta(minutes=args.min_interval_minutes),
        max_attempts=args.max_attempts,
    )
    outcome = run_once(
        args.queue, history, publisher, at, limits,
        paused=_truthy(os.environ.get("THREADS_PUBLISH_PAUSED")),
    )
    label = "[LIVE]" if args.mode == "live" else "[DRY-RUN]"
    for draft_id, problem in outcome.invalid_files.items():
        print(f"{label} 잘못된 대기열 파일 {draft_id}.json: {problem}", file=sys.stderr)
    line = f"{label} {outcome.status}"
    if outcome.draft_id:
        line += f" draft={outcome.draft_id}"
    if outcome.post_id:
        line += f" post_id={outcome.post_id}"
    if outcome.detail:
        line += f" ({outcome.detail})"
    print(line, file=sys.stderr if EXIT_CODES[outcome.status] else sys.stdout)
    if isinstance(publisher, DryRunPublisher):
        for text, reply_to in publisher.calls:
            prefix = f"  댓글(→{reply_to})" if reply_to else "  본문"
            print(f"{prefix}: {text}")
    print(f"게시 이력: {history}")
    return EXIT_CODES[outcome.status]


def _check(queue: Path, history_path: Path, at: datetime) -> int:
    drafts, invalid = load_queue(queue)
    history = read_history(history_path)
    status = {}
    for record in history:
        if record["status"] in TERMINAL_STATUSES:
            status[record["draft_id"]] = record["status"]
    for draft in drafts:
        state = status.get(draft.id) or ("due" if draft.scheduled_at <= at else "scheduled")
        print(f"{draft.scheduled_at.isoformat()}  {state:<10} {draft.id}")
    for draft_id, problem in invalid.items():
        print(f"INVALID {draft_id}.json: {problem}", file=sys.stderr)
    if not drafts and not invalid:
        print(f"대기열이 비어 있습니다: {queue}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
