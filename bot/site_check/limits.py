"""Лимиты (ТЗ, Л7–Л10): проверки за скользящие 24 часа на человека и на всех; владельцу — без лимита.

Считает база. Если она недоступна — счётчик в памяти (ТЗ, Р13): теряется точность, а не проверка.
"""
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

from bot.core.clock import Clock
from bot.site_check.checks import ChecksRepo

WINDOW = timedelta(hours=24)
SECONDS_IN_HOUR = 3600
MIN_HOURS_LEFT = 1
LIMIT_USER = "limit_user"
LIMIT_GLOBAL = "limit_global"


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    code: str | None = None
    hours_left: int | None = None


ALLOWED = LimitDecision(True)


class Limits:
    def __init__(self, repo: ChecksRepo, clock: Clock, user_daily: int, global_daily: int, admin_id: int):
        self._repo = repo
        self._clock = clock
        self._user_daily = user_daily
        self._global_daily = global_daily
        self._admin_id = admin_id
        self._memory: deque[tuple[datetime, int]] = deque()

    def remember_charge(self, user_id: int) -> None:
        # Поправка 8 к задаче 17: та же обрезка, что и в _memory_counts — иначе при исправной базе, где
        # _memory_counts не вызывается, deque рос бы всю жизнь процесса.
        now = self._clock.now()
        self._forget_before(now - WINDOW)
        self._memory.append((now, user_id))

    async def decide(self, user_id: int) -> LimitDecision:
        if user_id == self._admin_id:
            return ALLOWED
        since = self._clock.now() - WINDOW
        mine, total, oldest = await self._counts(user_id, since)
        if mine >= self._user_daily:
            return LimitDecision(False, LIMIT_USER, self._hours_until(oldest))
        if total >= self._global_daily:
            return LimitDecision(False, LIMIT_GLOBAL)
        return ALLOWED

    async def _counts(self, user_id: int, since: datetime) -> tuple[int, int, datetime | None]:
        try:
            mine = await self._repo.count_charged_since(since, user_id)
            total = await self._repo.count_charged_since(since)
            return mine, total, await self._repo.oldest_charged_since(since, user_id)
        except Exception:  # noqa: BLE001 — база недоступна: лимиты по памяти (ТЗ, Л10)
            logger.warning("лимиты считаются в памяти: база недоступна")
            return self._memory_counts(user_id, since)

    def _memory_counts(self, user_id: int, since: datetime) -> tuple[int, int, datetime | None]:
        self._forget_before(since)
        mine = [moment for moment, owner in self._memory if owner == user_id]
        return len(mine), len(self._memory), (mine[0] if mine else None)

    def _forget_before(self, since: datetime) -> None:
        while self._memory and self._memory[0][0] < since:
            self._memory.popleft()

    def _hours_until(self, oldest: datetime | None) -> int:
        if oldest is None:
            return MIN_HOURS_LEFT
        seconds = (oldest + WINDOW - self._clock.now()).total_seconds()
        return max(MIN_HOURS_LEFT, math.ceil(seconds / SECONDS_IN_HOUR))
