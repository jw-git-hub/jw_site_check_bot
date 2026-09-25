"""Ответ PageSpeed → замеры (ТЗ, 5.2–5.5). Только цифры и состояния проверок; оценки — в verdict.py.

Как читается проверка (ТЗ, 5.1): оценка 1 у «да/нет» и от 0,9 у остальных — пройдена; notApplicable — находки нет;
error или проверки нет в ответе — «неизвестно».

requested_url (уточнение владельца к ТЗ, 7.5): бот не проходит переадресации сам, поэтому вместо «цепочки
переадресаций» отдаём то, что просили измерить (`requestedUrl`), и то, где Lighthouse оказался (final_url) —
задача 13 покажет «запрошено → итог».
"""
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from bot.site_check.pagespeed import AUDIT_IDS

PASS_SCORE = 0.9
BINARY_MODE = "binary"
NOT_APPLICABLE_MODE = "notApplicable"
UNKNOWN_MODES = frozenset({"error", "manual"})
IMAGE_REQUEST_TYPE = "Image"
IMAGE_SUMMARY_TYPE = "image"
TOTAL_SUMMARY_TYPE = "total"
SUMMARY_TYPES = ("total", "image", "script", "font", "stylesheet")
IMAGE_SAVINGS = ("image-delivery-insight", "lcp-discovery-insight")
SCRIPT_SAVINGS = ("render-blocking-insight", "unused-javascript", "legacy-javascript-insight",
                  "duplicated-javascript-insight")
SERVER_SAVINGS = ("document-latency-insight",)
HEAVIEST_IMAGES = 3
HEAVIEST_FILES = 10
MAX_NAME_LENGTH = 40
ELLIPSIS = "…"
BUILD_HASH = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{8,}$")


class AuditState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SpeedFacts:
    lcp_ms: float | None
    fcp_ms: float | None
    tbt_ms: float | None
    cls: float | None
    speed_index_ms: float | None
    server_ms: float | None
    image_savings_ms: float
    script_savings_ms: float
    server_savings_ms: float


@dataclass(frozen=True)
class MobileFacts:
    viewport: AuditState
    viewport_snippet: str | None
    target_size: AuditState
    meta_viewport: AuditState


@dataclass(frozen=True)
class FileWeight:
    name: str
    kind: str
    bytes: int
    savings_bytes: int


@dataclass(frozen=True)
class ImageFacts:
    page_bytes: int | None
    image_bytes: int | None
    heaviest: tuple[FileWeight, ...]
    compress_ratio: int | None


@dataclass(frozen=True)
class PostNumbers:
    requests: int | None
    bytes_by_type: tuple[tuple[str, int], ...]
    heaviest_files: tuple[FileWeight, ...]
    third_parties: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class PageFacts:
    lighthouse_version: str
    final_url: str
    speed: SpeedFacts
    mobile: MobileFacts
    images: ImageFacts
    insecure_urls: tuple[str, ...]
    post: PostNumbers
    missing_audits: tuple[str, ...]
    # C7: что просили измерить (requestedUrl, без параметров); задача 13 покажет «запрошено → итог».
    requested_url: str = ""


def audit_state(audit: dict[str, Any] | None) -> AuditState:
    if not audit:
        return AuditState.UNKNOWN
    mode = audit.get("scoreDisplayMode")
    if mode == NOT_APPLICABLE_MODE:
        return AuditState.NOT_APPLICABLE
    score = audit.get("score")
    if mode in UNKNOWN_MODES or score is None:
        return AuditState.UNKNOWN
    threshold = 1 if mode == BINARY_MODE else PASS_SCORE
    return AuditState.PASSED if score >= threshold else AuditState.FAILED


def parse_lighthouse(result: dict[str, Any]) -> PageFacts:
    audits = result.get("audits") or {}
    savings = _savings_by_url(audits)
    return PageFacts(
        lighthouse_version=str(result.get("lighthouseVersion", "")),
        final_url=str(result.get("finalDisplayedUrl") or result.get("requestedUrl") or ""),
        speed=_speed(audits),
        mobile=_mobile(audits),
        images=_images(audits, savings),
        insecure_urls=tuple(strip_params(item.get("url", "")) for item in _items(audits, "is-on-https")),
        post=_post(audits, savings),
        missing_audits=tuple(name for name in AUDIT_IDS if name not in audits),
        requested_url=strip_params(str(result.get("requestedUrl") or "")),
    )


def _items(audits: dict[str, Any], name: str) -> list[dict[str, Any]]:
    details = (audits.get(name) or {}).get("details") or {}
    return [item for item in details.get("items") or [] if isinstance(item, dict)]


def _numeric(audits: dict[str, Any], name: str) -> float | None:
    value = (audits.get(name) or {}).get("numericValue")
    return float(value) if isinstance(value, int | float) else None


def _lcp_savings(audits: dict[str, Any], names: tuple[str, ...]) -> float:
    return sum(float(((audits.get(name) or {}).get("metricSavings") or {}).get("LCP") or 0) for name in names)


def _speed(audits: dict[str, Any]) -> SpeedFacts:
    return SpeedFacts(
        lcp_ms=_numeric(audits, "largest-contentful-paint"), fcp_ms=_numeric(audits, "first-contentful-paint"),
        tbt_ms=_numeric(audits, "total-blocking-time"), cls=_numeric(audits, "cumulative-layout-shift"),
        speed_index_ms=_numeric(audits, "speed-index"), server_ms=_numeric(audits, "server-response-time"),
        image_savings_ms=_lcp_savings(audits, IMAGE_SAVINGS), script_savings_ms=_lcp_savings(audits, SCRIPT_SAVINGS),
        server_savings_ms=_lcp_savings(audits, SERVER_SAVINGS),
    )


def _mobile(audits: dict[str, Any]) -> MobileFacts:
    items = _items(audits, "viewport-insight")
    snippet = (items[0].get("node") or {}).get("snippet") if items else None
    return MobileFacts(audit_state(audits.get("viewport-insight")), snippet, audit_state(audits.get("target-size")),
                       audit_state(audits.get("meta-viewport")))


def _savings_by_url(audits: dict[str, Any]) -> dict[str, int]:
    items = _items(audits, "image-delivery-insight")
    return {strip_params(item.get("url", "")): int(item.get("wastedBytes") or 0) for item in items}


def _images(audits: dict[str, Any], savings: dict[str, int]) -> ImageFacts:
    requests = [item for item in _items(audits, "network-requests") if item.get("resourceType") == IMAGE_REQUEST_TYPE]
    page_bytes = _numeric(audits, "total-byte-weight")
    return ImageFacts(page_bytes=None if page_bytes is None else int(page_bytes),
                      image_bytes=_summary_bytes(audits).get(IMAGE_SUMMARY_TYPE),
                      heaviest=_heaviest(requests, savings, HEAVIEST_IMAGES), compress_ratio=_compress_ratio(audits))


def _heaviest(requests: list[dict[str, Any]], savings: dict[str, int], limit: int) -> tuple[FileWeight, ...]:
    ordered = sorted(requests, key=lambda item: item.get("transferSize") or 0, reverse=True)[:limit]
    return tuple(FileWeight(file_name(item.get("url", "")), str(item.get("resourceType", "")),
                            int(item.get("transferSize") or 0), savings.get(strip_params(item.get("url", "")), 0))
                 for item in ordered)


def _compress_ratio(audits: dict[str, Any]) -> int | None:
    items = _items(audits, "image-delivery-insight")
    total = sum(int(item.get("totalBytes") or 0) for item in items)
    after = total - sum(int(item.get("wastedBytes") or 0) for item in items)
    return math.floor(total / after) if total and after > 0 else None


def _summary_bytes(audits: dict[str, Any]) -> dict[str, int]:
    return {str(item.get("resourceType")): int(item.get("transferSize") or 0)
            for item in _items(audits, "resource-summary")}


def _post(audits: dict[str, Any], savings: dict[str, int]) -> PostNumbers:
    summary = _summary_bytes(audits)
    total = [item for item in _items(audits, "resource-summary") if item.get("resourceType") == TOTAL_SUMMARY_TYPE]
    return PostNumbers(
        requests=int(total[0].get("requestCount") or 0) if total else None,
        bytes_by_type=tuple((kind, summary[kind]) for kind in SUMMARY_TYPES if kind in summary),
        heaviest_files=_heaviest(_items(audits, "network-requests"), savings, HEAVIEST_FILES),
        third_parties=_third_parties(audits),
    )


def _third_parties(audits: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    rows = []
    for item in _items(audits, "third-parties-insight"):
        entity = item.get("entity")
        name = entity.get("text") if isinstance(entity, dict) else entity
        if name:
            rows.append((str(name), int(item.get("transferSize") or 0)))
    return tuple(rows)


def strip_params(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def file_name(url: str) -> str:
    """Имя файла, как его увидит владелец в админке сайта: без хвоста сборки, до 40 знаков (ТЗ, 5.5)."""
    parts = urlsplit(url)
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) or parts.hostname or url
    pieces = name.split(".")
    if len(pieces) >= 3 and BUILD_HASH.match(pieces[-2]):
        pieces.pop(-2)
    joined = ".".join(pieces)
    return joined if len(joined) <= MAX_NAME_LENGTH else joined[:MAX_NAME_LENGTH - 1] + ELLIPSIS
