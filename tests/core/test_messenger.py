import json

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Chat, Message

import pytest

from bot.core.messenger import (AiogramMessenger, DeliveryFailed, EditRichDict, MessageGone, SendRichDict,
                                edit_or_send)
from tests.fakes import FAKE_NOW, FakeMessenger, fake_bot

DIVIDER_ONLY = {"blocks": [{"type": "divider"}]}


def sent_message(message_id: int) -> Message:
    return Message(message_id=message_id, date=FAKE_NOW, chat=Chat(id=1, type="private"))


def edit_error(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=EditRichDict(chat_id=1, message_id=1, rich_message={}), message=text)


async def test_send_passes_dict_and_returns_message_id():
    bot = fake_bot({"sendRichMessage": sent_message(7)})
    assert await AiogramMessenger(bot).send(1, DIVIDER_ONLY) == 7
    call = bot.session.calls[0]
    assert isinstance(call, SendRichDict)
    assert call.rich_message == DIVIDER_ONLY


def test_rich_dict_is_serialized_as_json():
    bot = fake_bot()
    assert json.loads(bot.session.prepare_value(DIVIDER_ONLY, bot=bot, files={})) == DIVIDER_ONLY


async def test_edit_not_modified_is_quiet():
    bot = fake_bot({"editMessageText": edit_error("Bad Request: message is not modified")})
    await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY)
    assert [call.__api_method__ for call in bot.session.calls] == ["editMessageText"]


async def test_edit_of_deleted_message_raises_gone():
    bot = fake_bot({"editMessageText": edit_error("Bad Request: message to edit not found")})
    with pytest.raises(MessageGone):
        await AiogramMessenger(bot).edit(1, 5, DIVIDER_ONLY)


async def test_blocked_user_becomes_delivery_failed():
    method = SendRichDict(chat_id=1, rich_message={})
    bot = fake_bot({"sendRichMessage": TelegramForbiddenError(method=method, message="Forbidden: bot was blocked")})
    with pytest.raises(DeliveryFailed):
        await AiogramMessenger(bot).send(1, DIVIDER_ONLY)


async def test_edit_or_send_sends_new_message_when_old_is_gone():
    messenger = FakeMessenger()
    messenger.gone.add(5)
    new_id = await edit_or_send(messenger, 1, 5, DIVIDER_ONLY)
    assert new_id != 5
    assert messenger.sent == [(1, DIVIDER_ONLY)]
