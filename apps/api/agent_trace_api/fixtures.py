from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random


@dataclass
class FixtureClock:
    """A deterministic, explicitly advanced clock for fixtures and runtime tests."""

    current: datetime

    @classmethod
    def epoch(cls) -> "FixtureClock":
        return cls(datetime(2026, 1, 15, 9, 30, tzinfo=UTC))

    def now(self) -> datetime:
        return self.current

    def advance(self, *, milliseconds: int) -> datetime:
        self.current += timedelta(milliseconds=milliseconds)
        return self.current


def fixture_random(seed: int) -> Random:
    return Random(seed)
