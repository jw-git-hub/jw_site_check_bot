import json

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Chat, Message

import pytest

from bot.core.messenger import (EMPTY_KEYBOARD, AiogramMessenger, DeliveryFailed, EditRichDict, MessageGone,
                                SendRichDict, edit_or_send)
from tests.fakes import FAKE_NOW, FakeMessenger, fake_bot

DIVIDER_ONLY = {"blocks": [{"type": "divider"}]}


def sent_message(message_id: int) -> Message:
    return Message(message_id=message_id, date=FAKE_NOW, chat=Chat(id=1, type="private"))


def edit_error(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=EditRichDict(chat_id=1, message_id=1, rich_message={}), message=text)


KEYBOARD = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}


async def test_send_passes_dict_and_returns_message_id():
    bot = fake_bot({"sendRichMessage": sent_message(7)})
    assert await AiogramMessenger(bot).send(1, DIVIDER_ONLY) == 7
    call = bot.session.calls[0]
    assert isinstance(call, SendRichDict)
    assert call.rich_message == DIVIDER_ONLY
    assert call.reply_markup is None


async def test_send_passes_reply_markup_alongside_rich_message():
    bot = fake_bot({"sendRichMessage": sent_message(7)})
    await AiogramMessenger(bot).send(1, DIVIDER_ONLY, KEYBOARD)
    call = bot.session.calls[0]
    assert call.rich_message == DIVIDER_ONLY
    assert call.reply_markup == KEYBOARD


async def test_edit_passes_reply_markup_alongside_rich_message():
    bot = fake_bot({"editMessageText": sent_message(7)})
    await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY, KEYBOARD)
    call = bot.session.calls[0]
    assert isinstance(call, EditRichDict)
    assert call.rich_message == DIVIDER_ONLY
    assert call.reply_markup == KEYBOARD


def test_rich_dict_is_serialized_as_json():
    bot = fake_bot()
    assert json.loads(bot.session.prepare_value(DIVIDER_ONLY, bot=bot, files={})) == DIVIDER_ONLY


def test_empty_keyboard_is_not_dropped_as_falsy_by_aiogram():
    """`{"inline_keyboard": []}` — валидная явная «без кнопок», а не «пусто»: prepare_value фильтрует по
    `is not None`, а не по правдивости значения, так что пустой список внутри словаря не пропадает (задача 23a,
    правка 1 — иначе editMessageText без reply_markup оставил бы прежнюю клавиатуру висеть под новым текстом)."""
    bot = fake_bot()
    prepared = bot.session.prepare_value(EMPTY_KEYBOARD, bot=bot, files={})
    assert json.loads(prepared) == EMPTY_KEYBOARD


async def test_edit_not_modified_is_quiet():
    bot = fake_bot({"editMessageText": edit_error("Bad Request: message is not modified")})
    await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY)
    assert [call.__api_method__ for call in bot.session.calls] == ["editMessageText"]


async def test_edit_of_deleted_message_raises_gone():
    bot = fake_bot({"editMessageText": edit_error("Bad Request: message to edit not found")})
    with pytest.raises(MessageGone):
        await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY)


@pytest.mark.parametrize("description", [
    "Bad Request: message can't be edited",
    "Bad Request: MESSAGE_ID_INVALID",
    "Bad Request: something Telegram has not said before",
])
async def test_any_other_bad_request_on_edit_also_raises_gone(description):
    """Тексты правки rich-сообщения неизвестны, Telegram переформулирует ошибки — список фраз не годится (ТЗ, 7.6)."""
    bot = fake_bot({"editMessageText": edit_error(description)})
    with pytest.raises(MessageGone):
        await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY)


async def test_blocked_user_becomes_delivery_failed():
    method = SendRichDict(chat_id=1, rich_message={})
    bot = fake_bot({"sendRichMessage": TelegramForbiddenError(method=method, message="Forbidden: bot was blocked")})
    with pytest.raises(DeliveryFailed):
        await AiogramMessenger(bot).send(1, DIVIDER_ONLY)


async def test_edit_forbidden_becomes_delivery_failed():
    method = EditRichDict(chat_id=1, message_id=5, rich_message={})
    bot = fake_bot({"editMessageText": TelegramForbiddenError(method=method, message="Forbidden: bot was blocked")})
    with pytest.raises(DeliveryFailed):
        await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY)


async def test_edit_or_send_sends_new_message_when_old_is_gone():
    messenger = FakeMessenger()
    messenger.gone.add(5)
    new_id = await edit_or_send(messenger, 1, 5, DIVIDER_ONLY, KEYBOARD)
    assert new_id != 5
    assert messenger.sent == [(1, DIVIDER_ONLY, KEYBOARD)]


async def test_edit_or_send_sends_new_message_for_unknown_bad_request():
    bot = fake_bot({"editMessageText": edit_error("Bad Request: something Telegram has not said before"),
                    "sendRichMessage": sent_message(9)})
    new_id = await edit_or_send(AiogramMessenger(bot), 1, 5, DIVIDER_ONLY)
    assert new_id == 9
    assert [call.__api_method__ for call in bot.session.calls] == ["editMessageText", "sendRichMessage"]


async def test_edit_or_send_clears_a_keyboard_left_from_before_when_none_is_given():
    """Telegram у editMessageText не убирает прежнюю клавиатуру сам, если reply_markup не передан (в отличие от
    отправки нового сообщения, где кнопок просто не будет) — иначе, например, после выбора языка кнопки
    «Русский»/«English» остались бы висеть под подтверждением (задача 23a, правка 1). Клавиатура здесь поэтому
    всегда явная — своя есть, нет — пустая; проверено в одном месте, а не по одному разу на каждой правке."""
    messenger = FakeMessenger()
    await edit_or_send(messenger, 1, 5, DIVIDER_ONLY)
    assert messenger.edited == [(1, 5, DIVIDER_ONLY, EMPTY_KEYBOARD)]


async def test_edit_or_send_fallback_send_also_gets_an_explicit_empty_keyboard():
    """Правка не прошла (сообщение удалено) — новое сообщение уходит с той же явной пустой клавиатурой, а не
    без reply_markup вовсе, чтобы поведение не расходилось между веткой правки и веткой отправки заново."""
    messenger = FakeMessenger()
    messenger.gone.add(5)
    await edit_or_send(messenger, 1, 5, DIVIDER_ONLY)
    assert messenger.sent == [(1, DIVIDER_ONLY, EMPTY_KEYBOARD)]
