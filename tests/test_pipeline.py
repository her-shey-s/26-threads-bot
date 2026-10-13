import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from threads_bot.models import Observation, Post
from threads_bot.pipeline import run_daily
from threads_bot.ranking import deduplicate, rank, scores
from threads_bot.sources import DemoPostSource, DemoTrendSource


AT = datetime(2026, 10, 10, 16, tzinfo=timezone.utc)


def observation(post_id="p1", *, age=2, metrics=None, reasons=None):
    return Observation(
        Post(post_id, "첫 문장!\n어떻게 생각하세요?", None, None, AT - timedelta(hours=age), None),
        AT,
        metrics if metrics is not None else {"likes": 10, "replies": 2, "reposts": 1, "quotes": 1},
        reasons or {},
    )


class RankingTests(unittest.TestCase):
    def test_weighted_score_and_time_adjustment(self):
        popular, rising = scores(observation(), AT)
        self.assertEqual(popular, 23)
        self.assertAlmostEqual(rising, 23 / 4 ** 0.7)

    def test_missing_metric_is_not_zero_or_ranked(self):
        item = observation(
            metrics={"likes": 10, "replies": 2, "reposts": 1, "quotes": None},
            reasons={"quotes": "not_available"},
        )
        self.assertEqual(scores(item, AT), (None, None))
        self.assertEqual(rank([item], AT), {"popular": [], "rising": []})

    def test_missing_or_future_timestamp_excluded_only_from_rising(self):
        item = observation()
        for timestamp in (None, AT + timedelta(hours=1)):
            changed = replace(item, post=replace(item.post, published_at=timestamp))
            self.assertEqual(scores(changed, AT), (23, None))

    def test_zero_is_a_valid_observed_metric(self):
        item = observation(metrics={name: 0 for name in ("likes", "replies", "reposts", "quotes")})
        self.assertEqual(scores(item, AT), (0, 0))
        self.assertEqual(len(rank([item], AT)["popular"]), 1)

    def test_duplicate_uses_latest_whole_snapshot(self):
        first = observation()
        latest = replace(first, observed_at=AT + timedelta(hours=1), metrics={
            "likes": 3, "replies": 0, "reposts": 0, "quotes": 0,
        })
        self.assertEqual(deduplicate([first, latest, first]), [latest])

    def test_ties_use_stable_post_id_order(self):
        result = rank([observation("b"), observation("a")], AT, limit=1)
        self.assertEqual(result["popular"][0].observation.post.id, "a")

    def test_rejects_invalid_metrics_and_naive_timestamps(self):
        for value in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                observation(metrics={"likes": value, "replies": 0, "reposts": 0, "quotes": 0})
        with self.assertRaises(ValueError):
            observation(metrics={"likes": 1})
        with self.assertRaises(ValueError):
            replace(observation(), observed_at=AT.replace(tzinfo=None))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "data.sqlite3"
        self.reports = self.root / "reports"

    def run_demo(self, source=None, at=AT):
        return run_daily(
            DemoTrendSource(), source or DemoPostSource(), at=at,
            database=self.database, reports=self.reports, source_name="demo",
        )

    def test_end_to_end_sqlite_json_csv_and_timezone(self):
        result = self.run_demo()
        self.assertTrue(result["is_demo"])
        self.assertEqual(result["report_date"], "2026-10-11")
        self.assertEqual((result["candidate_count"], result["unique_count"]), (25, 24))
        self.assertEqual([len(rows) for rows in result["rankings"].values()], [10, 10])
        stored = json.loads((self.reports / "2026-10-11.json").read_text())
        self.assertEqual(stored, result)
        with (self.reports / "2026-10-11.csv").open(newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual(len(csv_rows), 20)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 24)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM metrics WHERE quotes IS NULL").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT status FROM job_runs").fetchone()[0], "success")
            excluded = connection.execute(
                "SELECT COUNT(*) FROM daily_rankings WHERE post_id IN ('demo-008', 'demo-016', 'demo-024')"
            ).fetchone()[0]
            self.assertEqual(excluded, 0)

    def test_fixed_time_rerun_is_idempotent_and_keeps_attempt_history(self):
        first = self.run_demo()
        second = self.run_demo()
        self.assertEqual(first, second)
        with sqlite3.connect(self.database) as connection:
            for table, count in (("metrics", 24), ("features", 24), ("trends", 3), ("daily_rankings", 20), ("job_runs", 2)):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], count)

    def test_later_run_keeps_metric_history(self):
        self.run_demo()
        self.run_demo(at=AT + timedelta(hours=1))
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 48)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_rankings").fetchone()[0], 20)

    def test_empty_collection_fails_and_preserves_previous_rankings(self):
        self.run_demo()

        class EmptySource:
            def search(self, trends, at):
                return []

        with self.assertRaisesRegex(ValueError, "no candidates"):
            self.run_demo(EmptySource())
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_rankings").fetchone()[0], 20)
            self.assertEqual(connection.execute("SELECT status, error_code FROM job_runs ORDER BY id DESC LIMIT 1").fetchone(), ("failed", "ValueError"))

    def test_report_failure_rolls_back_results_and_records_failure(self):
        with patch("threads_bot.pipeline.write_reports", side_effect=OSError("sensitive provider detail")):
            with self.assertRaises(OSError):
                self.run_demo()
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT status, error_code FROM job_runs").fetchone(), ("failed", "OSError"))


if __name__ == "__main__":
    unittest.main()
