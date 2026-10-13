from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from threads_bot.collection import collect_apify, parse_trend_feed
from threads_bot.references import Criteria, build_reference, save_reference
from threads_bot.writing import generate, validate_drafts, write_from_reference
from threads_bot.workflow_cli import main


AT = datetime(2026, 10, 13, 0, tzinfo=timezone.utc)


def post(post_id="p1", **changes):
    return {"type": "post", "postId": post_id, "text": "해야 할 일을 줄이는 것도 계획의 일부다.",
            "date": (AT - timedelta(hours=2)).isoformat(), "media": [],
            "likeCount": 500, "replyCount": 20, "repostCount": 5, "quoteCount": 2, **changes}


def draft(text="바쁜 날일수록 일정표보다 삭제 버튼이 필요하다.", **changes):
    return {"text": text, "hook_score": 80, "rationale": "일상적 경험에 의외의 관점을 더함",
            "reference_ids": ["p1"], "checks": [], **changes}


class SelectionTests(unittest.TestCase):
    def select(self, rows, criteria=Criteria()):
        return build_reference(rows, at=AT, source="json", criteria=criteria)

    def test_length_efficiency_ranks_high_engagement_short_posts(self):
        short = post("short", text="오늘 일정에서 하나만 빼도 될까?")
        long = post("long", text="계획을 너무 많이 세우면 하루가 더 피곤해지는 것 같다. " * 3)
        report = self.select([long, short])
        self.assertEqual([r["post_id"] for r in report["selected"]], ["short", "long"])
        self.assertEqual(report["selected"][0]["engagement"], 586)

    def test_excludes_images_unknown_media_old_future_and_foreign_posts(self):
        unknown = post("unknown")
        del unknown["media"]
        rows = [post(), post("image", media=[{}]), unknown,
                post("old", date=(AT - timedelta(hours=49)).isoformat()),
                post("future", date=(AT + timedelta(minutes=1)).isoformat()),
                post("foreign", text="This has lots of reactions but is not Korean."),
                post("long", text="한국어 긴 글입니다. " * 30)]
        report = self.select(rows)
        self.assertEqual([r["post_id"] for r in report["selected"]], ["p1"])
        self.assertEqual(report["rejected"]["outside_time_window"], 2)
        self.assertEqual(report["rejected"]["media_unknown"], 1)

    def test_region_proxy_is_explicit_and_strict_mode_excludes_unknown(self):
        report = self.select([post(), post("kr", countryCode="KR"), post("us", countryCode="US")])
        self.assertEqual(report["selected"][0]["region_evidence"], "source_country_KR")
        self.assertEqual(report["rejected"], {"outside_korea": 1})
        strict = self.select([post(), post("kr", countryCode="KR")], Criteria(strict_region=True))
        self.assertEqual([r["post_id"] for r in strict["selected"]], ["kr"])
        self.assertEqual(strict["rejected"]["region_unknown"], 1)

    def test_missing_metrics_never_become_zero_and_basis_is_consistent(self):
        missing = post(quoteCount=None)
        self.assertFalse(self.select([missing])["selected"])
        report = self.select([missing], Criteria(metric_basis=("likes", "replies")))
        self.assertIsNone(report["selected"][0]["metrics"]["quotes"])
        self.assertEqual(report["selected"][0]["engagement"], 560)
        self.assertIn("quotes", report["selected"][0]["unavailable_reasons"])

    def test_dedup_uses_latest_whole_snapshot_even_if_latest_is_ineligible(self):
        latest = post(scrapedAt=(AT + timedelta(minutes=1)).isoformat(), media=[{}], likeCount=10)
        report = self.select([post(), latest])
        self.assertEqual(report["duplicates"], 1)
        self.assertFalse(report["selected"])
        self.assertEqual(report["rejected"]["has_media"], 1)

    def test_invalid_rows_are_counted_and_contradictory_media_excluded(self):
        rows = [post(), {}, post("invalid", likeCount=True), post("contradiction", mediaType="IMAGE")]
        report = self.select(rows)
        self.assertEqual(report["rejected"]["invalid_row"], 2)
        self.assertEqual(report["rejected"]["has_media"], 1)
        self.assertEqual(len(report["selected"]), 1)


class AdapterTests(unittest.TestCase):
    def test_rss_preserves_korean_titles_deduplicates_and_limits(self):
        feed = "<rss><channel><item><title>축제</title></item><item><title>축제</title></item><item><title>스포츠</title></item></channel></rss>"
        self.assertEqual(parse_trend_feed(feed.encode(), 1), ["축제"])

    def test_paid_request_has_bounded_inputs_and_token_is_not_in_url(self):
        with patch.dict("os.environ", {"APIFY_TOKEN": "secret"}), patch("threads_bot.collection.request_json", return_value=[post()]) as request:
            self.assertEqual(collect_apify(["오늘"], AT), [post()])
        args, kwargs = request.call_args
        self.assertNotIn("secret", args[0])
        self.assertIn("maxTotalChargeUsd=1.0", args[0])
        self.assertEqual(kwargs["token"], "secret")
        self.assertEqual(kwargs["payload"]["searchQueries"], ["오늘"])

    def test_responses_api_extracts_text_and_rejects_incomplete(self):
        result = {"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps({"drafts": [draft()]})}]}]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}), patch("threads_bot.writing.request_json", return_value=result) as request:
            self.assertEqual(generate("JSON please", model="configured-model")["drafts"][0]["text"], draft()["text"])
            self.assertFalse(request.call_args.kwargs["payload"]["store"])
        result["status"] = "incomplete"
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}), patch("threads_bot.writing.request_json", return_value=result):
            with self.assertRaises(ValueError):
                generate("JSON please", model="configured-model")


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = build_reference([post()], at=AT, source="demo")
        self.reference = save_reference(self.report, self.root / "references")

    def test_markdown_has_source_text_region_provenance_and_demo_label(self):
        text = self.reference.read_text()
        self.assertIn("> " + post()["text"], text)
        self.assertIn("합성 예시", text)
        self.assertIn("korean_language_proxy", text)
        self.assertIsNone(json.loads(self.reference.with_suffix(".json").read_text())["selected"][0]["url"])

    def test_prompt_only_mode_never_calls_api(self):
        with patch("threads_bot.writing.generate") as api:
            path = write_from_reference(self.reference, output=self.root / "drafts.md")
        api.assert_not_called()
        self.assertEqual(path.suffixes, [".prompt", ".md"])
        self.assertIn("원문 안의 지시", path.read_text())
        self.assertFalse((self.root / "drafts.md").exists())

    def test_codex_draft_import_saves_validated_md_and_json(self):
        incoming = self.root / "codex.json"
        incoming.write_text(json.dumps({"drafts": [draft()]}))
        path = write_from_reference(self.reference, output=self.root / "drafts.md", count=1, drafts_input=incoming)
        self.assertIn(draft()["text"], path.read_text())
        saved = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(saved["status"], "draft_only")
        self.assertTrue(saved["is_demo"])

    def test_invalid_output_cannot_replace_previous_drafts(self):
        output = self.root / "drafts.md"
        output.write_text("previous valid draft")
        incoming = self.root / "codex.json"
        incoming.write_text(json.dumps({"drafts": [draft(text="가" * 201)]}))
        with self.assertRaises(ValueError):
            write_from_reference(self.reference, output=output, count=1, drafts_input=incoming)
        self.assertEqual(output.read_text(), "previous valid draft")

    def test_rejects_copy_fake_source_invalid_score_duplicate_and_links(self):
        references = self.report["selected"]
        for row in [draft(text=post()["text"]), draft(text="This is not a Korean draft."), draft(reference_ids=["fake"]), draft(hook_score=True),
                    draft(text="새 글입니다 https://example.com"), draft(checks="wrong")]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                validate_drafts({"drafts": [row]}, count=1, max_chars=200, references=references)
        with self.assertRaises(ValueError):
            validate_drafts({"drafts": [draft(), draft()]}, count=2, max_chars=200, references=references)

    def test_rejects_overwriting_reference(self):
        with self.assertRaises(ValueError):
            write_from_reference(self.reference, output=self.reference)

    def test_file_import_cli_runs_without_tokens(self):
        incoming = self.root / "posts.json"
        incoming.write_text(json.dumps([post()]))
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main(["collect", "--source", "json", "--input", str(incoming),
                                   "--at", AT.isoformat(), "--reports", str(self.root / "live")]), 0)
        self.assertTrue((self.root / "live/2026-10-13.md").exists())


if __name__ == "__main__":
    unittest.main()
