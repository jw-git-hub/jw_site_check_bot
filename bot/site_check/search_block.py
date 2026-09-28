"""«Поиск в Google» (ТЗ, 5.6): открыта ли страница поисковикам, robots.txt, заголовок и описание — из категории
seo; canonical на чужой сайт — из своей загрузки страницы (page_fetch.py): Lighthouse такое не ловит.

«Fetch of robots.txt failed» без кода ответа — сбой Lighthouse, не сайта: не находка. robots.txt при 4xx или пустом
файле — notApplicable, тоже не находка (ТЗ, 5.6).
"""
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import idna

from bot.site_check.audits import AuditState, audit_entry, audit_items, audit_state, strip_params
from bot.site_check.findings import Block, BlockVerdict, Finding, FindingItem, Grade, graded, not_checked
from bot.site_check.page_fetch import PagePreview
from bot.site_check.url_input import to_ascii_host

HEADER_SOURCE_PREFIX = "x-robots-tag"
NODE_SOURCE = "node"
CODE_SOURCE = "code"
ROBOTS_SOURCE = "source-location"
STATUS_IN_TEXT = re.compile(r"\b([1-5][0-9]{2})\b")
FIRST_SERVER_ERROR = 500
WWW_PREFIX = "www."


class BlockSource(StrEnum):
    META = "meta"
    HEADER = "header"
    ROBOTS_TXT = "robots_txt"


@dataclass(frozen=True)
class SearchFacts:
    crawlable: AuditState
    block_source: BlockSource | None    # откуда запрет; None — запрета нет или форма незнакома
    block_snippet: str                  # метатег, заголовок или место в robots.txt — для «Подробных замеров»
    robots_txt: AuditState
    robots_txt_status: int | None       # код ответа на robots.txt, если Lighthouse его назвал
    robots_txt_errors: tuple[str, ...]  # номера строк с ошибками
    title: AuditState
    description: AuditState


# Незнакомая форма источника запрета — как метатег: так Lighthouse сообщает чаще всего (ТЗ, 5.6).
CLOSED_FINDINGS = {BlockSource.META: Finding.CLOSED_META, BlockSource.HEADER: Finding.CLOSED_HEADER,
                   BlockSource.ROBOTS_TXT: Finding.CLOSED_ROBOTS}


def parse_search(audits: dict[str, Any]) -> SearchFacts:
    source, snippet = block_source(audit_items(audits, "is-crawlable"))
    return SearchFacts(
        crawlable=audit_state(audits.get("is-crawlable")), block_source=source, block_snippet=snippet,
        robots_txt=audit_state(audits.get("robots-txt")),
        robots_txt_status=_status_in(audit_entry(audits, "robots-txt").get("displayValue")),
        robots_txt_errors=tuple(str(item["index"]) for item in audit_items(audits, "robots-txt") if "index" in item),
        title=audit_state(audits.get("document-title")), description=audit_state(audits.get("meta-description")))


def block_source(items: list[dict[str, Any]]) -> tuple[BlockSource | None, str]:
    """Источник запрета по первому элементу is-crawlable: метатег, заголовок сервера или строка robots.txt."""
    source = items[0].get("source") if items else None
    if isinstance(source, str):
        return _header_source(source)
    if not isinstance(source, dict):
        return None, ""
    kind = source.get("type")
    if kind == NODE_SOURCE:
        return BlockSource.META, str(source.get("snippet", ""))
    if kind == ROBOTS_SOURCE:
        return BlockSource.ROBOTS_TXT, f"{strip_params(str(source.get('url', '')))}:{source.get('line', '')}"
    if kind == CODE_SOURCE:
        return _header_source(str(source.get("value", "")))
    return None, ""


def _header_source(text: str) -> tuple[BlockSource | None, str]:
    return (BlockSource.HEADER, text) if text.lower().startswith(HEADER_SOURCE_PREFIX) else (None, "")


def _status_in(text: Any) -> int | None:
    found = STATUS_IN_TEXT.search(text) if isinstance(text, str) else None
    return int(found.group(1)) if found else None


def judge_search(search: SearchFacts | None, preview: PagePreview | None = None) -> BlockVerdict:
    """Главный источник — is-crawlable (ТЗ, 6.1): нет его — «неизвестно», блок не печатается."""
    if search is None or search.crawlable is AuditState.UNKNOWN:
        return not_checked(Block.SEARCH)
    return graded(Block.SEARCH, [*_closed(search), *_robots(search), *_texts(search), *_canonical(preview)])


def _closed(search: SearchFacts) -> list[FindingItem]:
    if search.crawlable is not AuditState.FAILED:
        return []
    return [FindingItem(CLOSED_FINDINGS.get(search.block_source, Finding.CLOSED_META), Grade.BAD)]


def _robots(search: SearchFacts) -> list[FindingItem]:
    if search.robots_txt is not AuditState.FAILED:
        return []
    if search.robots_txt_errors:
        return [FindingItem(Finding.ROBOTS_ERRORS, Grade.FIX, count=len(search.robots_txt_errors))]
    status = search.robots_txt_status
    if status is not None and status >= FIRST_SERVER_ERROR:
        return [FindingItem(Finding.ROBOTS_UNREACHABLE, Grade.FIX, detail=str(status))]
    return []


def _texts(search: SearchFacts) -> list[FindingItem]:
    found = []
    if search.title is AuditState.FAILED:
        found.append(FindingItem(Finding.NO_TITLE, Grade.FIX))
    if search.description is AuditState.FAILED:
        found.append(FindingItem(Finding.NO_DESCRIPTION, Grade.FIX))
    return found


def _canonical(preview: PagePreview | None) -> list[FindingItem]:
    host = foreign_canonical_host(preview)
    return [FindingItem(Finding.CANONICAL_FOREIGN, Grade.FIX, detail=host)] if host else []


def foreign_canonical_host(preview: PagePreview | None) -> str | None:
    """Хост canonical, если это другой сайт (ТЗ, 5.6): без учёта www., регистра и записи домена (IDNA). Как читается."""
    head = preview.head if preview else None
    if head is None or not head.canonical:
        return None
    canonical, page = _site_host(head.canonical), _site_host(preview.url)
    if canonical is None or page is None or canonical == page:
        return None
    return _readable(canonical)


def _site_host(url: str) -> str | None:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    ascii_host = to_ascii_host(host) if host else None
    return ascii_host.removeprefix(WWW_PREFIX) if ascii_host else None


def _readable(ascii_host: str) -> str:
    try:
        return idna.decode(ascii_host)
    except idna.IDNAError:
        return ascii_host
