import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .features import extract_features
from .models import METRIC_WEIGHTS
from .ranking import deduplicate, rank, scores
from .sources import PostSource, TrendSource
from .storage import open_database, save_results


def write_reports(report: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / report["report_date"]
    json_path = base.with_suffix(".json")
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    csv_path = base.with_suffix(".csv")
    temporary = csv_path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "kind", "rank", "post_id", "score", "published_at", "observed_at", "url",
        ])
        writer.writeheader()
        for kind, rows in report["rankings"].items():
            for index, row in enumerate(rows, 1):
                writer.writerow({"kind": kind, "rank": index, **row})
    temporary.replace(csv_path)


def run_daily(
    trend_source: TrendSource,
    post_source: PostSource,
    *,
    at: datetime,
    database: Path,
    reports: Path,
    source_name: str,
    report_timezone: str = "Asia/Seoul",
    limit: int = 10,
) -> dict:
    if at.utcoffset() is None:
        raise ValueError("Run time must include a timezone")
    if limit < 1:
        raise ValueError("Ranking limit must be positive")
    at = at.astimezone(timezone.utc)
    report_date = at.astimezone(ZoneInfo(report_timezone)).date().isoformat()
    connection = open_database(database)
    try:
        cursor = connection.execute(
            "INSERT INTO job_runs (started_at, report_date, source, status) VALUES (?, ?, ?, 'running')",
            (at.isoformat(), report_date, source_name),
        )
        run_id = cursor.lastrowid
        connection.commit()
        try:
            trends = list(trend_source.discover(at))
            if not trends:
                raise ValueError("Trend source returned no keywords")
            candidates = list(post_source.search(trends, at))
            if not candidates:
                raise ValueError("Post source returned no candidates")
            items = deduplicate(candidates)
            rankings = rank(items, at, limit)
            report = {
                "source": source_name,
                "is_demo": source_name == "demo",
                "report_date": report_date,
                "timezone": report_timezone,
                "observed_at": at.isoformat(),
                "candidate_count": len(candidates),
                "unique_count": len(items),
                "ranking_weights": METRIC_WEIGHTS,
                "rising_method": "E / (age_hours + 2)^0.7; not measured growth",
                "trends": [{"keyword": t.keyword, "source": t.source} for t in trends],
                "rankings": {
                    kind: [{
                        "post_id": row.observation.post.id,
                        "score": row.score,
                        "published_at": row.observation.post.published_at.isoformat()
                        if row.observation.post.published_at else None,
                        "observed_at": row.observation.observed_at.isoformat(),
                        "url": row.observation.post.url,
                    } for row in rows]
                    for kind, rows in rankings.items()
                },
                "candidates": [{
                    "post_id": item.post.id,
                    "metrics": {name: item.metrics.get(name) for name in METRIC_WEIGHTS},
                    "unavailable_reasons": item.unavailable_reasons,
                    "ranking_exclusions": [
                        kind for kind, score in zip(("popular", "rising"), scores(item, at))
                        if score is None
                    ],
                    "features": extract_features(item.post),
                } for item in items],
            }
            with connection:
                save_results(connection, run_id, report_date, trends, items, rankings)
                write_reports(report, reports)
                connection.execute(
                    """UPDATE job_runs SET finished_at=?, status='success',
                    candidate_count=?, unique_count=? WHERE id=?""",
                    (datetime.now(timezone.utc).isoformat(), len(candidates), len(items), run_id),
                )
            return report
        except Exception as error:
            with connection:
                # Record only the error type; provider exception messages may contain tokens.
                connection.execute(
                    "UPDATE job_runs SET finished_at=?, status='failed', error_code=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), type(error).__name__, run_id),
                )
            raise
    finally:
        connection.close()
