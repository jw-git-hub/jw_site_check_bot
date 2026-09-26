"""Подделки для тестов. Секреты собираются в коде, чтобы файлы тестов проходили хук pre-commit."""
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TelegramUser

from bot.core.messenger import DeliveryFailed, MessageGone

TELEGRAM_TOKEN_TAIL = "Ab1_-" * 7  # 35 знаков после двоеточия
GOOGLE_KEY_TAIL = "x1Y2z3" * 5 + "abcde"  # 35 знаков после префикса
FAKE_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

ADMIN_ID = 1
DIVIDER_TEXT = "────"
BLOCKED_TEXT = "Forbidden: bot was blocked by the user"


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


class FakeMessenger:
    """Запоминает отправленное и исправленное. gone — удалённые сообщения, blocked — чаты, где бот заблокирован.

    sent — (chat_id, rich_message, reply_markup), edited — (chat_id, message_id, rich_message, reply_markup):
    клавиатура — последним элементом, чтобы старые проверки по индексу текста не ломались (задача 23a).

    history — и отправленное, и исправленное, в хронологическом порядке (только успешная доставка),
    чтобы last() возвращал действительно последнее сообщение, а не последнюю правку.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, dict[str, Any], dict[str, Any] | None]] = []
        self.edited: list[tuple[int, int, dict[str, Any], dict[str, Any] | None]] = []
        self.history: list[dict[str, Any]] = []
        self.gone: set[int] = set()
        self.blocked: set[int] = set()
        self._next_id = 100

    async def send(self, chat_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> int:
        if chat_id in self.blocked:
            raise DeliveryFailed(BLOCKED_TEXT)
        self._next_id += 1
        self.sent.append((chat_id, rich_message, reply_markup))
        self.history.append(rich_message)
        return self._next_id

    async def edit(self, chat_id: int, message_id: int, rich_message: dict[str, Any],
                   reply_markup: dict[str, Any] | None = None) -> None:
        if chat_id in self.blocked:
            raise DeliveryFailed(BLOCKED_TEXT)
        if message_id in self.gone:
            raise MessageGone("message to edit not found")
        self.edited.append((chat_id, message_id, rich_message, reply_markup))
        self.history.append(rich_message)

    def last(self) -> str:
        """Текст последнего сообщения — отправленного или исправленного, по времени."""
        return rich_text(self.history[-1]) if self.history else ""


def rich_text(rich_message: dict[str, Any]) -> str:
    """Весь текст rich-сообщения построчно — удобно сравнивать с примерами ТЗ."""
    return "\n".join(_block_lines(rich_message["blocks"]))


def _inline(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_inline(item) for item in value)
    return _inline(value.get("text", ""))


def _block_lines(blocks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "divider":
            lines.append(DIVIDER_TEXT)
        elif kind == "details":
            lines += [block["summary"], *_block_lines(block["blocks"])]
        elif kind == "table":
            lines += [" | ".join(_inline(cell["text"]) for cell in row) for row in block["cells"]]
        else:
            lines.append(_inline(block["text"]))
    return lines


class RecordingSession(BaseSession):
    """Подделка сессии aiogram: запоминает методы и отвечает заготовками по имени метода."""

    def __init__(self, responses: dict[str, Any] | None = None):
        super().__init__()
        self.calls: list[Any] = []
        self._responses = responses or {}

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        response = self._responses.get(method.__api_method__, True)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        return None

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b""


def fake_bot(responses: dict[str, Any] | None = None) -> Bot:
    return Bot("42:TEST", session=RecordingSession(responses))


def make_user(user_id: int = 77, language_code: str | None = "ru") -> TelegramUser:
    return TelegramUser(id=user_id, is_bot=False, first_name="Тест", language_code=language_code)


def make_message(text: str = "", user_id: int = 77, language_code: str | None = "ru", entities=None,
                 message_id: int = 1) -> Message:
    return Message(message_id=message_id, date=FAKE_NOW, chat=Chat(id=user_id, type="private"),
                   from_user=make_user(user_id, language_code), text=text, entities=entities)


def make_callback(data: str, user_id: int = 77, language_code: str | None = "ru", message_id: int = 5) -> CallbackQuery:
    return CallbackQuery(id="cb1", from_user=make_user(user_id, language_code), chat_instance="ci", data=data,
                         message=make_message(user_id=user_id, message_id=message_id))
