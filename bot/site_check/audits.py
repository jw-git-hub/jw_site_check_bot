"""Общие помощники чтения проверок Lighthouse (ТЗ, 5.1): состояние проверки, её элементы и числа, адреса и имена
файлов. Неожиданная форма ответа даёт известный исход — «неизвестно», пусто, None, — а не падение разбора."""
import re
from enum import StrEnum
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

PASS_SCORE = 0.9
BINARY_MODE = "binary"
NOT_APPLICABLE_MODE = "notApplicable"
UNKNOWN_MODES = frozenset({"error", "manual"})
MAX_NAME_LENGTH = 40
ELLIPSIS = "…"
BUILD_HASH = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{8,}$")


class AuditState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


def is_number(value: Any) -> bool:
    """int/float, но не bool — bool лишь формально подтип int."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def as_number(value: Any) -> float:
    return float(value) if is_number(value) else 0.0


def audit_entry(audits: dict[str, Any], name: str) -> dict[str, Any]:
    entry = audits.get(name)
    return entry if isinstance(entry, dict) else {}


def audit_items(audits: dict[str, Any], name: str) -> list[dict[str, Any]]:
    details = audit_entry(audits, name).get("details")
    items = details.get("items") if isinstance(details, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def numeric_value(audits: dict[str, Any], name: str) -> float | None:
    value = audit_entry(audits, name).get("numericValue")
    return float(value) if is_number(value) else None


def audit_state(audit: Any) -> AuditState:
    if not isinstance(audit, dict):
        return AuditState.UNKNOWN
    mode = audit.get("scoreDisplayMode")
    if mode == NOT_APPLICABLE_MODE:
        return AuditState.NOT_APPLICABLE
    score = audit.get("score")
    if mode in UNKNOWN_MODES or not is_number(score):
        return AuditState.UNKNOWN
    threshold = 1 if mode == BINARY_MODE else PASS_SCORE
    return AuditState.PASSED if score >= threshold else AuditState.FAILED


def strip_params(url: str) -> str:
    if not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def file_name(url: str) -> str:
    """Имя файла, как его увидит владелец в админке сайта: без хвоста сборки, до 40 знаков (ТЗ, 5.5)."""
    if not isinstance(url, str):
        return ""
    try:
        return _file_name(url)
    except ValueError:
        return ""


def _file_name(url: str) -> str:
    parts = urlsplit(url)
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) or parts.hostname or url
    pieces = name.split(".")
    if len(pieces) >= 3 and BUILD_HASH.match(pieces[-2]):
        pieces.pop(-2)
    joined = ".".join(pieces)
    return joined if len(joined) <= MAX_NAME_LENGTH else joined[:MAX_NAME_LENGTH - 1] + ELLIPSIS
