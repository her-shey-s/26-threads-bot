"""One scheduled publishing pass: pick at most one due draft, check guards, publish, record."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path

from .drafts import Draft, load_queue
from .publisher import AuthError, PermanentError, PublishError, Publisher, TransientError

# Unverified API limit (third-party guides say 500); confirm in Phase 0.
MAX_TEXT_LENGTH = 500
DISCLOSURE_MARKERS = ("수수료를 제공받", "파트너스 활동의 일환", "#광고", "#ad", "affiliate", "commission")
SIMILARITY_LIMIT = 0.9
TERMINAL_STATUSES = {"published", "rejected", "failed", "uncertain"}


@dataclass(frozen=True)
class Limits:
    max_per_day: int = 3
    min_interval: timedelta = timedelta(minutes=120)
    max_attempts: int = 3
    quota_reserve: int = 10


@dataclass
class Outcome:
    status: str  # idle | paused | waiting | published | rejected | retry | failed | uncertain | auth_error
    draft_id: str | None = None
    detail: str = ""
    post_id: str | None = None
    invalid_files: dict[str, str] = field(default_factory=dict)


def read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_history(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def check_draft(draft: Draft, published_texts: list[str]) -> str | None:
    """Return a rejection reason, or None if the draft may be posted."""
    if len(draft.text) > MAX_TEXT_LENGTH:
        return f"text_too_long:{len(draft.text)}>{MAX_TEXT_LENGTH}"
    if draft.reply_text and len(draft.reply_text) > MAX_TEXT_LENGTH:
        return f"reply_too_long:{len(draft.reply_text)}>{MAX_TEXT_LENGTH}"
    if draft.affiliate:
        combined = (draft.text + "\n" + (draft.reply_text or "")).lower()
        if not any(marker.lower() in combined for marker in DISCLOSURE_MARKERS):
            return "affiliate_disclosure_missing"
    normalized = _normalize(draft.text)
    for previous in published_texts:
        other = _normalize(previous)
        if normalized == other:
            return "duplicate_text"
        if SequenceMatcher(None, normalized, other).ratio() >= SIMILARITY_LIMIT:
            return "near_duplicate_text"
    return None


def run_once(
    queue_dir: Path,
    history_path: Path,
    publisher: Publisher,
    at: datetime,
    limits: Limits = Limits(),
    paused: bool = False,
) -> Outcome:
    if at.utcoffset() is None:
        raise ValueError("at must include a timezone")
    drafts, invalid = load_queue(queue_dir)
    if paused:
        return Outcome("paused", detail="THREADS_PUBLISH_PAUSED is set", invalid_files=invalid)

    history = read_history(history_path)
    done = {r["draft_id"] for r in history if r["status"] in TERMINAL_STATUSES}
    attempts: dict[str, int] = {}
    for record in history:
        if record["status"] == "retry":
            attempts[record["draft_id"]] = attempts.get(record["draft_id"], 0) + 1

    due = [d for d in drafts if d.id not in done and d.scheduled_at <= at]
    if not due:
        return Outcome("idle", detail="no due drafts", invalid_files=invalid)
    draft = due[0]

    published = [r for r in history if r["status"] == "published"]
    times = [datetime.fromisoformat(r["at"]) for r in published]
    if sum(1 for t in times if t > at - timedelta(hours=24)) >= limits.max_per_day:
        return Outcome("waiting", draft.id, "daily_limit", invalid_files=invalid)
    if times and at - max(times) < limits.min_interval:
        return Outcome("waiting", draft.id, "min_interval", invalid_files=invalid)

    def record(status: str, **extra: object) -> None:
        append_history(history_path, {
            "draft_id": draft.id, "status": status,
            "at": at.astimezone(timezone.utc).isoformat(), **extra,
        })

    reason = check_draft(draft, [r.get("text", "") for r in published])
    if reason:
        record("rejected", reason=reason)
        return Outcome("rejected", draft.id, reason, invalid_files=invalid)

    try:
        quota = publisher.quota()
    except AuthError as error:
        return Outcome("auth_error", draft.id, str(error), invalid_files=invalid)
    if quota is not None and quota.total - quota.used <= limits.quota_reserve:
        return Outcome("waiting", draft.id, f"quota {quota.used}/{quota.total}", invalid_files=invalid)

    try:
        post_id = publisher.publish(draft.text)
    except AuthError as error:
        # Not recorded: the draft stays eligible once the token is fixed.
        return Outcome("auth_error", draft.id, str(error), invalid_files=invalid)
    except PublishError as error:
        return _record_failure(draft, error, attempts.get(draft.id, 0), limits, record, invalid)

    record("published", post_id=post_id, text=draft.text, affiliate=draft.affiliate)
    outcome = Outcome("published", draft.id, post_id=post_id, invalid_files=invalid)
    if draft.reply_text:
        try:
            reply_id = publisher.publish(draft.reply_text, reply_to_id=post_id)
            record("reply_published", post_id=reply_id, parent_id=post_id)
        except PublishError as error:
            record("reply_failed", error=f"{type(error).__name__}: {error}", parent_id=post_id)
            outcome.detail = f"reply failed: {type(error).__name__}"
    return outcome


def _record_failure(draft, error, previous_attempts, limits, record, invalid) -> Outcome:
    summary = f"{type(error).__name__}: {error}"
    if error.stage == "publish" and not isinstance(error, PermanentError):
        # The publish call may have gone through; never post again automatically.
        record("uncertain", error=summary)
        return Outcome("uncertain", draft.id, summary, invalid_files=invalid)
    if isinstance(error, TransientError) and previous_attempts + 1 < limits.max_attempts:
        record("retry", error=summary)
        return Outcome("retry", draft.id, summary, invalid_files=invalid)
    record("failed", error=summary)
    return Outcome("failed", draft.id, summary, invalid_files=invalid)
