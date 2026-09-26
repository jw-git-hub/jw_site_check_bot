"""Сторож зависания (ТЗ, 13.3): цикл событий или опрос Telegram молчат 120 секунд — выход, Docker поднимет заново.

Docker без Swarm не перезапускает контейнер в статусе unhealthy, поэтому сторож — внутри бота. Пульс опроса ставится
на каждую попытку getUpdates, даже неудачную: без интернета цикл жив, и перезапуск бы не помог. Самый долгий честный
промежуток между попытками в aiogram 3.31 — 70 секунд запроса и 5 секунд паузы после сбоя: предел 120 секунд его
не задевает. Пока всё живо, сторож обновляет файл /tmp/alive — по нему Docker показывает здоровье контейнера.
"""
import asyncio
import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import GetUpdates
from loguru import logger

EVENT_LOOP = "цикл событий"
POLLING = "опрос Telegram"
SILENCE_LIMIT_SECONDS = 120
CHECK_EVERY_SECONDS = 10
PULSE_EVERY_SECONDS = 10
WATCHDOG_EXIT_CODE = 1
ALIVE_FILE = Path("/tmp/alive")


class Heartbeat:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._beats: dict[str, float] = {}
        self._lock = threading.Lock()

    def beat(self, name: str) -> None:
        with self._lock:
            self._beats[name] = self._clock()

    def silent(self, names: Iterable[str], limit: float) -> list[str]:
        now = self._clock()
        with self._lock:
            return [name for name in names if now - self._beats.get(name, now) > limit]


class PollingPulse(BaseRequestMiddleware):
    """Отметка на каждую попытку getUpdates — удачную или нет: значит, цикл опроса крутится."""

    def __init__(self, heartbeat: Heartbeat):
        self._heartbeat = heartbeat

    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        finally:
            if isinstance(method, GetUpdates):
                self._heartbeat.beat(POLLING)


async def loop_pulse(heartbeat: Heartbeat) -> None:
    while True:
        heartbeat.beat(EVENT_LOOP)
        await asyncio.sleep(PULSE_EVERY_SECONDS)


def start_watchdog(heartbeat: Heartbeat, *, limit: float = SILENCE_LIMIT_SECONDS, every: float = CHECK_EVERY_SECONDS,
                   exit_process: Callable[[int], object] = os._exit, alive_file: Path = ALIVE_FILE,
                   sleep: Callable[[float], object] = time.sleep) -> threading.Thread:
    def watch() -> None:
        while True:
            sleep(every)
            silent = heartbeat.silent((EVENT_LOOP, POLLING), limit)
            if silent:
                logger.critical("сторож: молчат {} — выхожу, Docker перезапустит", ", ".join(silent))
                exit_process(WATCHDOG_EXIT_CODE)
                return
            alive_file.touch()

    thread = threading.Thread(target=watch, name="сторож", daemon=True)
    thread.start()
    return thread
