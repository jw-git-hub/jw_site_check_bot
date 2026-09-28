"""Строки трёх новых блоков отчёта (ТЗ, 5.6–5.8, 7.1). Какие находки — решают *_block.py; здесь только слова.

Строка — со строчной буквы, без точки; «хорошо» — только факты, которые подтверждены данными (ТЗ, раздел 1).
"""
from typing import TYPE_CHECKING

from bot.core.i18n import Lang, Texts
from bot.site_check.audits import ELLIPSIS, AuditState
from bot.site_check.findings import BlockVerdict, Finding, FindingItem, Grade
from bot.site_check.readability_block import ReadabilityFacts
from bot.site_check.thresholds import QUOTE_MAX_CHARS

if TYPE_CHECKING:
    from bot.site_check.report import ReportRequest

NAMES_JOIN = ", "


def quote(text: str, limit: int = QUOTE_MAX_CHARS) -> str:
    """Цитата с сайта — как есть, длинная обрезается многоточием (ТЗ, 5.6)."""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + ELLIPSIS


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
