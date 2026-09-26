"""Отправка и правка rich-сообщений через aiogram (ТЗ, 7.6).

Свои методы, где rich_message — словарь: типизированные классы aiogram разбирали бы блоки через объединение
моделей pydantic. Остальной код говорит с Telegram через протокол Messenger, в тестах — подделка.
"""
from typing import Any, Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message

NOT_MODIFIED = "message is not modified"


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
    def __init__(self, bot: Bot):
        self._bot = bot

    async def send(self, chat_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> int:
        try:
            sent = await self._bot(SendRichDict(chat_id=chat_id, rich_message=rich_message,
                                                reply_markup=reply_markup))
        except TelegramAPIError as error:
            raise DeliveryFailed(str(error)) from None
        return sent.message_id

    async def edit(self, chat_id: int, message_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> None:
        try:
            await self._bot(EditRichDict(chat_id=chat_id, message_id=message_id, rich_message=rich_message,
                                         reply_markup=reply_markup))
        except TelegramBadRequest as error:
            _raise_edit_problem(str(error).lower())
        except TelegramAPIError as error:
            raise DeliveryFailed(str(error)) from None


def _raise_edit_problem(description: str) -> None:
    """Текст ошибки правки Telegram переформулирует и не документирует для rich-сообщений (ТЗ, 7.6):
    любой Bad Request, кроме «не изменилось», значит — отчёт уйдёт новым сообщением."""
    if NOT_MODIFIED in description:
        return
    raise MessageGone(description)


async def edit_or_send(messenger: Messenger, chat_id: int, message_id: int, rich_message: dict[str, Any],
                       reply_markup: dict[str, Any] | None = None) -> int:
    """Правит сообщение, а если его нет — шлёт новое. Возвращает id сообщения, где теперь текст."""
    try:
        await messenger.edit(chat_id, message_id, rich_message, reply_markup)
        return message_id
    except MessageGone:
        return await messenger.send(chat_id, rich_message, reply_markup)
