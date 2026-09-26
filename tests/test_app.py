import subprocess
import sys
from pathlib import Path

from aiogram.types import Chat, Message, Update

from bot.__main__ import EXIT_CONFIG
from bot.app import _throttle_notice_sender, build_dispatcher, close_interrupted, closed_answer, throttle_notice
from bot.core.users import Users
from bot.site_check.checks import QUEUED, ChecksRepo, NewCheck
from bot.site_check.url_input import parse_input
from tests.fakes import (FAKE_NOW, FakeClock, FakeMessenger, fake_bot, fake_google_key, make_callback, make_message,
                         make_user)

ROOT = Path(__file__).resolve().parents[1]
STRANGER = 500


def test_missing_token_stops_start_without_showing_other_values():
    environ = {"PATH": "/usr/bin:/bin", "PAGESPEED_API_KEY": fake_google_key(), "ADMIN_ID": "1"}
    result = subprocess.run([sys.executable, "-B", "-m", "bot"], cwd=ROOT, env=environ, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == EXIT_CONFIG
    assert "bot_token" in result.stdout
    assert fake_google_key() not in result.stdout + result.stderr


async def test_only_private_chats_reach_the_bot(db, settings):
    clock, messenger = FakeClock(), FakeMessenger()
    dispatcher = build_dispatcher(settings, users=Users(db, clock), repo=ChecksRepo(db, clock), messenger=messenger,
                                  intake=None, clock=clock)
    private = dispatcher.sub_routers[0]
    assert [router.name for router in private.sub_routers] == ["core_commands", "admin", "site_check"]
    in_group = Message(message_id=1, date=FAKE_NOW, chat=Chat(id=-100, type="supergroup"),
                       from_user=make_user(STRANGER), text="example.com")
    await dispatcher.feed_update(fake_bot(), Update(update_id=1, message=in_group))
    assert messenger.sent == []
    in_private = make_message("example.com", user_id=STRANGER)
    await dispatcher.feed_update(fake_bot(), Update(update_id=2, message=in_private))
    assert "Бот скоро откроется" in messenger.last()


async def test_unfinished_checks_are_closed_on_start(db):
    clock = FakeClock()
    await Users(db, clock).touch(77, "ru", "channel")
    repo = ChecksRepo(db, clock)
    await repo.create(NewCheck(77, "channel", parse_input("site.org", []), QUEUED, chat_id=77, message_id=9))
    messenger = FakeMessenger()
    await close_interrupted(repo, messenger)
    assert messenger.edited[0][:2] == (77, 9)
    assert "Проверка прервалась" in messenger.last()


def test_throttle_notice_is_a_rich_text_in_the_telegram_language():
    """Поправка 1 к задаче 19: предупреждение о частоте — тот же текст, что и в locales (rich с шапкой соберёт
    _throttle_notice_sender). Поправка 2: язык — из language_code Telegram (осознанное решение, см. отчёт)."""
    assert "Too fast" in throttle_notice(None)
    assert "Слишком часто" in throttle_notice("ru")


async def test_throttle_notice_sender_sends_rich_message_with_header():
    """Поправка 1 к задаче 19: send_notice для ThrottleMiddleware — rich-сообщение с шапкой, не голый текст."""
    messenger = FakeMessenger()
    send_notice = _throttle_notice_sender(messenger)
    await send_notice(42, "Слишком часто — подождите пару секунд.")
    assert messenger.sent == [(42, messenger.sent[0][1])]
    assert "Слишком часто" in messenger.last()


async def test_throttle_notice_sender_suppresses_delivery_failure():
    """Поправка 1 к задаче 19: заблокировавший бота человек не должен ронять обработчик предупреждением."""
    messenger = FakeMessenger()
    messenger.blocked.add(42)
    send_notice = _throttle_notice_sender(messenger)
    await send_notice(42, "текст")  # не должно бросить DeliveryFailed
    assert messenger.sent == []


async def test_closed_bot_answers_soon_to_strangers(db):
    messenger = FakeMessenger()
    answer = closed_answer(Users(db, FakeClock()), messenger)
    await answer(make_message("example.com", user_id=STRANGER))
    assert "Бот скоро откроется" in messenger.last()
    bot = fake_bot()
    await answer(make_callback("again", user_id=STRANGER).as_(bot))
    assert [call.__api_method__ for call in bot.session.calls] == ["answerCallbackQuery"]
