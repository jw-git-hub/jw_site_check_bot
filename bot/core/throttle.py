"""Ограничитель частоты на человека (перенесено из загрузчика, без эмодзи).

Сообщения и кнопки — разные экземпляры со своими интервалами: кнопки жмут чаще, чем шлют ссылки.
Нажатие кнопки отвечается всегда, иначе у человека до 30 секунд висит крутилка.

Уведомление о сообщении — само rich-сообщение с шапкой (ТЗ, 7.1), поэтому его шлёт не event.answer(),
а send_notice — отправка через мессенджер, которую собирает bot/app.py (задача 19).
"""
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.core.messenger import DeliveryFailed


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, interval: float, notice: Callable[[str | None], str],
                send_notice: Callable[[int, str], Awaitable[object]], clock: Callable[[], float] = time.monotonic):
        self._interval = interval
        self._notice = notice
        self._send_notice = send_notice
        self._clock = clock
        self._last_event: dict[int, float] = {}
        self._last_notice: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        now = self._clock()
        if self._too_soon(self._last_event, user.id, now):
            with contextlib.suppress(TelegramAPIError, DeliveryFailed):
                await self._notify(event, user.id, user.language_code, now)
            return None
        self._last_event[user.id] = now
        self._forget_old(now)
        return await handler(event, data)

    def _too_soon(self, moments: dict[int, float], user_id: int, now: float) -> bool:
        last = moments.get(user_id)
        return last is not None and now - last < self._interval

    async def _notify(self, event: TelegramObject, user_id: int, language_code: str | None, now: float) -> None:
        if self._too_soon(self._last_notice, user_id, now):
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        self._last_notice[user_id] = now
        text = self._notice(language_code)
        if isinstance(event, CallbackQuery):
            await event.answer(text)
        elif isinstance(event, Message):
            await self._send_notice(event.chat.id, text)

    def _forget_old(self, now: float) -> None:
        for moments in (self._last_event, self._last_notice):
            for user_id in [uid for uid, moment in moments.items() if now - moment > self._interval]:
                del moments[user_id]
