"""Documented WebData Labs dataset adapter and Korean topic discovery."""

from datetime import datetime, timedelta
import os
import re
from xml.etree import ElementTree

from .http_client import request_bytes, request_json
from .models import METRIC_WEIGHTS, Observation, Post


ACTOR = "webdata_labs~threads-scraper"
TREND_FEED = "https://trends.google.com/trending/rss?geo=KR"
# Broad everyday phrases complement news trends, without selecting one topic.
BROAD_QUERIES = ("요즘", "사람", "진짜", "생각", "오늘", "인생", "이유", "나만")
FIELDS = {"likes": "likeCount", "replies": "replyCount", "reposts": "repostCount", "quotes": "quoteCount"}


def timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp requires a timezone")
    return parsed


def parse_trend_feed(data: bytes, limit: int = 12) -> list[str]:
    titles = [item.findtext("title", "").strip() for item in ElementTree.fromstring(data).findall("./channel/item")]
    return list(dict.fromkeys(title for title in titles if title))[:limit]


def discover_queries(*, trend_limit=12) -> list[str]:
    titles = parse_trend_feed(request_bytes(TREND_FEED), trend_limit)
    if not titles:
        raise ValueError("Korean trend feed returned no topics")
    return list(dict.fromkeys([*titles, *BROAD_QUERIES]))[:20]


def collect_apify(queries: list[str], at: datetime, *, hours=48, per_query=20, max_charge=1.0) -> list[dict]:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN is required in secret settings")
    if not queries or len(queries) > 20 or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise ValueError("Provide 1 to 20 nonempty queries")
    if not 1 <= per_query <= 20 or max_charge <= 0:
        raise ValueError("Invalid collection limits")
    # One bounded run; do not retry a paid POST on timeout (it may still be running).
    url = (f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
           f"?timeout=60&maxTotalChargeUsd={max_charge}&clean=true")
    result = request_json(url, token=token, timeout=65, payload={
        "mode": "search", "searchQueries": queries, "maxPosts": per_query,
        "postedAfter": (at - timedelta(hours=hours)).isoformat(),
    })
    if not isinstance(result, list):
        raise ValueError("Apify must return a dataset array")
    return result


def media_count(row: dict) -> int | None:
    media = row.get("media")
    if media is not None and not isinstance(media, list):
        raise ValueError("media must be an array")
    declared = row.get("mediaCount")
    if declared is not None and (type(declared) is not int or declared < 0):
        raise ValueError("Invalid media count")
    kind = str(row.get("mediaType", "")).upper()
    # Any positive evidence wins over a contradictory empty array / TEXT marker.
    if media or declared or kind in {"IMAGE", "VIDEO", "CAROUSEL", "CAROUSEL_ALBUM"}:
        return max(len(media or []), declared or 0, 1)
    if media == [] or declared == 0 or kind in {"TEXT", "TEXT_POST"}:
        return 0
    return None


def normalize(row: dict, at: datetime) -> Observation:
    if not isinstance(row, dict) or row.get("type", "post") != "post":
        raise ValueError("Expected a post row")
    post_id = row.get("postId")
    text = row.get("text")
    if not isinstance(post_id, (str, int)) or isinstance(post_id, bool) or not isinstance(text, str):
        raise ValueError("Post ID and text are required")
    metrics = {name: row.get(field) for name, field in FIELDS.items()}
    reasons = {name: "not_provided_by_source" for name in METRIC_WEIGHTS if metrics[name] is None}
    return Observation(Post(
        str(post_id), text.strip(), row.get("username"), row.get("url"),
        timestamp(row.get("date")), media_count(row),
    ), timestamp(row.get("scrapedAt")) or at, metrics, reasons)


def country_code(row: dict) -> str | None:
    value = row.get("countryCode")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid countryCode")
    return value.strip().upper()


def is_korean(text: str, minimum_ratio=0.35) -> bool:
    letters = [char for char in text if char.isalpha()]
    korean = len(re.findall(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", text))
    return korean >= 4 and bool(letters) and korean / len(letters) >= minimum_ratio
