from datetime import datetime

from .models import METRIC_WEIGHTS, Observation, RankedPost


def deduplicate(observations: list[Observation]) -> list[Observation]:
    """Use one whole latest snapshot per ID; never add counts across results."""
    unique: dict[str, Observation] = {}
    for item in observations:
        previous = unique.get(item.post.id)
        if previous is None or item.observed_at > previous.observed_at:
            unique[item.post.id] = item
    return list(unique.values())


def scores(item: Observation, at: datetime) -> tuple[float | None, float | None]:
    # Partial weighted sums would unfairly compare posts with different coverage.
    if any(item.metrics.get(name) is None for name in METRIC_WEIGHTS):
        return None, None
    engagement = float(sum(item.metrics[name] * weight for name, weight in METRIC_WEIGHTS.items()))
    published = item.post.published_at
    if published is None or published > at:
        return engagement, None
    age_hours = (at - published).total_seconds() / 3600
    return engagement, engagement / (age_hours + 2) ** 0.7


def rank(items: list[Observation], at: datetime, limit: int = 10) -> dict[str, list[RankedPost]]:
    rankings: dict[str, list[RankedPost]] = {"popular": [], "rising": []}
    for item in items:
        for kind, score in zip(rankings, scores(item, at)):
            if score is not None:
                rankings[kind].append(RankedPost(item, score))
    for kind in rankings:
        rankings[kind] = sorted(
            rankings[kind], key=lambda row: (-row.score, row.observation.post.id)
        )[:limit]
    return rankings
