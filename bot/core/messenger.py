"""Отправка и правка rich-сообщений через aiogram (ТЗ, 7.6).

Свои методы, где rich_message — словарь: типизированные классы aiogram разбирали бы блоки через объединение
моделей pydantic. Остальной код говорит с Telegram через протокол Messenger, в тестах — подделка.

Полоса-анимация шапки (задача 33d): `AiogramMessenger` — единственное место, где метка `rich.header(lang)`
превращается в файл или в запомненный file_id, кэшем `bot.core.banner.BannerCache`.
"""
from typing import Any, Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message

from bot.core.banner import BannerCache

NOT_MODIFIED = "message is not modified"
EMPTY_KEYBOARD: dict[str, Any] = {"inline_keyboard": []}


class SendRichDict(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int
    rich_message: dict[str, Any]
    reply_markup: dict[str, Any] | None = None


class EditRichDict(TelegramMethod[Message | bool]):
    __returning__ = Message | bool
    __api_method__ = "editMessageText"

    chat_id: int
    message_id: int
    rich_message: dict[str, Any]
    reply_markup: dict[str, Any] | None = None


class MessageGone(Exception):
    """Сообщение удалено или больше не правится — текст уйдёт новым сообщением."""


class DeliveryFailed(Exception):
    """Telegram не принял сообщение: человек заблокировал бота или другой отказ."""


class Messenger(Protocol):
    async def send(self, chat_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> int: ...

    async def edit(self, chat_id: int, message_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> None: ...


class AiogramMessenger:
    def __init__(self, bot: Bot, banner: BannerCache | None = None):
        self._bot = bot
        self._banner = banner or BannerCache()

    async def send(self, chat_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> int:
        resolved = self._banner.resolve(rich_message)
        try:
            sent = await self._bot(SendRichDict(chat_id=chat_id, rich_message=resolved,
                                                reply_markup=reply_markup))
        except TelegramAPIError as error:
            raise DeliveryFailed(str(error)) from None
        self._banner.remember(rich_message, sent)
        return sent.message_id

    async def edit(self, chat_id: int, message_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> None:
        resolved = self._banner.resolve(rich_message)
        try:
            edited = await self._bot(EditRichDict(chat_id=chat_id, message_id=message_id, rich_message=resolved,
                                                  reply_markup=reply_markup))
        except TelegramBadRequest as error:
            _raise_edit_problem(str(error).lower())
            return
        except TelegramAPIError as error:
            raise DeliveryFailed(str(error)) from None
        self._banner.remember(rich_message, edited)


def _raise_edit_problem(description: str) -> None:
    """Текст ошибки правки Telegram переформулирует и не документирует для rich-сообщений (ТЗ, 7.6):
    любой Bad Request, кроме «не изменилось», значит — отчёт уйдёт новым сообщением."""
    if NOT_MODIFIED in description:
        return
    raise MessageGone(description)


async def edit_or_send(messenger: Messenger, chat_id: int, message_id: int, rich_message: dict[str, Any],
                       reply_markup: dict[str, Any] | None = None) -> int:
    """Правит сообщение, а если его нет — шлёт новое. Возвращает id сообщения, где теперь текст.

    Telegram у editMessageText не убирает прежнюю клавиатуру сам, если reply_markup не передать (в отличие от
    отправки нового сообщения, где кнопок просто не будет) — иначе, например, кнопки выбора языка остались бы
    висеть под подтверждением. Клавиатура здесь поэтому всегда явная: своя есть — она, нет — пустая. Одно место
    на все правки, а не по одной вставке `{"inline_keyboard": []}` в каждом вызывающем коде."""
    keyboard = reply_markup if reply_markup is not None else EMPTY_KEYBOARD
    try:
        await messenger.edit(chat_id, message_id, rich_message, keyboard)
        return message_id
    except MessageGone:
        return await messenger.send(chat_id, rich_message, keyboard)
