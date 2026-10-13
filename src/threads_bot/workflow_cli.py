import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from .collection import collect_apify, discover_queries
from .references import Criteria, atomic_text, build_reference, save_reference
from .writing import write_from_reference


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="한국어 짧은 글 수집 → Markdown → 글쓰기 초안")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="조건을 만족하는 레퍼런스 저장")
    collect.add_argument("--source", choices=["demo", "json", "apify"], required=True)
    collect.add_argument("--input", type=Path, help="WebData Labs 형식의 JSON dataset 파일")
    collect.add_argument("--query", action="append", help="생략하면 한국 트렌드 RSS + 일상 검색어 자동 발굴")
    collect.add_argument("--at", help="시간대가 있는 ISO 시각")
    collect.add_argument("--max-chars", type=int, default=200)
    collect.add_argument("--hours", type=float, default=48)
    collect.add_argument("--min-engagement", type=float, default=100)
    collect.add_argument("--limit", type=int, default=20)
    collect.add_argument("--strict-region", action="store_true", help="countryCode=KR이 확인된 글만 선택")
    collect.add_argument("--metric-basis", default="likes,replies,reposts,quotes")
    collect.add_argument("--max-charge", type=float, default=1.0, help="Apify 1회 지출 상한 USD")
    collect.add_argument("--per-query", type=int, default=20)
    collect.add_argument("--reports", type=Path)
    write = commands.add_parser("write", help="레퍼런스를 읽고 작성 요청 또는 AI 초안 저장")
    write.add_argument("--reference", type=Path, required=True)
    write.add_argument("--output", type=Path, help="기본: 레퍼런스와 같은 폴더의 YYYY-MM-DD.drafts.md")
    write.add_argument("--count", type=int, default=3)
    write.add_argument("--max-chars", type=int, default=200)
    write.add_argument("--model", help="설정하면 AI API 호출; 생략하면 Codex용 작성 요청만 저장")
    write.add_argument("--drafts-input", type=Path, help="Codex가 작성한 JSON 초안을 검증해 저장")
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            output = args.output or args.reference.with_name(args.reference.stem + ".drafts.md")
            path = write_from_reference(args.reference, output=output, count=args.count,
                                        max_chars=args.max_chars, model=args.model,
                                        drafts_input=args.drafts_input)
            print(f"{'AI 검토용 초안' if args.model or args.drafts_input else 'Codex용 작성 요청 (아직 초안 아님)'}: {path}")
            return 0
        at = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        criteria = Criteria(args.max_chars, args.hours, args.min_engagement, args.limit,
                            args.strict_region, tuple(args.metric_basis.split(",")))
        if at.utcoffset() is None:
            raise ValueError("--at must include a timezone")
        queries = args.query or []
        if args.source == "apify":
            if args.input:
                raise ValueError("--input is only for json/demo")
            if not os.environ.get("APIFY_TOKEN"):
                raise ValueError("APIFY_TOKEN is required in secret settings")
            queries = queries or discover_queries()
            rows = collect_apify(queries, at, hours=args.hours, per_query=args.per_query,
                                 max_charge=args.max_charge)
            # Preserve the paid response even if later normalization/filtering fails.
            raw_path = Path("data/live") / (at.strftime("%Y%m%dT%H%M%S%z") + ".json")
            atomic_text(raw_path, json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        else:
            input_path = args.input
            if args.source == "demo":
                input_path = input_path or Path(__file__).with_name("reference_demo.json")
                if args.at is None:
                    at = datetime.fromisoformat("2026-10-13T09:00:00+09:00")
            if input_path is None:
                raise ValueError("--input is required for json source")
            rows = json.loads(input_path.read_text(encoding="utf-8"))
        report = build_reference(rows, at=at, source=args.source, criteria=criteria, queries=queries)
        directory = args.reports or Path("reports") / ("reference-demo" if args.source == "demo" else "reference-live")
        print(f"{'[DEMO] ' if report['is_demo'] else ''}수집 {report['input_count']}개 → 고유 {report['unique_count']}개 → 조건 충족 {report['eligible_count']}개")
        print("제외 집계: " + json.dumps(report["rejected"], ensure_ascii=False))
        if not report["selected"]:
            # Keep diagnostics without replacing any successful daily reference.
            atomic_text(directory / f"{report['report_date']}.rejected.json", json.dumps(report, ensure_ascii=False, indent=2))
        path = save_reference(report, directory)
        print(f"레퍼런스 Markdown: {path}")
        return 0
    except (ValueError, OSError, RuntimeError, TypeError) as error:
        # Neither provider error bodies nor source text / tokens should be printed.
        print(f"실행 실패: {type(error).__name__}. 입력 형식·조건·비밀 설정·네트워크를 확인하세요.", file=sys.stderr)
        return 1
