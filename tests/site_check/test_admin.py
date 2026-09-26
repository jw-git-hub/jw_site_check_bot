import inspect
import re

from aiogram.filters import CommandObject

from bot.brand import BRAND
from bot.core.commands import simple_message
from bot.core.users import Users
from bot.locales import TEXTS
from bot.site_check import admin
from bot.site_check.admin import IsAdmin, on_site, on_stats
from bot.site_check.checks import QUEUED, ChecksRepo, NewCheck
from bot.site_check.notifier import OWNER_LANG
from bot.site_check.pipeline import CheckResult
from bot.site_check.url_input import parse_input
from bot.site_check.verdict import judge
from tests.builders import TODAY, page, security
from tests.fakes import ADMIN_ID, FakeClock, FakeMessenger, make_message


async def fill(db) -> ChecksRepo:
    clock = FakeClock()
    await Users(db, clock).touch(77, "ru", "channel")
    await Users(db, clock).touch(78, "en", "fb")
    repo = ChecksRepo(db, clock)
    check_id = await repo.create(NewCheck(77, "channel", parse_input("site.org", []), QUEUED))
    await repo.finish_done(check_id, CheckResult("https://site.org/", page(), security(), judge(page(), security(), TODAY)))
    await repo.create(NewCheck(78, "fb", None, "failed", "not_a_link"))
    return repo


async def test_stats_by_label_for_week_and_month(db):
    messenger = FakeMessenger()
    await on_stats(make_message("/stats", user_id=ADMIN_ID), repo=await fill(db), messenger=messenger, texts=TEXTS,
                   brand=BRAND, clock=FakeClock())
    text = messenger.last()
    assert "Учёт за 7 дней" in text and "Учёт за 30 дней" in text
    assert "channel | 1 | 1 | 1 | 0" in text
    assert "fb | 1 | 0 | 1 | 1" in text
    assert "Отказы: not_a_link — 1." in text
    assert "Нажатия «Обсудить» бот не видит" in text


async def test_site_shows_where_the_person_came_from_and_what_they_saw(db):
    messenger = FakeMessenger()
    await on_site(make_message("/site site.org", user_id=ADMIN_ID), CommandObject(command="site", args="site.org"),
                  repo=await fill(db), messenger=messenger, texts=TEXTS, brand=BRAND)
    text = messenger.last()
    assert "Проверки site.org" in text
    assert "25.09.2026 19:00 | channel | ok | хорошо · хорошо · хорошо · хорошо" in text


async def test_site_without_domain_explains_usage(db):
    messenger = FakeMessenger()
    await on_site(make_message("/site", user_id=ADMIN_ID), CommandObject(command="site", args=None),
                  repo=await fill(db), messenger=messenger, texts=TEXTS, brand=BRAND)
    assert "Пришлите домен: /site example.com" in messenger.last()


async def test_only_owner_passes_the_filter(settings):
    assert await IsAdmin()(make_message("/stats", user_id=ADMIN_ID), settings=settings)
    assert not await IsAdmin()(make_message("/stats", user_id=500), settings=settings)


# /site показывает ключевые цифры (время до главного на экране и вес страницы), теми же функциями
# форматирования, что и отчёт.
async def test_site_shows_lcp_seconds_and_page_weight(db):
    messenger = FakeMessenger()
    await on_site(make_message("/site site.org", user_id=ADMIN_ID), CommandObject(command="site", args="site.org"),
                  repo=await fill(db), messenger=messenger, texts=TEXTS, brand=BRAND)
    assert "1,4 секунды · 260 КБ" in messenger.last()


# Нет цифры в metrics (проверка упала до замера) — прочерк, без падения.
async def test_site_shows_a_dash_when_key_numbers_are_missing(db):
    clock = FakeClock()
    await Users(db, clock).touch(77, "ru", "channel")
    repo = ChecksRepo(db, clock)
    check_id = await repo.create(NewCheck(77, "channel", parse_input("site.org", []), QUEUED))
    await repo.finish_failed(check_id, "timeout", charged=False)
    messenger = FakeMessenger()
    await on_site(make_message("/site site.org", user_id=ADMIN_ID), CommandObject(command="site", args="site.org"),
                  repo=repo, messenger=messenger, texts=TEXTS, brand=BRAND)
    assert "— · —" in messenger.last()


# Служебное сообщение «пришлите домен» собрано через simple_message, не своей копией.
async def test_site_usage_message_is_built_with_simple_message(db):
    messenger = FakeMessenger()
    await on_site(make_message("/site", user_id=ADMIN_ID), CommandObject(command="site", args=None),
                  repo=await fill(db), messenger=messenger, texts=TEXTS, brand=BRAND)
    assert messenger.sent[-1][1] == simple_message(BRAND, TEXTS.get(OWNER_LANG, "site_usage"))


# usage-сообщение собрано вызовом simple_message, SECTION_SIZE и ITEMS_JOIN — из bot.site_check.report, не
# свои копии. Разница не видна в тексте сообщения (простое сообщение выглядит так же), поэтому проверяем
# источник модуля.
def test_admin_reuses_simple_message_and_report_constants_instead_of_redeclaring_them():
    source = inspect.getsource(admin)
    assert "simple_message(" in source
    assert "from bot.site_check.report import" in source
    assert re.search(r"(?m)^SECTION_SIZE\s*=", source) is None
    assert re.search(r"(?m)^ITEMS_JOIN\s*=", source) is None
