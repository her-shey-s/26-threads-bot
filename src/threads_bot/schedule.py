"""Random daily posting slots.

GitHub Actions cron cannot run at random times, so the workflow wakes up every
30 minutes inside the window and this module decides whether a slot has come.
Slots are random but reproducible for a date: every run on the same day computes
the same slots, so no state is needed besides the publish history.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import random
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DailySlots:
    count: int = 3
    window_start: time = time(9, 0)
    window_end: time = time(22, 0)
    min_gap: timedelta = timedelta(hours=2)
    # The last slot is this much before window_end so the 30-minute cron can still reach it.
    end_margin: timedelta = timedelta(minutes=30)
    timezone: str = "Asia/Seoul"
    seed: str = ""

    def __post_init__(self) -> None:
        span = self._span_minutes()
        needed = (self.count - 1) * self.min_gap.total_seconds() / 60
        if self.count < 1 or span < needed:
            raise ValueError("window is too short for the requested slots and gap")

    def _span_minutes(self) -> int:
        start = datetime.combine(date(2000, 1, 1), self.window_start)
        end = datetime.combine(date(2000, 1, 1), self.window_end) - self.end_margin
        return int((end - start).total_seconds() // 60)

    def slots_for(self, day: date) -> list[datetime]:
        """Sorted slot times for a local date, each pair at least min_gap apart."""
        rng = random.Random(f"{day.isoformat()}|{self.seed}")
        span = self._span_minutes()
        gap = int(self.min_gap.total_seconds() // 60)
        # Uniform over valid layouts: pick points in the span reduced by the gaps, then spread them.
        free = span - gap * (self.count - 1)
        picks = sorted(rng.randint(0, free) for _ in range(self.count))
        zone = ZoneInfo(self.timezone)
        start = datetime.combine(day, self.window_start, tzinfo=zone)
        return [start + timedelta(minutes=p + i * gap) for i, p in enumerate(picks)]

    def local_day(self, at: datetime) -> date:
        return at.astimezone(ZoneInfo(self.timezone)).date()

    def in_window(self, at: datetime) -> bool:
        local = at.astimezone(ZoneInfo(self.timezone)).time()
        return self.window_start <= local < self.window_end

    def passed(self, at: datetime) -> int:
        """How many of today's slots are at or before `at`."""
        return sum(1 for slot in self.slots_for(self.local_day(at)) if slot <= at)
