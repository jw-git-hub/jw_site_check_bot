"""Режим до запуска (ТЗ, Р4): полностью бот отвечает только владельцу. /start проходит — он записывает метку."""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.core.config import CoreSettings

START_COMMAND = "/start"


def is_open_for(settings: CoreSettings, user_id: int) -> bool:
    return settings.open_to_all or user_id == settings.admin_id


class OpenGate(BaseMiddleware):
    def __init__(self, settings: CoreSettings, on_closed: Callable[[TelegramObject], Awaitable[None]]):
        self._settings = settings
        self._on_closed = on_closed

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or is_open_for(self._settings, user.id) or _is_start(event):
            return await handler(event, data)
        await self._on_closed(event)
        return None


def _is_start(event: TelegramObject) -> bool:
    return isinstance(event, Message) and _command(event.text) == START_COMMAND


def _command(text: str | None) -> str:
    """Первое слово команды без @имени_бота, как разбирает его aiogram Command."""
    first_word = (text or "").split(maxsplit=1)[0] if text else ""
    return first_word.split("@", 1)[0]
