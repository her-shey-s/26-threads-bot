from dataclasses import dataclass
from datetime import datetime


METRIC_WEIGHTS = {"likes": 1, "replies": 3, "reposts": 4, "quotes": 3}


@dataclass(frozen=True)
class Trend:
    keyword: str
    source: str


@dataclass(frozen=True)
class Post:
    id: str
    text: str
    author: str | None
    url: str | None
    published_at: datetime | None
    media_count: int | None


@dataclass(frozen=True)
class Observation:
    post: Post
    observed_at: datetime
    metrics: dict[str, int | None]
    unavailable_reasons: dict[str, str]

    def __post_init__(self) -> None:
        if not self.post.id:
            raise ValueError("Post ID is required")
        timestamps = (self.observed_at, self.post.published_at)
        if any(t is not None and t.utcoffset() is None for t in timestamps):
            raise ValueError("Timestamps must include a timezone")
        for name in METRIC_WEIGHTS:
            value = self.metrics.get(name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"Invalid metric: {name}")
            if value is None and not self.unavailable_reasons.get(name):
                raise ValueError(f"Missing metric needs a reason: {name}")


@dataclass(frozen=True)
class RankedPost:
    observation: Observation
    score: float
