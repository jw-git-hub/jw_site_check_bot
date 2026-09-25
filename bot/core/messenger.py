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
GONE_PHRASES = ("message to edit not found", "message can't be edited", "message_id_invalid")


class SendRichDict(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int
    rich_message: dict[str, Any]


class EditRichDict(TelegramMethod[Message | bool]):
    __returning__ = Message | bool
    __api_method__ = "editMessageText"

    chat_id: int
    message_id: int
    rich_message: dict[str, Any]


class MessageGone(Exception):
    """Сообщение удалено или больше не правится — текст уйдёт новым сообщением."""


class DeliveryFailed(Exception):
    """Telegram не принял сообщение: человек заблокировал бота или другой отказ."""


class Messenger(Protocol):
    async def send(self, chat_id: int, rich_message: dict[str, Any]) -> int: ...

    async def edit(self, chat_id: int, message_id: int, rich_message: dict[str, Any]) -> None: ...


class AiogramMessenger:
    def __init__(self, bot: Bot):
        self._bot = bot

    async def send(self, chat_id: int, rich_message: dict[str, Any]) -> int:
        try:
            sent = await self._bot(SendRichDict(chat_id=chat_id, rich_message=rich_message))
        except TelegramAPIError as error:
            raise DeliveryFailed(str(error)) from None
        return sent.message_id

    async def edit(self, chat_id: int, message_id: int, rich_message: dict[str, Any]) -> None:
        try:
            await self._bot(EditRichDict(chat_id=chat_id, message_id=message_id, rich_message=rich_message))
        except TelegramBadRequest as error:
            _raise_edit_problem(str(error).lower())
        except TelegramAPIError as error:
            raise DeliveryFailed(str(error)) from None


def _raise_edit_problem(description: str) -> None:
    if NOT_MODIFIED in description:
        return
    if any(phrase in description for phrase in GONE_PHRASES):
        raise MessageGone(description)
    raise DeliveryFailed(description)


async def edit_or_send(messenger: Messenger, chat_id: int, message_id: int, rich_message: dict[str, Any]) -> int:
    """Правит сообщение, а если его нет — шлёт новое. Возвращает id сообщения, где теперь текст."""
    try:
        await messenger.edit(chat_id, message_id, rich_message)
        return message_id
    except MessageGone:
        return await messenger.send(chat_id, rich_message)
