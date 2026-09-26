"""Очередь проверок (ТЗ, Л1–Л3): воркеров — CHECK_WORKERS, в очереди — до QUEUE_MAX, у человека одна проверка за раз.

Упавшая проверка не останавливает воркер. После перезапуска очередь пустая — незаконченные проверки закрывает
bot/app.py (ТЗ, Л5).
"""
import asyncio
import math
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from bot.core.clock import Clock
from bot.core.i18n import Lang
from bot.site_check.url_input import Target

DEFAULT_DURATION_SECONDS = 40
DURATIONS_KEPT = 20
SECONDS_IN_MINUTE = 60
MIN_MINUTES = 1


@dataclass
class CheckJob:
    check_id: int | None
    user_id: int
    chat_id: int
    message_id: int
    lang: Lang
    source: str
    target: Target
    is_admin: bool
    was_queued: bool


class CheckQueue:
    def __init__(self, workers: int, max_size: int, run: Callable[[CheckJob], Awaitable[None]] | None, clock: Clock):
        self._workers = workers
        self._queue: asyncio.Queue[CheckJob] = asyncio.Queue(maxsize=max_size)
        self._run = run
        self._clock = clock
        self._running = 0
        self._busy: dict[int, str] = {}
        self._durations: deque[float] = deque(maxlen=DURATIONS_KEPT)
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._work(), name=f"проверки-{number}") for number in range(self._workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def reserve(self, user_id: int, display: str) -> None:
        self._busy[user_id] = display

    def release(self, user_id: int) -> None:
        self._busy.pop(user_id, None)

    def busy_display(self, user_id: int) -> str | None:
        return self._busy.get(user_id)

    def is_full(self) -> bool:
        return self._queue.full()

    def is_idle(self) -> bool:
        return self._running == 0 and self._queue.empty()

    def ahead_now(self) -> int:
        """Сколько проверок впереди новой: 0 — свободный воркер возьмёт её сразу."""
        waiting = self._queue.qsize()
        return 0 if self._running + waiting < self._workers else self._running + waiting

    def submit(self, job: CheckJob) -> None:
        self._queue.put_nowait(job)

    def estimate_minutes(self, ahead: int) -> int:
        average = sum(self._durations) / len(self._durations) if self._durations else DEFAULT_DURATION_SECONDS
        rounds = math.ceil(ahead / self._workers)
        return max(MIN_MINUTES, math.ceil(rounds * average / SECONDS_IN_MINUTE))

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            self._running += 1
            started = self._clock.monotonic()
            try:
                await self._run(job)
            except Exception:  # noqa: BLE001 — одна проверка не должна останавливать воркер
                logger.exception("проверка {} упала", job.check_id)
            finally:
                self._running -= 1
                self._durations.append(self._clock.monotonic() - started)
                self.release(job.user_id)
                self._queue.task_done()
