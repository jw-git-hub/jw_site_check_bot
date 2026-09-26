from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from bot.core.users import Users
from bot.site_check.checks import DONE, FAILED, INTERRUPTED, QUEUED, ChecksRepo, NewCheck
from bot.site_check.limits import LIMIT_GLOBAL, LIMIT_USER, Limits
from bot.site_check.pipeline import CheckResult
from bot.site_check.url_input import parse_input
from bot.site_check.verdict import judge
from tests.builders import TODAY, page, security
from tests.fakes import ADMIN_ID, FakeClock

USER = 77
UNKNOWN_USER_ID = 999


def good_result() -> CheckResult:
    return CheckResult("https://www.site.org/?utm_source=x", page(), security(), judge(page(), security(), TODAY))


@pytest.fixture
async def repo(db):
    clock = FakeClock()
    for user_id, label in ((USER, "channel"), (78, "fb"), (ADMIN_ID, "direct")):
        await Users(db, clock).touch(user_id, "ru", label)
    return ChecksRepo(db, clock)


async def add_check(repo: ChecksRepo, user_id: int = USER, charged: bool = True, source: str = "channel") -> int:
    check_id = await repo.create(NewCheck(user_id, source, parse_input("site.org", []), QUEUED, chat_id=user_id,
                                          message_id=5))
    await repo.finish_failed(check_id, "not_found", charged)
    return check_id


async def test_stored_url_has_no_parameters(repo, db):
    check_id = await repo.create(NewCheck(USER, "channel", parse_input("https://пример.рф/uslugi?utm_source=fb", []),
                                          QUEUED))
    async with db.connect() as connection:
        row = (await connection.execute(text("SELECT domain, url FROM checks WHERE id = :id"), {"id": check_id})).one()
    assert (row.domain, row.url) == ("пример.рф", "https://пример.рф/uslugi")


async def test_done_check_keeps_grades_summary_and_numbers(repo):
    check_id = await repo.create(NewCheck(USER, "channel", parse_input("site.org", []), QUEUED))
    await repo.mark_running(check_id)
    await repo.finish_done(check_id, good_result())
    recent = (await repo.recent_for_domain("site.org"))[0]
    assert (recent.status, recent.summary, recent.source) == (DONE, "ok", "channel")
    assert recent.grades == ("good", "good", "good", "good")
    assert recent.metrics["final_url"] == "https://www.site.org/"
    assert recent.metrics["cert_until"] == "2026-12-08"
    assert recent.metrics["lcp_ms"] == 1400


async def test_charged_checks_are_counted_per_user_and_in_total(repo):
    since = FakeClock().now() - timedelta(hours=24)
    await add_check(repo, charged=True)
    await add_check(repo, charged=False)
    await add_check(repo, user_id=78, charged=True)
    assert await repo.count_charged_since(since, USER) == 1
    assert await repo.count_charged_since(since) == 2


async def test_unfinished_checks_are_interrupted_and_uncharged(repo):
    check_id = await repo.create(NewCheck(USER, "channel", parse_input("site.org", []), QUEUED, chat_id=USER,
                                          message_id=9))
    unfinished = await repo.interrupt_unfinished()
    assert [(item.check_id, item.chat_id, item.message_id, item.lang) for item in unfinished] == [(check_id, USER, 9, "ru")]
    assert (await repo.recent_for_domain("site.org"))[0].status == "interrupted"
    assert await repo.interrupt_unfinished() == []


async def test_stats_by_label(repo):
    await add_check(repo, charged=True, source="channel")
    check_id = await repo.create(NewCheck(USER, "channel", parse_input("site.org", []), QUEUED))
    await repo.finish_done(check_id, good_result())
    await repo.create(NewCheck(78, "fb", None, FAILED, "not_a_link"))
    rows, refusals = await repo.stats(FakeClock().now() - timedelta(days=7))
    by_label = {row.source: row for row in rows}
    assert (by_label["channel"].checks, by_label["channel"].reported_users, by_label["channel"].new_users) == (2, 1, 1)
    assert (by_label["fb"].refusals, by_label["fb"].new_users) == (1, 1)
    assert dict(refusals) == {"not_found": 1, "not_a_link": 1}


async def test_user_limit_with_hours_until_next(repo):
    await add_check(repo)
    await add_check(repo)
    clock = FakeClock()
    clock.advance(3600)  # у лимитов — через час после обеих проверок
    decision = await Limits(repo, clock, user_daily=2, global_daily=100, admin_id=ADMIN_ID).decide(USER)
    assert (decision.allowed, decision.code, decision.hours_left) == (False, LIMIT_USER, 23)


async def test_global_limit_and_admin_without_limit(repo):
    limits = Limits(repo, FakeClock(), user_daily=10, global_daily=2, admin_id=ADMIN_ID)
    await add_check(repo, user_id=USER)
    await add_check(repo, user_id=78)
    assert (await limits.decide(USER)).code == LIMIT_GLOBAL
    assert (await limits.decide(ADMIN_ID)).allowed


class BrokenRepo:
    async def count_charged_since(self, since, user_id=None):
        raise RuntimeError("база недоступна")

    async def oldest_charged_since(self, since, user_id):
        raise RuntimeError("база недоступна")


async def test_limits_fall_back_to_memory_when_database_is_down():
    limits = Limits(BrokenRepo(), FakeClock(), user_daily=1, global_daily=100, admin_id=ADMIN_ID)
    assert (await limits.decide(USER)).allowed
    limits.remember_charge(USER)
    assert (await limits.decide(USER)).code == LIMIT_USER


# --- Поправки к задаче 15 (task-15-carries.md) ---


async def test_mark_running_status_is_covered_by_interrupt(repo):
    """Поправка 1: статусы идут в SQL только через константы. Проверяем, что RUNNING в MARK_RUNNING и
    INTERRUPT — один и тот же код, а не разные литералы: проверка, помеченная running, тоже прерывается."""
    check_id = await repo.create(NewCheck(USER, "channel", parse_input("site.org", []), QUEUED, chat_id=USER,
                                          message_id=11))
    await repo.mark_running(check_id)
    unfinished = await repo.interrupt_unfinished()
    assert [item.check_id for item in unfinished] == [check_id]
    assert (await repo.recent_for_domain("site.org"))[0].status == INTERRUPTED


async def test_create_rejects_check_for_unknown_user(repo):
    """Поправки 2 и 3: пользователь должен быть в базе до записи проверки (Users.touch — обязанность вызывающего,
    ChecksRepo.create его не создаёт сам), а внешние ключи (PRAGMA foreign_keys=ON) действительно применяются на
    каждом соединении — запись проверки с несуществующим user_id падает ошибкой целостности, а не молча проходит.
    Вызываем репозиторий напрямую (не через best_effort, поправка 4), иначе ошибку тест бы не увидел."""
    with pytest.raises(IntegrityError):
        await repo.create(NewCheck(UNKNOWN_USER_ID, "channel", parse_input("site.org", []), QUEUED))
