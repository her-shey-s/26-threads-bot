import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

from .pipeline import run_daily
from .sources import DemoPostSource, DemoTrendSource


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"collect", "write"}:
        from .workflow_cli import main as workflow_main
        return workflow_main(sys.argv[1:])
    parser = argparse.ArgumentParser(description="Threads 일일 랭킹 MVP (현재 샘플 데이터 전용)")
    parser.add_argument("--source", choices=["demo"], required=True)
    parser.add_argument("--database", type=Path, default=Path("data/demo.sqlite3"))
    parser.add_argument("--reports", type=Path, default=Path("reports/demo"))
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--at", help="Timezone-aware ISO timestamp for repeatable demo runs")
    args = parser.parse_args()
    try:
        at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        report = run_daily(
            DemoTrendSource(), DemoPostSource(), at=at, database=args.database,
            reports=args.reports, source_name=args.source, report_timezone=args.timezone,
        )
    except Exception as error:
        print(f"실행 실패: {type(error).__name__}. 설정과 job_runs를 확인하세요.", file=sys.stderr)
        return 1
    print(
        f"[DEMO] {report['report_date']} 후보 {report['candidate_count']}개 → "
        f"중복 제거 {report['unique_count']}개; "
        f"인기 {len(report['rankings']['popular'])}개, 시간 보정 {len(report['rankings']['rising'])}개"
    )
    print(f"SQLite: {args.database}; JSON/CSV: {args.reports}")
    return 0
