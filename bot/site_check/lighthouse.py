"""Ответ PageSpeed → замеры (ТЗ, 5.2–5.5). Только цифры и состояния проверок; оценки — в verdict.py.

Как читается проверка (ТЗ, 5.1): оценка 1 у «да/нет» и от 0,9 у остальных — пройдена; notApplicable — находки нет;
error или проверки нет в ответе — «неизвестно».

requested_url (уточнение владельца к ТЗ, 7.5): бот не проходит переадресации сам, поэтому вместо «цепочки
переадресаций» отдаём то, что просили измерить (`requestedUrl`), и то, где Lighthouse оказался (final_url) —
цифры для поста (post_numbers.py) покажут «запрошено → итог».

Lighthouse может прислать не то, что мы ждём (обновление API, обрезанный ответ): неизвестный вход даёт известный
исход — None/0/пусто/«неизвестно», а не падение разбора (правило владельца, ТЗ 5.1 про переименование проверок).
"""
import base64
import binascii
import math
import re
from dataclasses import dataclass, field
from typing import Any

from bot.site_check.audits import (AuditState, as_number, audit_entry, audit_items, audit_state, file_name,
                                   is_number, numeric_value, strip_params)
from bot.site_check.markers import find_markers
from bot.site_check.pagespeed import AUDIT_IDS
from bot.site_check.readability_block import ReadabilityFacts, parse_readability
from bot.site_check.search_block import SearchFacts, parse_search
from bot.site_check.thresholds import BLURRY_MIN_SIDE_PX

IMAGE_REQUEST_TYPE = "Image"
IMAGE_SUMMARY_TYPE = "image"
TOTAL_SUMMARY_TYPE = "total"
SUMMARY_TYPES = ("total", "image", "script", "font", "stylesheet")
CATEGORY_NAMES = ("seo", "accessibility")  # оценки категорий для «Подробных замеров» (ТЗ, 7.5)
PERCENT = 100
IMAGE_SAVINGS = ("image-delivery-insight", "lcp-discovery-insight")
SCRIPT_SAVINGS = ("render-blocking-insight", "unused-javascript", "legacy-javascript-insight",
                  "duplicated-javascript-insight")
SERVER_SAVINGS = ("document-latency-insight",)
HEAVIEST_IMAGES = 3
HEAVIEST_FILES = 10
HTTP_URL_PREFIXES = ("http://", "https://")
MIN_TRANSFER_BYTES = 1
SCREENSHOT_MAX_BYTES = 1024 * 1024  # ТЗ, 5.9
SCREENSHOT_PREFIXES = ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")
DISPLAYED_SIZE = re.compile(r"(\d+)\s*x\s*(\d+)")


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
    stretched: tuple[str, ...] = ()          # растянутые или сплющенные картинки (image-aspect-ratio, ТЗ 5.5)
    blurry: tuple[str, ...] = ()             # нечёткие на телефоне картинки, без мелких значков (ТЗ 5.5)
    blurry_small_skipped: int = 0            # сколько мелких значков не в счёт


@dataclass(frozen=True)
class PostNumbers:
    requests: int | None
    bytes_by_type: tuple[tuple[str, int], ...]
    heaviest_files: tuple[FileWeight, ...]
    third_parties: tuple[tuple[str, int], ...]
    category_scores: tuple[tuple[str, int], ...] = ()


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
    # Что просили измерить (requestedUrl, без параметров) — пара с final_url покажет «запрошено → итог» (ТЗ, 7.5).
    requested_url: str = ""
    search: SearchFacts | None = None
    readability: ReadabilityFacts | None = None
    # Что мерил Lighthouse, с параметрами — только для своей загрузки страницы; не хранится и не пишется в журнал.
    measured_url: str = ""
    # Сервисы по запросам страницы (markers.py, ТЗ 5.10) — счётчики и чаты, которые рисует скрипт.
    service_markers: frozenset[str] = frozenset()
    # Снимок первого экрана (ТЗ, 5.9); не хранится (5.1: «скриншоты не храним») — не в repr, не в базе, не в /site.
    screenshot: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Savings:
    """Экономия по адресу картинки: сперва точное совпадение, иначе — тот же адрес без параметров.

    Разные варианты одной картинки (Next.js `?w=`, Shopify `?width=`) Lighthouse иногда называет
    одинаково в разных проверках и без параметров — тогда подходит только точный адрес.
    """

    by_url: dict[str, int]
    by_stripped: dict[str, int]

    def get(self, url: str) -> int:
        if url in self.by_url:
            return self.by_url[url]
        return self.by_stripped.get(strip_params(url), 0)


def _url(item: dict[str, Any], key: str = "url") -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def parse_screenshot(audits: dict[str, Any]) -> bytes | None:
    """Снимок первого экрана (ТЗ, 5.9): data: с JPEG, PNG или WebP в base64, не больше SCREENSHOT_MAX_BYTES."""
    details = audit_entry(audits, "final-screenshot").get("details")
    raw = details.get("data") if isinstance(details, dict) else None
    if not isinstance(raw, str) or not raw.startswith(SCREENSHOT_PREFIXES):
        return None
    try:
        image = base64.b64decode(raw.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    return image if 0 < len(image) <= SCREENSHOT_MAX_BYTES else None


def parse_lighthouse(result: dict[str, Any]) -> PageFacts:
    raw_audits = result.get("audits")
    audits = raw_audits if isinstance(raw_audits, dict) else {}
    savings = _savings_by_url(audits)
    return PageFacts(
        lighthouse_version=str(result.get("lighthouseVersion", "")),
        final_url=strip_params(str(result.get("finalDisplayedUrl") or result.get("requestedUrl") or "")),
        speed=_speed(audits),
        mobile=_mobile(audits),
        images=_images(audits, savings),
        insecure_urls=tuple(strip_params(url) for item in audit_items(audits, "is-on-https") if (url := _url(item))),
        post=_post(audits, savings, result.get("categories")),
        missing_audits=tuple(name for name in AUDIT_IDS if name not in audits),
        requested_url=strip_params(str(result.get("requestedUrl") or "")),
        search=parse_search(audits),
        readability=parse_readability(audits),
        measured_url=str(result.get("finalDisplayedUrl") or result.get("requestedUrl") or ""),
        service_markers=find_markers(" ".join(_url(item) for item in audit_items(audits, "network-requests"))),
        screenshot=parse_screenshot(audits),
    )


def _lcp_savings(audits: dict[str, Any], names: tuple[str, ...]) -> float:
    return sum(_lcp_saving(audits, name) for name in names)


def _lcp_saving(audits: dict[str, Any], name: str) -> float:
    savings = audit_entry(audits, name).get("metricSavings")
    value = savings.get("LCP") if isinstance(savings, dict) else None
    return float(value) if is_number(value) else 0.0


def _speed(audits: dict[str, Any]) -> SpeedFacts:
    return SpeedFacts(
        lcp_ms=numeric_value(audits, "largest-contentful-paint"), fcp_ms=numeric_value(audits, "first-contentful-paint"),
        tbt_ms=numeric_value(audits, "total-blocking-time"), cls=numeric_value(audits, "cumulative-layout-shift"),
        speed_index_ms=numeric_value(audits, "speed-index"), server_ms=numeric_value(audits, "server-response-time"),
        image_savings_ms=_lcp_savings(audits, IMAGE_SAVINGS), script_savings_ms=_lcp_savings(audits, SCRIPT_SAVINGS),
        server_savings_ms=_lcp_savings(audits, SERVER_SAVINGS),
    )


def _mobile(audits: dict[str, Any]) -> MobileFacts:
    items = audit_items(audits, "viewport-insight")
    snippet = _snippet(items[0]) if items else None
    return MobileFacts(audit_state(audits.get("viewport-insight")), snippet,
                       audit_state(audits.get("target-size")), audit_state(audits.get("meta-viewport")))


def _snippet(item: dict[str, Any]) -> str | None:
    node = item.get("node")
    value = node.get("snippet") if isinstance(node, dict) else None
    return value if isinstance(value, str) else None


def _savings_by_url(audits: dict[str, Any]) -> _Savings:
    by_url: dict[str, int] = {}
    by_stripped: dict[str, int] = {}
    for item in audit_items(audits, "image-delivery-insight"):
        url = _url(item)
        if not url:
            continue
        wasted = int(as_number(item.get("wastedBytes")))
        by_url[url] = wasted
        by_stripped[strip_params(url)] = wasted
    return _Savings(by_url, by_stripped)


def _images(audits: dict[str, Any], savings: _Savings) -> ImageFacts:
    requests = [item for item in audit_items(audits, "network-requests") if item.get("resourceType") == IMAGE_REQUEST_TYPE]
    page_bytes = numeric_value(audits, "total-byte-weight")
    blurry, skipped = _blurry(audits)
    return ImageFacts(page_bytes=None if page_bytes is None else int(page_bytes),
                      image_bytes=_summary_bytes(audits).get(IMAGE_SUMMARY_TYPE),
                      heaviest=_heaviest(requests, savings, HEAVIEST_IMAGES), compress_ratio=_compress_ratio(audits),
                      stretched=_names(audit_items(audits, "image-aspect-ratio")), blurry=blurry,
                      blurry_small_skipped=skipped)


def _names(items: list[dict[str, Any]]) -> tuple[str, ...]:
    """Имена картинок без повторов: одна картинка с разными параметрами адреса — один раз."""
    return tuple(dict.fromkeys(name for item in items if (name := file_name(_url(item)))))


def _blurry(audits: dict[str, Any]) -> tuple[tuple[str, ...], int]:
    items = audit_items(audits, "image-size-responsive")
    big = [item for item in items if _min_displayed_side(item) >= BLURRY_MIN_SIDE_PX]
    return _names(big), len(items) - len(big)


def _min_displayed_side(item: dict[str, Any]) -> int:
    """Размер на экране из displayedSize («381 x 259»); нет его — 0: не считаем (ложную тревогу не показываем)."""
    found = DISPLAYED_SIZE.search(str(item.get("displayedSize", "")))
    return min(int(found.group(1)), int(found.group(2))) if found else 0


def _heaviest(requests: list[dict[str, Any]], savings: _Savings, limit: int) -> tuple[FileWeight, ...]:
    downloaded = [item for item in requests if _is_real_download(item)]
    ordered = sorted(downloaded, key=lambda item: as_number(item.get("transferSize")), reverse=True)[:limit]
    return tuple(_file_weight(item, savings) for item in ordered)


def _is_real_download(item: dict[str, Any]) -> bool:
    """data: URIs и обнулённые запросы (кэш, ошибка) не забирали сеть — незачем показывать их «весом» (ТЗ, 5.5)."""
    return _url(item).startswith(HTTP_URL_PREFIXES) and as_number(item.get("transferSize")) >= MIN_TRANSFER_BYTES


def _file_weight(item: dict[str, Any], savings: _Savings) -> FileWeight:
    url = _url(item)
    return FileWeight(file_name(url), str(item.get("resourceType", "")), int(as_number(item.get("transferSize"))),
                      savings.get(url))


def _compress_ratio(audits: dict[str, Any]) -> int | None:
    items = audit_items(audits, "image-delivery-insight")
    total = sum(as_number(item.get("totalBytes")) for item in items)
    after = total - sum(as_number(item.get("wastedBytes")) for item in items)
    return math.floor(total / after) if total and after > 0 else None


def _summary_bytes(audits: dict[str, Any]) -> dict[str, int]:
    return {str(item.get("resourceType")): int(as_number(item.get("transferSize")))
            for item in audit_items(audits, "resource-summary")}


def _post(audits: dict[str, Any], savings: _Savings, categories: Any) -> PostNumbers:
    summary = _summary_bytes(audits)
    total = [item for item in audit_items(audits, "resource-summary") if item.get("resourceType") == TOTAL_SUMMARY_TYPE]
    return PostNumbers(
        requests=int(as_number(total[0].get("requestCount"))) if total else None,
        bytes_by_type=tuple((kind, summary[kind]) for kind in SUMMARY_TYPES if kind in summary),
        heaviest_files=_heaviest(audit_items(audits, "network-requests"), savings, HEAVIEST_FILES),
        third_parties=_third_parties(audits),
        category_scores=_category_scores(categories),
    )


def _category_scores(categories: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(categories, dict):
        return ()
    scores = []
    for name in CATEGORY_NAMES:
        entry = categories.get(name)
        score = entry.get("score") if isinstance(entry, dict) else None
        if is_number(score):
            scores.append((name, round(score * PERCENT)))
    return tuple(scores)


def _third_parties(audits: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    rows = []
    for item in audit_items(audits, "third-parties-insight"):
        name = _entity_name(item.get("entity"))
        if name:
            rows.append((name, int(as_number(item.get("transferSize")))))
    return tuple(rows)


def _entity_name(entity: Any) -> str | None:
    if isinstance(entity, dict):
        entity = entity.get("text")
    return entity if isinstance(entity, str) and entity else None
