"""Команды владельца (ТЗ, раздел 8): /stats — учёт по меткам, /site <домен> — откуда человек и что он видел."""
from datetime import timedelta
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import Message

from bot.core import rich
from bot.core.clock import Clock
from bot.core.commands import Brand, simple_message
from bot.core.config import CoreSettings
from bot.core.i18n import Texts
from bot.core.messenger import Messenger
from bot.site_check.checks import ChecksRepo, DomainCheck, LabelStats
from bot.site_check.notifier import OWNER_LANG
from bot.site_check.post_numbers import OWNER_TIMEZONE, TIME_FORMAT
# Те же константы, что и в отчёте, не свои копии.
from bot.site_check.report import ITEMS_JOIN, SECTION_SIZE
from bot.site_check.url_input import Target, parse_input

STATS_WINDOWS_DAYS = (7, 30)
PARTS_JOIN = " · "
UNKNOWN_GRADE = "unknown"
NO_VALUE = "—"

router = Router(name="admin")


class IsAdmin(Filter):
    async def __call__(self, event: Message, settings: CoreSettings) -> bool:
        return event.from_user is not None and event.from_user.id == settings.admin_id


@router.message(Command("stats"), IsAdmin())
async def on_stats(message: Message, repo: ChecksRepo, messenger: Messenger, texts: Texts, brand: Brand,
                   clock: Clock) -> None:
    windows = [(days, *await repo.stats(clock.now() - timedelta(days=days))) for days in STATS_WINDOWS_DAYS]
    await messenger.send(message.chat.id, stats_message(texts, brand, windows))


@router.message(Command("site"), IsAdmin())
async def on_site(message: Message, command: CommandObject, repo: ChecksRepo, messenger: Messenger, texts: Texts,
                  brand: Brand) -> None:
    parsed = parse_input(command.args or "", [])
    if not isinstance(parsed, Target):
        await messenger.send(message.chat.id, simple_message(brand, texts.get(OWNER_LANG, "site_usage")))
        return
    checks = await repo.recent_for_domain(parsed.display_host)
    await messenger.send(message.chat.id, site_message(texts, brand, parsed.display_host, checks))


def stats_message(texts: Texts, brand: Brand,
                  windows: list[tuple[int, list[LabelStats], list[tuple[str, int]]]]) -> dict:
    blocks = [rich.header(brand.section)]
    for days, rows, refusals in windows:
        blocks += _stats_section(texts, days, rows, refusals)
    blocks.append(rich.paragraph(texts.get(OWNER_LANG, "stats_note")))
    return rich.message(blocks)


def _stats_section(texts: Texts, days: int, rows: list[LabelStats], refusals: list[tuple[str, int]]) -> list[dict]:
    title = rich.heading(texts.get(OWNER_LANG, "stats_title", days=texts.count(OWNER_LANG, days, "day")),
                         SECTION_SIZE)
    if not rows:
        return [title, rich.paragraph(texts.get(OWNER_LANG, "stats_empty"))]
    header = [texts.get(OWNER_LANG, key) for key in ("stats_label", "stats_new", "stats_reported", "stats_checks",
                                                      "stats_refusals")]
    body = [[row.source, str(row.new_users), str(row.reported_users), str(row.checks), str(row.refusals)]
            for row in rows]
    section = [title, rich.table([header, *body])]
    if refusals:
        items = ITEMS_JOIN.join(f"{code} — {total}" for code, total in refusals)
        section.append(rich.paragraph(texts.get(OWNER_LANG, "stats_refusal_codes", items=items)))
    return section


def site_message(texts: Texts, brand: Brand, domain: str, checks: list[DomainCheck]) -> dict:
    blocks = [rich.header(brand.section),
             rich.heading(texts.get(OWNER_LANG, "site_title", domain=domain), SECTION_SIZE)]
    if not checks:
        return rich.message([*blocks, rich.paragraph(texts.get(OWNER_LANG, "site_empty"))])
    header = [texts.get(OWNER_LANG, key) for key in ("site_when", "site_source", "site_result", "site_grades",
                                                      "site_numbers")]
    body = [_site_row(texts, check) for check in checks]
    return rich.message([*blocks, rich.table([header, *body])])


def _site_row(texts: Texts, check: DomainCheck) -> list[str]:
    when = check.created_at.astimezone(OWNER_TIMEZONE).strftime(TIME_FORMAT)
    result = check.summary or check.error_code or check.status
    grades = PARTS_JOIN.join(texts.get(OWNER_LANG, f"grade_{grade or UNKNOWN_GRADE}") for grade in check.grades)
    numbers = PARTS_JOIN.join([_lcp_text(texts, check.metrics), _weight_text(texts, check.metrics)])
    return [when, check.source, result, grades, numbers]


def _lcp_text(texts: Texts, metrics: dict[str, Any]) -> str:
    """Время до главного на экране (LCP). Нет цифры (проверка упала до замера) — прочерк, без падения."""
    lcp_ms = metrics.get("lcp_ms")
    return texts.seconds(OWNER_LANG, lcp_ms) if lcp_ms is not None else NO_VALUE


def _weight_text(texts: Texts, metrics: dict[str, Any]) -> str:
    """Вес страницы. Нет цифры (проверка упала до замера) — прочерк, без падения."""
    page_bytes = metrics.get("page_bytes")
    return texts.size(OWNER_LANG, page_bytes) if page_bytes is not None else NO_VALUE
