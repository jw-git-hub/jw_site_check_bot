"""Строки трёх новых блоков отчёта (ТЗ, 5.6–5.8, 7.1). Какие находки — решают *_block.py; здесь только слова.

Строка — со строчной буквы, без точки; «хорошо» — только факты, которые подтверждены данными (ТЗ, раздел 1).
"""
from typing import TYPE_CHECKING

from bot.core.i18n import Lang, Texts
from bot.site_check.audits import ELLIPSIS, AuditState
from bot.site_check.findings import BlockVerdict, Finding, FindingItem, Grade
from bot.site_check.page_fetch import PagePreview
from bot.site_check.readability_block import ReadabilityFacts
from bot.site_check.thresholds import QUOTE_MAX_CHARS

if TYPE_CHECKING:
    from bot.site_check.report import ReportRequest

NAMES_JOIN = ", "


def quote(text: str, limit: int = QUOTE_MAX_CHARS) -> str:
    """Цитата с сайта — как есть, длинная обрезается многоточием (ТЗ, 5.6)."""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + ELLIPSIS


CLOSED_LINES = {Finding.CLOSED_META: ("search_closed_meta", "search_closed_effect"),
                Finding.CLOSED_HEADER: ("search_closed_header", "search_closed_effect"),
                Finding.CLOSED_ROBOTS: ("search_closed_robots", "search_closed_robots_effect")}
SEARCH_LINES = {Finding.ROBOTS_ERRORS: "search_robots_errors", Finding.NO_TITLE: "search_no_title",
                Finding.NO_DESCRIPTION: "search_no_description"}


def search_lines(texts: Texts, lang: Lang, request: "ReportRequest", verdict: BlockVerdict) -> list[str]:
    fixes = [line for item in verdict.findings if item.grade is Grade.FIX for line in _search_fix(texts, lang, item)]
    closed = [item for item in verdict.findings if item.grade is Grade.BAD]
    if closed:
        return [texts.get(lang, key) for key in CLOSED_LINES[closed[0].finding]] + fixes
    title = _page_title(request.preview)
    if verdict.grade is Grade.GOOD:
        return [texts.get(lang, "search_open"), _title_line(texts, lang, title)]
    has_title = title and all(item.finding is not Finding.NO_TITLE for item in verdict.findings)
    return ([texts.get(lang, "search_title_quote", title=quote(title))] if has_title else []) + fixes


def _search_fix(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    if item.finding is Finding.ROBOTS_UNREACHABLE:
        return [texts.get(lang, "search_robots_unreachable", status=item.detail)]
    if item.finding is Finding.CANONICAL_FOREIGN:
        return [texts.get(lang, "search_canonical_foreign", host=item.detail),
                texts.get(lang, "search_canonical_foreign_effect")]
    return [texts.get(lang, SEARCH_LINES[item.finding])]


def _page_title(preview: PagePreview | None) -> str | None:
    """Настоящий <title> из своей загрузки (ТЗ, 5.6) — не og:title."""
    return preview.head.title if preview and preview.head else None


def _title_line(texts: Texts, lang: Lang, title: str | None) -> str:
    if title:
        return texts.get(lang, "search_title_quote", title=quote(title))
    return texts.get(lang, "search_title_and_description")


def readability_lines(texts: Texts, lang: Lang, request: "ReportRequest", verdict: BlockVerdict) -> list[str]:
    if verdict.grade is Grade.GOOD:
        return _readability_good(texts, lang, request.page.readability)
    return [line for item in verdict.findings for line in _readability_finding(texts, lang, item)]


def _readability_good(texts: Texts, lang: Lang, facts: ReadabilityFacts) -> list[str]:
    """Контраст не проверен — о нём молчим; подписи — только если image-alt пройдена, а не notApplicable (5.8)."""
    lines = [texts.get(lang, "readability_good_contrast")] if facts.contrast is not AuditState.UNKNOWN else []
    if facts.alt is AuditState.PASSED:
        lines.append(texts.get(lang, "readability_good_alt"))
    return lines


def _readability_finding(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    if item.finding is Finding.LOW_CONTRAST:
        return _contrast_lines(texts, lang, item)
    if item.finding is Finding.NO_ALT:
        return _alt_lines(texts, lang, item)
    return [texts.get(lang, "readability_no_lang")]


def _contrast_lines(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    first = (texts.get(lang, "readability_low_contrast", example=quote(item.examples[0])) if item.examples
             else texts.get(lang, "readability_low_contrast_plain"))
    return [first, texts.get(lang, "readability_low_contrast_effect")]


def _alt_lines(texts: Texts, lang: Lang, item: FindingItem) -> list[str]:
    count = texts.count(lang, item.count or 0, "picture")
    first = (texts.get(lang, "readability_no_alt", count=count, names=NAMES_JOIN.join(item.examples))
             if item.examples else texts.get(lang, "readability_no_alt_plain", count=count))
    return [first, texts.get(lang, "readability_no_alt_effect")]
