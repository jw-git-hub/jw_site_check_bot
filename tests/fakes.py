"""Подделки для тестов. Секреты собираются в коде, чтобы файлы тестов проходили хук pre-commit."""
from datetime import UTC, datetime, timedelta

TELEGRAM_TOKEN_TAIL = "Ab1_-" * 7  # 35 знаков после двоеточия
GOOGLE_KEY_TAIL = "x1Y2z3" * 5 + "abcde"  # 35 знаков после префикса
FAKE_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def fake_telegram_token() -> str:
    return "1234567890:" + TELEGRAM_TOKEN_TAIL


def fake_google_key() -> str:
    return "AI" + "za" + GOOGLE_KEY_TAIL


class FakeClock:
    def __init__(self, now: datetime | None = None, monotonic: float = 1000.0):
        self._now = now or FAKE_NOW
        self._monotonic = monotonic

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds
