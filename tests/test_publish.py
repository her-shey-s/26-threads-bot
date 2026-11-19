from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from threads_bot import publish_cli
from threads_bot.drafts import load_queue
from threads_bot.publish_loop import Limits, read_history, run_once
from threads_bot.publisher import (
    AuthError, DryRunPublisher, PermanentError, Quota, ThreadsPublisher, TransientError, classify_http_error,
)

KST = timezone(timedelta(hours=9))
AT = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
SECRET = "test-token-value"


class FlakyPublisher(DryRunPublisher):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    def publish(self, text, reply_to_id=None):
        if self.errors:
            raise self.errors.pop(0)
        return super().publish(text, reply_to_id)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.queue = root / "queue"
        self.queue.mkdir()
        self.history = root / "history.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, draft_id, text="안녕하세요, 오늘의 글입니다.", offset_minutes=-5, **extra):
        data = {"text": text, "scheduled_at": (AT + timedelta(minutes=offset_minutes)).isoformat(), **extra}
        (self.queue / f"{draft_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_at(self, publisher=None, at=AT, limits=Limits(), paused=False):
        return run_once(self.queue, self.history, publisher or DryRunPublisher(), at, limits, paused)

    def test_publishes_earliest_due_draft_once(self):
        self.add("b", "두 번째 글이에요", offset_minutes=-1)
        self.add("a", "첫 번째 글이에요", offset_minutes=-30)
        self.add("later", "아직 이른 글", offset_minutes=30)
        publisher = DryRunPublisher()
        outcome = self.run_at(publisher)
        self.assertEqual((outcome.status, outcome.draft_id), ("published", "a"))
        self.assertEqual(publisher.calls, [("첫 번째 글이에요", None)])
        self.assertEqual(read_history(self.history)[0]["status"], "published")

    def test_idle_when_nothing_due(self):
        self.add("later", offset_minutes=30)
        self.assertEqual(self.run_at().status, "idle")
        self.assertFalse(self.history.exists())

    def test_paused_publishes_nothing(self):
        self.add("a")
        publisher = DryRunPublisher()
        self.assertEqual(self.run_at(publisher, paused=True).status, "paused")
        self.assertEqual(publisher.calls, [])

    def test_min_interval_and_daily_limit(self):
        self.add("a", "하나")
        self.add("b", "완전히 다른 두 번째 내용")
        self.add("c", "세 번째는 또 다른 이야기")
        limits = Limits(max_per_day=2, min_interval=timedelta(hours=1))
        self.assertEqual(self.run_at(limits=limits).status, "published")
        waiting = self.run_at(at=AT + timedelta(minutes=30), limits=limits)
        self.assertEqual((waiting.status, waiting.detail), ("waiting", "min_interval"))
        self.assertEqual(self.run_at(at=AT + timedelta(hours=1), limits=limits).status, "published")
        capped = self.run_at(at=AT + timedelta(hours=3), limits=limits)
        self.assertEqual((capped.status, capped.detail), ("waiting", "daily_limit"))
        self.assertEqual(self.run_at(at=AT + timedelta(hours=25), limits=limits).status, "published")

    def test_rejects_duplicates_long_text_and_missing_disclosure(self):
        self.add("a", "똑같은 글 내용입니다")
        self.run_at()
        self.add("dup", "똑같은  글 내용입니다 ")
        self.add("long", "가" * 501)
        self.add("ad", "이 제품 좋아요", affiliate=True, reply_text="https://example.com/p")
        later = AT + timedelta(hours=3)
        results = [self.run_at(at=later + timedelta(hours=3 * i)) for i in range(3)]
        reasons = {r.draft_id: r.detail for r in results}
        self.assertEqual(reasons["dup"], "duplicate_text")
        self.assertTrue(reasons["long"].startswith("text_too_long"))
        self.assertEqual(reasons["ad"], "affiliate_disclosure_missing")
        self.assertTrue(all(r.status == "rejected" for r in results))

    def test_affiliate_with_disclosure_posts_link_as_reply(self):
        reply = "https://example.com/p\n이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
        self.add("ad", "이 제품 좋아요", affiliate=True, reply_text=reply)
        publisher = DryRunPublisher()
        outcome = self.run_at(publisher)
        self.assertEqual(outcome.status, "published")
        self.assertEqual(publisher.calls, [("이 제품 좋아요", None), (reply, "dry-run-1")])
        statuses = [r["status"] for r in read_history(self.history)]
        self.assertEqual(statuses, ["published", "reply_published"])

    def test_transient_errors_retry_then_fail(self):
        self.add("a")
        publisher = FlakyPublisher([TransientError("HTTP 500")] * 3)
        limits = Limits(max_attempts=3)
        self.assertEqual(self.run_at(publisher, limits=limits).status, "retry")
        self.assertEqual(self.run_at(publisher, limits=limits).status, "retry")
        self.assertEqual(self.run_at(publisher, limits=limits).status, "failed")
        self.assertEqual(self.run_at(publisher, limits=limits).status, "idle")

    def test_permanent_error_fails_without_retry(self):
        self.add("a")
        self.assertEqual(self.run_at(FlakyPublisher([PermanentError("HTTP 400")])).status, "failed")
        self.assertEqual(self.run_at().status, "idle")

    def test_ambiguous_publish_step_is_never_retried(self):
        self.add("a")
        error = TransientError("timeout", stage="publish")
        self.assertEqual(self.run_at(FlakyPublisher([error])).status, "uncertain")
        publisher = DryRunPublisher()
        self.assertEqual(self.run_at(publisher).status, "idle")
        self.assertEqual(publisher.calls, [])

    def test_auth_error_keeps_draft_eligible(self):
        self.add("a")
        self.assertEqual(self.run_at(FlakyPublisher([AuthError("HTTP 401")])).status, "auth_error")
        self.assertFalse(self.history.exists())
        self.assertEqual(self.run_at().status, "published")

    def test_reply_failure_does_not_repost_main(self):
        self.add("a", reply_text="댓글 링크")
        publisher = FlakyPublisher([])
        original = publisher.publish

        def fail_reply(text, reply_to_id=None):
            if reply_to_id:
                raise TransientError("HTTP 500")
            return original(text, reply_to_id)

        publisher.publish = fail_reply
        outcome = self.run_at(publisher)
        self.assertEqual(outcome.status, "published")
        self.assertIn("reply failed", outcome.detail)
        self.assertEqual(self.run_at(at=AT + timedelta(hours=3)).status, "idle")

    def test_low_quota_waits(self):
        self.add("a")

        class LowQuota(DryRunPublisher):
            def quota(self):
                return Quota(used=245, total=250)

        outcome = self.run_at(LowQuota())
        self.assertEqual(outcome.status, "waiting")
        self.assertFalse(self.history.exists())

    def test_invalid_queue_files_are_reported_not_published(self):
        (self.queue / "bad.json").write_text('{"text": "시간대 없음", "scheduled_at": "2026-11-20T09:00:00"}')
        (self.queue / "broken.json").write_text("{not json")
        drafts, errors = load_queue(self.queue)
        self.assertEqual(drafts, [])
        self.assertEqual(set(errors), {"bad", "broken"})
        self.assertEqual(self.run_at().status, "idle")


class ThreadsPublisherTests(unittest.TestCase):
    def make(self, responses):
        calls = []

        def transport(method, url, params):
            calls.append((method, url, dict(params)))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        return ThreadsPublisher("123", SECRET, transport=transport, sleep=lambda _: None), calls

    def test_container_status_then_publish(self):
        publisher, calls = self.make([{"id": "c1"}, {"status": "IN_PROGRESS"}, {"status": "FINISHED"}, {"id": "p1"}])
        self.assertEqual(publisher.publish("본문", reply_to_id="parent"), "p1")
        self.assertEqual([c[0] for c in calls], ["POST", "GET", "GET", "POST"])
        self.assertTrue(calls[0][1].endswith("/123/threads"))
        self.assertEqual(calls[0][2]["media_type"], "TEXT")
        self.assertEqual(calls[0][2]["reply_to_id"], "parent")
        self.assertTrue(calls[3][1].endswith("/123/threads_publish"))
        self.assertEqual(calls[3][2]["creation_id"], "c1")

    def test_failed_container_and_publish_stage(self):
        publisher, _ = self.make([{"id": "c1"}, {"status": "ERROR"}])
        with self.assertRaises(PermanentError):
            publisher.publish("본문")
        publisher, _ = self.make([{"id": "c1"}, {"status": "FINISHED"}, TransientError("timeout")])
        with self.assertRaises(TransientError) as caught:
            publisher.publish("본문")
        self.assertEqual(caught.exception.stage, "publish")

    def test_quota_unknown_shape_is_none(self):
        publisher, _ = self.make([{"data": [{"quota_usage": 3, "config": {"quota_total": 250}}]}])
        self.assertEqual(publisher.quota(), Quota(3, 250))
        publisher, _ = self.make([{"unexpected": True}])
        self.assertIsNone(publisher.quota())

    def test_http_error_classification(self):
        self.assertIsInstance(classify_http_error(400, {"error": {"code": 190}}), AuthError)
        self.assertIsInstance(classify_http_error(403, {}), AuthError)
        self.assertIsInstance(classify_http_error(429, {}), TransientError)
        self.assertIsInstance(classify_http_error(503, {}), TransientError)
        self.assertIsInstance(classify_http_error(400, {"error": {"message": "bad"}}), PermanentError)

    def test_missing_credentials(self):
        with self.assertRaises(AuthError):
            ThreadsPublisher("", "")


class CliTests(unittest.TestCase):
    def test_dry_run_cli_and_live_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue"
            queue.mkdir()
            (queue / "a.json").write_text(json.dumps({"text": "hello", "scheduled_at": AT.isoformat()}))
            history = Path(tmp) / "h.jsonl"
            common = ["--queue", str(queue), "--history", str(history), "--at", AT.isoformat()]
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(publish_cli.main(["check", *common]), 0)
                self.assertEqual(publish_cli.main(["run", *common]), 0)
                self.assertEqual(read_history(history)[0]["status"], "published")
                self.assertEqual(publish_cli.main(["run", "--mode", "live", *common]), 2)

    def test_secret_never_written_to_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue"
            queue.mkdir()
            (queue / "a.json").write_text(json.dumps({"text": "hello", "scheduled_at": AT.isoformat()}))
            history = Path(tmp) / "h.jsonl"

            def transport(method, url, params):
                raise classify_http_error(400, {"error": {"message": "rejected"}})

            publisher = ThreadsPublisher("1", SECRET, transport=transport)
            run_once(queue, history, publisher, AT)
            self.assertNotIn(SECRET, history.read_text())


if __name__ == "__main__":
    unittest.main()


class SlotTests(unittest.TestCase):
    def setUp(self):
        from threads_bot.schedule import DailySlots
        self.slots = DailySlots()

    def test_slots_respect_window_gap_and_are_stable(self):
        from datetime import date
        for offset in range(400):
            day = date(2026, 1, 1) + timedelta(days=offset)
            slots = self.slots.slots_for(day)
            self.assertEqual(len(slots), 3)
            self.assertEqual(slots, self.slots.slots_for(day))
            for slot in slots:
                local = slot.time()
                self.assertGreaterEqual((local.hour, local.minute), (9, 0))
                self.assertLessEqual((local.hour, local.minute), (21, 30))
            for a, b in zip(slots, slots[1:]):
                self.assertGreaterEqual(b - a, timedelta(hours=2))
        days = [self.slots.slots_for(date(2026, 1, 1) + timedelta(days=i))[0] for i in range(10)]
        self.assertGreater(len({d.time() for d in days}), 1)

    def test_window_too_short_is_rejected(self):
        from datetime import time
        from threads_bot.schedule import DailySlots
        with self.assertRaises(ValueError):
            DailySlots(window_start=time(9), window_end=time(12))

    def test_loop_publishes_once_per_passed_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue"
            queue.mkdir()
            for i in range(5):
                (queue / f"d{i}.json").write_text(json.dumps(
                    {"text": f"서로 다른 글 번호 {i} " + "가나다라마바사"[i] * 10, "scheduled_at": "2026-11-01T00:00:00+09:00"},
                    ensure_ascii=False))
            history = Path(tmp) / "h.jsonl"
            day = datetime(2026, 11, 20, tzinfo=KST).date()
            slot_times = self.slots.slots_for(day)
            limits = Limits(min_interval=timedelta(minutes=110))

            def run(at):
                return run_once(queue, history, DryRunPublisher(), at, limits, slots=self.slots)

            early = run(datetime(2026, 11, 20, 8, 50, tzinfo=KST))
            self.assertEqual((early.status, early.detail), ("waiting", "outside_window"))
            before = run(slot_times[0] - timedelta(minutes=1))
            self.assertEqual(before.status, "waiting")
            self.assertTrue(before.detail.startswith("next_slot"))
            self.assertEqual(run(slot_times[0] + timedelta(minutes=10)).status, "published")
            self.assertEqual(run(slot_times[0] + timedelta(minutes=40)).status, "waiting")
            for slot in slot_times[1:]:
                self.assertEqual(run(slot + timedelta(minutes=10)).status, "published")
            late = run(datetime(2026, 11, 20, 22, 10, tzinfo=KST))
            self.assertEqual((late.status, late.detail), ("waiting", "outside_window"))
            next_day = self.slots.slots_for(day + timedelta(days=1))[0]
            self.assertEqual(run(next_day + timedelta(minutes=5)).status, "published")


class CoupangDraftTests(unittest.TestCase):
    def test_coupang_url_builds_body_notice_and_comment(self):
        from threads_bot.drafts import COUPANG_BODY_NOTICE, COUPANG_DISCLOSURE, parse_draft
        draft = parse_draft("c1", {
            "text": "자취 3년차가 진짜 잘 쓰는 물건",
            "scheduled_at": AT.isoformat(),
            "reply_text": "제가 쓰는 제품은 여기 있어요",
            "coupang_url": "https://link.coupang.com/a/abc123",
        })
        self.assertTrue(draft.affiliate)
        self.assertTrue(draft.text.endswith(COUPANG_BODY_NOTICE))
        self.assertEqual(
            draft.reply_text,
            f"제가 쓰는 제품은 여기 있어요\n\nhttps://link.coupang.com/a/abc123\n\n{COUPANG_DISCLOSURE}",
        )

    def test_coupang_url_without_intro_and_validation(self):
        from threads_bot.drafts import DraftError, parse_draft
        base = {"text": "본문", "scheduled_at": AT.isoformat()}
        draft = parse_draft("c2", {**base, "coupang_url": "https://coupa.ng/xyz"})
        self.assertTrue(draft.reply_text.startswith("https://coupa.ng/xyz\n\n"))
        for bad in ("http://link.coupang.com/a/x", "https://www.coupang.com/vp/products/1",
                    "https://link.coupang.com/", "https://evil.example/link.coupang.com", 123):
            with self.assertRaises(DraftError):
                parse_draft("bad", {**base, "coupang_url": bad})

    def test_coupang_draft_publishes_body_then_link_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue"
            queue.mkdir()
            (queue / "c.json").write_text(json.dumps({
                "text": "이거 사고 삶의 질 올라감", "scheduled_at": AT.isoformat(),
                "coupang_url": "https://link.coupang.com/a/abc123",
            }, ensure_ascii=False), encoding="utf-8")
            history = Path(tmp) / "h.jsonl"
            publisher = DryRunPublisher()
            outcome = run_once(queue, history, publisher, AT)
            self.assertEqual(outcome.status, "published")
            (body, parent), (comment, reply_to) = publisher.calls
            self.assertIsNone(parent)
            self.assertNotIn("link.coupang.com", body)
            self.assertEqual(reply_to, "dry-run-1")
            self.assertIn("https://link.coupang.com/a/abc123", comment)
            records = read_history(history)
            self.assertEqual([r["status"] for r in records], ["published", "reply_published"])
            self.assertTrue(records[0]["affiliate"])
