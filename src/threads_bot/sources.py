"""Replace these protocols with approved network adapters after Phase 0."""

from datetime import datetime, timedelta
from typing import Iterable, Protocol

from .models import Observation, Post, Trend


class TrendSource(Protocol):
    def discover(self, at: datetime) -> Iterable[Trend]: ...


class PostSource(Protocol):
    def search(self, trends: list[Trend], at: datetime) -> Iterable[Observation]: ...


class DemoTrendSource:
    def discover(self, at: datetime) -> Iterable[Trend]:
        return [Trend(keyword, "synthetic") for keyword in ("기술", "문화", "생활")]


class DemoPostSource:
    """Deterministic fictional data; never requests Threads or posts content."""

    def search(self, trends: list[Trend], at: datetime) -> Iterable[Observation]:
        for index in range(1, 25):
            keyword = trends[(index - 1) % len(trends)].keyword
            metrics: dict[str, int | None] = {
                "likes": index * 13,
                "replies": index % 7,
                "reposts": index % 5,
                "quotes": index % 3,
            }
            reasons = {}
            if index % 8 == 0:
                metrics["quotes"] = None
                reasons["quotes"] = "demo_missing_field"
            post = Post(
                id=f"demo-{index:03}",
                text=f"{keyword}, 오늘의 작은 발견!\n어떤 경험이 있으신가요?",
                author=f"fictional-{index}",
                url=None,
                published_at=at - timedelta(hours=index % 12 + 1),
                media_count=index % 3,
            )
            observation = Observation(post, at, metrics, reasons)
            yield observation
            if index == 1:
                yield observation  # A duplicate across search results.
