import json

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (Animation, BufferedInputFile, Chat, FSInputFile, Message, RichBlockAnimation,
                           RichMessage)

import pytest

from bot.core import rich
from bot.core.banner import BANNER_FILES
from bot.core.messenger import (EMPTY_KEYBOARD, AiogramMessenger, DeliveryFailed, EditRichDict, MessageGone,
                                SendRichDict, edit_or_send)
from tests.fakes import FAKE_NOW, FakeMessenger, fake_bot

DIVIDER_ONLY = {"blocks": [{"type": "divider"}]}
BANNER_MESSAGE = {"blocks": [{"type": "animation", "animation": {"type": "animation", "media": "banner:ru"}},
                             {"type": "paragraph", "text": ["привет"]}]}


def sent_message(message_id: int) -> Message:
    return Message(message_id=message_id, date=FAKE_NOW, chat=Chat(id=1, type="private"))


def sent_message_with_banner(message_id: int, file_id: str) -> Message:
    clip = Animation(file_id=file_id, file_unique_id=file_id, width=1600, height=400, duration=1)
    rich_message = RichMessage(blocks=[RichBlockAnimation(animation=clip)])
    return Message(message_id=message_id, date=FAKE_NOW, chat=Chat(id=1, type="private"), rich_message=rich_message)


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


async def test_message_without_a_banner_block_is_sent_untouched():
    bot = fake_bot({"sendRichMessage": sent_message(7)})
    await AiogramMessenger(bot).send(1, DIVIDER_ONLY)
    assert bot.session.calls[0].rich_message == DIVIDER_ONLY


async def test_send_without_a_cached_file_id_uploads_the_banner_file():
    """Метка полосы без запомненного file_id — на её место встаёт файл для загрузки (задача 23b): само тело
    запроса подделка сессии не строит (`RecordingSession` в обход `build_form_data`, byte-for-byte разбор — в
    `probe_inputfile`, проверено вручную по пакету aiogram), но в вызов метода должен попасть настоящий
    `InputFile`, а не строка-метка."""
    bot = fake_bot({"sendRichMessage": sent_message(7)})
    await AiogramMessenger(bot).send(1, BANNER_MESSAGE)
    media = bot.session.calls[0].rich_message["blocks"][0]["animation"]["media"]
    assert isinstance(media, FSInputFile)
    assert BANNER_FILES["ru"].samefile(media.path)


async def test_send_does_not_mutate_the_callers_rich_message():
    bot = fake_bot({"sendRichMessage": sent_message(7)})
    original = {"blocks": [dict(BANNER_MESSAGE["blocks"][0]), dict(BANNER_MESSAGE["blocks"][1])]}
    await AiogramMessenger(bot).send(1, original)
    assert original["blocks"][0]["animation"]["media"] == "banner:ru"


async def test_send_remembers_file_id_and_reuses_it_on_the_next_send():
    bot = fake_bot({"sendRichMessage": sent_message_with_banner(7, "file123")})
    messenger = AiogramMessenger(bot)
    await messenger.send(1, BANNER_MESSAGE)
    await messenger.send(1, BANNER_MESSAGE)
    media = bot.session.calls[1].rich_message["blocks"][0]["animation"]["media"]
    assert media == "file123"


async def test_edit_substitutes_the_banner_and_remembers_its_file_id_too():
    bot = fake_bot({"editMessageText": sent_message_with_banner(7, "file456")})
    messenger = AiogramMessenger(bot)
    await messenger.edit(1, 5, BANNER_MESSAGE)
    first_media = bot.session.calls[0].rich_message["blocks"][0]["animation"]["media"]
    assert isinstance(first_media, FSInputFile)
    await messenger.edit(1, 5, BANNER_MESSAGE)
    second_media = bot.session.calls[1].rich_message["blocks"][0]["animation"]["media"]
    assert second_media == "file456"


def test_file_inside_rich_message_goes_as_an_attachment():
    bot, files = fake_bot(), {}
    shot = BufferedInputFile(b"\xff\xd8\xff\xd9", "screen.jpg")
    dumped = bot.session.prepare_value({"blocks": [rich.photo(shot)]}, bot=bot, files=files)
    assert list(files.values()) == [shot] and "attach://" in dumped
