import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import MessageEntity
from sqlalchemy import text

from bot.brand import BRAND
from bot.core.messenger import DeliveryFailed
from bot.core.users import Users
from bot.locales import TEXTS
from bot.site_check import handlers, replies
from bot.site_check.checks import ChecksRepo
from bot.site_check.handlers import (CheckRunner, IncomingText, Intake, NOT_TEXT, QUEUE_FULL, entity_urls, on_again)
from bot.site_check.limits import LIMIT_GLOBAL, Limits
from bot.site_check.notifier import Notifier
from bot.site_check.pagespeed import (BLOCKED_STATUSES, ERROR_CODES, ERRORED_DOCUMENT, FIRST_SERVER_ERROR,
                                      MEASURE_FAILED, NOT_FOUND_STATUS, LighthouseFailure, classify)
from bot.site_check.pipeline import CERT_BLOCKS, CheckFailed, CheckResult
from bot.site_check.probe import PRIVATE_ADDRESS, SERVICE_DOWN, UNREACHABLE_DNS
from bot.site_check.queue import CheckQueue
from bot.site_check.url_input import BAD_ADDRESS, NOT_A_LINK
from bot.site_check.verdict import judge
from tests.builders import TODAY, page, security
from tests.fakes import ADMIN_ID, FakeClock, FakeMessenger, fake_bot, make_callback, rich_text

USER = 77
FIRST_MESSAGE_ID = 101  # FakeMessenger нумерует с 101


@pytest.fixture(autouse=True)
def _no_real_retry_delay(monkeypatch):
    """Поправка 4: пауза перед второй попыткой — настоящая в проде, в тестах ждать нечего."""
    monkeypatch.setattr(handlers, "RETRY_DELAY_SECONDS", 0)


class FakePipeline:
    def __init__(self):
        self.outcomes: dict[str, object] = {}
        self.gate: asyncio.Event | None = None
        self.calls: list[str] = []

    async def run(self, target):
        self.calls.append(target.host)
        if self.gate:
            await self.gate.wait()
        outcome = self.outcomes.get(target.host) or CheckResult(f"https://{target.host}/", page(), security(),
                                                                 judge(page(), security(), TODAY))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _build_world(messenger, settings, db) -> SimpleNamespace:
    clock, pipeline = FakeClock(), FakePipeline()
    users, repo = Users(db, clock), ChecksRepo(db, clock)
    limits = Limits(repo, clock, settings.user_daily_limit, settings.global_daily_limit, settings.admin_id)
    notifier = Notifier(messenger, settings.admin_id, clock, TEXTS, BRAND)
    runner = CheckRunner(pipeline, repo, limits, messenger, TEXTS, BRAND, notifier, clock)
    queue = CheckQueue(settings.check_workers, settings.queue_max, runner.run, clock)
    intake = Intake(users, repo, limits, queue, messenger, TEXTS, BRAND, settings, notifier)
    return SimpleNamespace(messenger=messenger, pipeline=pipeline, queue=queue, intake=intake, db=db)


@pytest.fixture
async def world(db, settings):
    built = _build_world(FakeMessenger(), settings, db)
    built.queue.start()
    yield built
    if built.pipeline.gate:
        built.pipeline.gate.set()
    await built.queue.stop()


def link(text: str, user_id: int = USER) -> IncomingText:
    return IncomingText(user_id, user_id, "ru", text, [])


async def settle(world) -> None:
    for _ in range(300):
        if world.queue.is_idle():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("очередь не опустела")


async def rows(db, sql: str) -> list:
    async with db.connect() as connection:
        return (await connection.execute(text(sql))).all()


async def test_link_gets_status_then_report_in_the_same_message(world):
    await world.intake.handle_text(link("example.com"))
    await settle(world)
    assert world.messenger.sent[0][1]["blocks"][1]["text"] == ["Проверяю example.com…"]
    chat_id, message_id, report = world.messenger.edited[-1]
    assert (chat_id, message_id) == (USER, FIRST_MESSAGE_ID)
    assert "Сайт в порядке" in world.messenger.last()
    assert await rows(world.db, "SELECT status, charged FROM checks") == [("done", 1)]


async def test_not_a_link_is_explained_and_recorded_without_charge(world):
    await world.intake.handle_text(link("привет"))
    assert "Это не похоже на адрес сайта" in world.messenger.last()
    assert await rows(world.db, "SELECT status, error_code, charged FROM checks") == [("failed", "not_a_link", 0)]


async def test_social_page_offers_discussion(world):
    await world.intake.handle_text(link("https://instagram.com/shop"))
    assert "Это страница на Instagram" in world.messenger.last()


async def test_second_link_during_check_is_refused(world):
    world.pipeline.gate = asyncio.Event()
    await world.intake.handle_text(link("example.com"))
    await world.intake.handle_text(link("example.org"))
    assert "Сначала закончу с example.com" in world.messenger.last()
    world.pipeline.gate.set()
    await settle(world)
    assert world.pipeline.calls == ["example.com"]


async def test_report_is_sent_anew_when_status_message_is_gone(world):
    world.pipeline.gate = asyncio.Event()
    await world.intake.handle_text(link("example.com"))
    world.messenger.gone.add(FIRST_MESSAGE_ID)
    world.pipeline.gate.set()
    await settle(world)
    assert "Сайт в порядке" in rich_text(world.messenger.sent[-1][1])


async def test_blocked_user_does_not_break_worker(world):
    world.pipeline.gate = asyncio.Event()
    await world.intake.handle_text(link("example.com"))
    world.messenger.blocked.add(USER)
    world.pipeline.gate.set()
    await settle(world)
    assert await rows(world.db, "SELECT status FROM checks") == [("done",)]
    await world.intake.handle_text(link("example.org", user_id=78))
    await settle(world)
    assert "Сайт в порядке" in world.messenger.last()


async def test_site_failure_is_explained_and_charged(world):
    world.pipeline.outcomes["example.com"] = CheckFailed("not_found", reached_measurement=True, page_status=404)
    await world.intake.handle_text(link("example.com"))
    await settle(world)
    assert "По этому адресу страницы нет" in world.messenger.last()
    assert await rows(world.db, "SELECT error_code, charged FROM checks") == [("not_found", 1)]


async def test_our_failure_is_not_charged_and_owner_is_notified(world):
    world.pipeline.outcomes["example.com"] = CheckFailed("service_down", reached_measurement=True, reason="quota")
    await world.intake.handle_text(link("example.com"))
    await settle(world)
    assert await rows(world.db, "SELECT error_code, charged FROM checks") == [("service_down", 0)]
    to_owner = [message for chat_id, message in world.messenger.sent if chat_id == ADMIN_ID]
    assert len(to_owner) == 1


async def test_missing_audits_notify_owner_once(world):
    lacking = replace(page(), missing_audits=("target-size",))
    result = CheckResult("https://example.com/", lacking, security(), judge(lacking, security(), TODAY))
    world.pipeline.outcomes["example.com"] = result
    world.pipeline.outcomes["example.org"] = result
    await world.intake.handle_text(link("example.com"))
    await settle(world)
    await world.intake.handle_text(link("example.org", user_id=78))
    await settle(world)
    assert len([chat_id for chat_id, _ in world.messenger.sent if chat_id == ADMIN_ID]) == 1


async def test_user_limit_is_explained(world, settings):
    for _ in range(settings.user_daily_limit):
        await world.intake.handle_text(link("example.com"))
        await settle(world)
    await world.intake.handle_text(link("example.com"))
    assert "Лимит — 10 проверок в сутки" in world.messenger.last()


def test_entity_urls_take_links_and_text_links():
    message_text = "мой сайт example.com и вот ещё"
    entities = [MessageEntity(type="url", offset=9, length=11),
                MessageEntity(type="text_link", offset=24, length=6, url="https://site.org/page")]
    assert entity_urls(message_text, entities) == ["example.com", "https://site.org/page"]


# --- Поправки к задаче 17 (task-17-carries.md) ---


async def test_service_down_before_measurement_only_logs_no_owner_notice(world):
    """Поправка 1: сбой на своей стороне до замера (свой DNS) не выдаётся владельцу за сбой PageSpeed."""
    world.pipeline.outcomes["example.com"] = CheckFailed("service_down", reached_measurement=False, reason="dns")
    await world.intake.handle_text(link("example.com"))
    await settle(world)
    assert [chat_id for chat_id, _ in world.messenger.sent if chat_id == ADMIN_ID] == []
    assert await rows(world.db, "SELECT error_code, charged FROM checks") == [("service_down", 0)]


async def test_broken_report_builder_does_not_leave_checking_forever(world, monkeypatch):
    """Поправка 3: сборка отчёта упала — человек получает measure_failed, а не «Проверяю…» навсегда.

    Раунд ревью 1, находка 3 (ТЗ Л9): сбой на нашей стороне (отчёт не собрался, хотя замер прошёл) не
    списывается — как и другие «наши» сбои (service_down)."""
    def boom(*args, **kwargs):
        raise RuntimeError("отчёт не собрался")

    monkeypatch.setattr(handlers, "build_report", boom)
    await world.intake.handle_text(link("example.com"))
    await settle(world)
    assert "Не получилось измерить" in world.messenger.last()
    assert await rows(world.db, "SELECT status, error_code, charged FROM checks") == [("failed", "measure_failed", 0)]


async def test_final_message_delivery_recovers_after_one_retry(db, settings):
    """Поправка 4: доставка итога проваливается один раз — вторая попытка проходит."""
    inner = FakeMessenger()

    class FlakyOnce:
        def __init__(self, target: FakeMessenger):
            self._target = target
            self._broken_edits_left = 1

        async def send(self, chat_id, rich_message):
            return await self._target.send(chat_id, rich_message)

        async def edit(self, chat_id, message_id, rich_message):
            if self._broken_edits_left > 0:
                self._broken_edits_left -= 1
                raise DeliveryFailed("временный сбой")
            await self._target.edit(chat_id, message_id, rich_message)

    world = _build_world(FlakyOnce(inner), settings, db)
    world.queue.start()
    try:
        await world.intake.handle_text(link("example.com"))
        await settle(world)
    finally:
        await world.queue.stop()
    assert "Сайт в порядке" in rich_text(inner.edited[-1][2])


async def test_new_user_not_text_refusal_is_recorded(world):
    """Поправка 7: touch() отмечает человека раньше записи отказа — иначе внешний ключ уронил бы вставку,
    а best_effort тихо проглотил бы ошибку, не оставив в checks ничего."""
    new_user_id = 555
    await world.intake.handle_not_text(new_user_id, new_user_id, "ru")
    assert await rows(world.db, "SELECT status, error_code FROM checks") == [("failed", "not_text")]


async def test_on_again_survives_a_stale_callback(db):
    """Поправка 5: нажатие устаревшей кнопки не должно ронять обработчик."""
    users, messenger = Users(db, FakeClock()), FakeMessenger()
    stale_answer = TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id="1"), message="query is too old")
    bot = fake_bot({"answerCallbackQuery": stale_answer})
    callback = make_callback("again", user_id=USER).as_(bot)
    await on_again(callback, users=users, messenger=messenger, texts=TEXTS, brand=BRAND)
    assert "Пришлите ссылку на сайт" in messenger.last()


async def test_reservation_is_released_when_building_status_fails(world, monkeypatch):
    """Поправка 9: любой сбой между reserve и удачным submit освобождает место, ошибка идёт дальше."""
    def boom(*args, **kwargs):
        raise RuntimeError("статус не собрался")

    monkeypatch.setattr(replies, "checking", boom)
    with pytest.raises(RuntimeError):
        await world.intake.handle_text(link("example.com"))
    assert world.queue.busy_display(USER) is None


async def test_queue_overflow_with_delivery_failure_releases_reservation_once(world, monkeypatch):
    """Раунд ревью 1, находка 1: переполнение очереди — release только один. В брифе `_queue_overflow`
    освобождала место сама, а `edit_or_send` мог бросить DeliveryFailed до `finish_failed` — запись оставалась
    «queued», исключение уходило в `_enqueue`, и там срабатывал второй release. Между двумя release есть await:
    если за это время тот же человек забронировал бы место заново, второй release стёр бы уже чужую, свежую
    бронь (ТЗ Л2 — две проверки разом). Теперь release — один раз, в `_enqueue`, после того как `_start`
    вернёт, встала ли работа в очередь.

    Переполнение задаём напрямую (подменяем `submit`/`is_full` очереди), а не гоняясь за настоящей ёмкостью:
    `_refusal` сама отказывает раньше `_enqueue`, если очередь уже полна — нужен именно случай, когда полна
    она стала между этой проверкой и вызовом `submit`."""
    overflow_user = 3

    async def failing_edit(chat_id, message_id, rich_message):
        raise DeliveryFailed("правка недоступна")

    def submit_finds_it_full(job):
        raise asyncio.QueueFull()

    monkeypatch.setattr(world.messenger, "edit", failing_edit)
    monkeypatch.setattr(world.queue, "is_full", lambda: False)
    monkeypatch.setattr(world.queue, "submit", submit_finds_it_full)
    release_calls: list[int] = []
    original_release = world.queue.release

    def spy_release(user_id: int) -> None:
        release_calls.append(user_id)
        original_release(user_id)

    monkeypatch.setattr(world.queue, "release", spy_release)

    await world.intake.handle_text(link("example.net", user_id=overflow_user))

    assert release_calls.count(overflow_user) == 1
    assert world.queue.busy_display(overflow_user) is None
    assert await rows(world.db, f"SELECT status, error_code, charged FROM checks WHERE user_id = {overflow_user}") \
        == [("failed", "queue_full", 0)]

    world.queue.reserve(overflow_user, "новая бронь")  # свежая бронь не пострадала от лишнего release
    assert world.queue.busy_display(overflow_user) == "новая бронь"


# Поправка 6: список кодов исходов — из констант самих модулей, не переписан строками заново.
_CLASSIFY_CODES = set(ERROR_CODES.values()) - {CERT_BLOCKS}
_PAGE_STATUS_CODES = {classify(LighthouseFailure(ERRORED_DOCUMENT, status))
                      for status in (*BLOCKED_STATUSES, NOT_FOUND_STATUS, FIRST_SERVER_ERROR)}
_PROBE_CODES = {UNREACHABLE_DNS, PRIVATE_ADDRESS, SERVICE_DOWN}
_URL_INPUT_CODES = {NOT_A_LINK, BAD_ADDRESS}
_INTAKE_CODES = {NOT_TEXT, QUEUE_FULL, LIMIT_GLOBAL}
PRODUCIBLE_FAILURE_CODES = sorted(_CLASSIFY_CODES | _PAGE_STATUS_CODES | _PROBE_CODES | _URL_INPUT_CODES
                                  | _INTAKE_CODES | {MEASURE_FAILED})


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("code", PRODUCIBLE_FAILURE_CODES)
def test_every_producible_failure_code_has_a_reply(code, lang):
    # Раунд ревью 1, находка 2: без этой строки тест не мог упасть — failure() тихо подменяет неизвестный код
    # на measure_failed, и "{" not in text была бы верна для любого кода, даже для не заведённого текста.
    assert code in replies.FAILURE_CODES
    message_text = rich_text(replies.failure(TEXTS, lang, BRAND, code, status=FIRST_SERVER_ERROR, site="example.com"))
    assert "{" not in message_text


def test_server_error_reply_shows_the_status_code():
    message_text = rich_text(replies.failure(TEXTS, "ru", BRAND, "server_error", status=503, site="example.com"))
    assert "503" in message_text
