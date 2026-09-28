"""«Подробные замеры — видите только вы» — только владельцу (ТЗ, 7.5, Р8): все замеры таблицей в раскрывающемся
блоке (заголовок переименован в задаче 23b по просьбе владельца — прежнее название «Цифры для поста» было
непонятным; ключи `post_*` и имя модуля не менялись)."""
from datetime import datetime, timedelta, timezone

from bot.core import rich
from bot.core.i18n import Lang, Texts
from bot.site_check.audits import AuditState, strip_params
from bot.site_check.head_tags import HeadTags
from bot.site_check.lighthouse import CATEGORY_NAMES, FileWeight, PageFacts, SpeedFacts
from bot.site_check.page_fetch import ImageCheck, ImageState, PagePreview
from bot.site_check.readability_block import ReadabilityFacts
from bot.site_check.report_blocks import quote
from bot.site_check.search_block import SearchFacts
from bot.site_check.verdict import SecurityFacts

OWNER_TIMEZONE = timezone(timedelta(hours=7))  # Бангкок, Дананг
TIME_FORMAT = "%d.%m.%Y %H:%M"
CLS_FORMAT = "{:.2f}"
NAMES_SEPARATOR = ", "
REDIRECT_CHAIN = "{requested} → {final}"
POST_TITLE_CHARS = 80          # ТЗ, 7.5
POST_DESCRIPTION_CHARS = 100
NO_VALUE = "—"
LINES_JOIN = ", "
CATEGORY_JOIN = " / "
ROBOTS_STATE_KEYS = {AuditState.PASSED: "post_robots_ok", AuditState.NOT_APPLICABLE: "post_robots_none"}


def post_numbers(texts: Texts, lang: Lang, page: PageFacts, security: SecurityFacts, measured_at: datetime,
                 preview: PagePreview | None = None) -> dict:
    rows = [[texts.get(lang, "post_what"), texts.get(lang, "post_value")]]
    rows += _speed_rows(texts, lang, page.speed)
    rows += _weight_rows(texts, lang, page)
    rows += [_file_row(texts, lang, item) for item in page.post.heaviest_files]
    rows += [[texts.get(lang, "post_third_party", name=name), texts.size(lang, size)]
             for name, size in page.post.third_parties]
    rows += _search_rows(texts, lang, page.search, preview)
    rows += _preview_rows(texts, lang, preview)
    rows += _contacts_rows(texts, lang, preview)
    rows += _readability_rows(texts, lang, page.readability, preview)
    rows += _category_rows(texts, lang, page.post.category_scores)
    rows.append(_screenshot_row(texts, lang, page))
    rows += _meta_rows(texts, lang, page, security, measured_at)
    return rich.details(texts.get(lang, "post_numbers_title"), [rich.table(rows)])


def _speed_rows(texts: Texts, lang: Lang, speed: SpeedFacts) -> list[list[str]]:
    timings = (("post_lcp", speed.lcp_ms), ("post_fcp", speed.fcp_ms), ("post_tbt", speed.tbt_ms),
               ("post_speed_index", speed.speed_index_ms), ("post_server", speed.server_ms))
    rows = [[texts.get(lang, key), texts.seconds(lang, value)] for key, value in timings if value is not None]
    if speed.cls is not None:
        rows.append([texts.get(lang, "post_cls"), CLS_FORMAT.format(speed.cls)])
    return rows


def _weight_rows(texts: Texts, lang: Lang, page: PageFacts) -> list[list[str]]:
    rows = [[texts.get(lang, f"post_bytes_{kind}"), texts.size(lang, size)] for kind, size in page.post.bytes_by_type]
    if page.post.requests is not None:
        rows.append([texts.get(lang, "post_requests"), str(page.post.requests)])
    return rows


def _file_row(texts: Texts, lang: Lang, item: FileWeight) -> list[str]:
    size = texts.size(lang, item.bytes)
    if item.savings_bytes:
        size = texts.get(lang, "post_savings", size=size, savings=texts.size(lang, item.savings_bytes))
    return [f"{item.name} ({item.kind})", size]


def _search_rows(texts: Texts, lang: Lang, search: SearchFacts | None,
                 preview: PagePreview | None) -> list[list[str]]:
    if search is None:
        return []
    rows = [[texts.get(lang, "post_indexing"), _indexing_text(texts, lang, search)],
            [texts.get(lang, "post_robots"), _robots_text(texts, lang, search)]]
    return rows + _head_rows(texts, lang, preview.head if preview else None)


def _indexing_text(texts: Texts, lang: Lang, search: SearchFacts) -> str:
    if search.crawlable is AuditState.FAILED:
        return texts.get(lang, "post_indexing_closed", source=search.block_snippet or NO_VALUE)
    return texts.get(lang, "post_indexing_open") if search.crawlable is AuditState.PASSED else NO_VALUE


def _robots_text(texts: Texts, lang: Lang, search: SearchFacts) -> str:
    if search.robots_txt_errors:
        return texts.get(lang, "post_robots_errors", lines=LINES_JOIN.join(search.robots_txt_errors))
    if search.robots_txt is AuditState.FAILED and search.robots_txt_status:
        return texts.get(lang, "post_robots_status", status=search.robots_txt_status)
    key = ROBOTS_STATE_KEYS.get(search.robots_txt)
    return texts.get(lang, key) if key else NO_VALUE


def _head_rows(texts: Texts, lang: Lang, head: HeadTags | None) -> list[list[str]]:
    if head is None:
        return []
    rows = [_quoted_row(texts, lang, "post_title", head.title, POST_TITLE_CHARS),
            _quoted_row(texts, lang, "post_description", head.description, POST_DESCRIPTION_CHARS)]
    if head.canonical:
        rows.append([texts.get(lang, "post_canonical"), strip_params(head.canonical)])
    return [row for row in rows if row]


def _quoted_row(texts: Texts, lang: Lang, key: str, text: str | None, limit: int) -> list[str]:
    if not text:
        return []
    return [texts.get(lang, key, chars=texts.count(lang, len(text), "char")), quote(text, limit)]


def _preview_rows(texts: Texts, lang: Lang, preview: PagePreview | None) -> list[list[str]]:
    rows = [[texts.get(lang, "post_own_fetch"), _own_fetch_text(texts, lang, preview)]]
    if preview is None or preview.head is None:
        return rows
    title = preview.head.preview_title
    rows.append([texts.get(lang, "post_preview_title"), quote(title, POST_TITLE_CHARS) if title else NO_VALUE])
    rows.append([texts.get(lang, "post_preview_image"), _image_text(texts, lang, preview.image)])
    return rows


def _own_fetch_text(texts: Texts, lang: Lang, preview: PagePreview | None) -> str:
    if preview is None:
        return texts.get(lang, "post_own_fetch_none")
    if preview.failure:
        return texts.get(lang, f"post_own_fetch_{preview.failure}", status=preview.status or NO_VALUE)
    return texts.get(lang, "post_own_fetch_ok", status=preview.status, size=texts.size(lang, preview.head_bytes),
                     seconds=texts.seconds(lang, preview.elapsed_ms))


def _image_text(texts: Texts, lang: Lang, image: ImageCheck | None) -> str:
    if image is None:
        return texts.get(lang, "post_preview_image_none")
    if image.state is ImageState.OK:
        size = texts.size(lang, image.size_bytes) if image.size_bytes else NO_VALUE
        return texts.get(lang, "post_preview_image_ok", name=image.name, kind=image.content_type, size=size)
    return texts.get(lang, f"post_preview_image_{image.state}", status=image.status or NO_VALUE,
                     kind=image.content_type or NO_VALUE)


def _contacts_rows(texts: Texts, lang: Lang, preview: PagePreview | None) -> list[list[str]]:
    facts = preview.contacts if preview else None
    if facts is None:
        return []
    counts = texts.get(lang, "post_contacts_value", calls=facts.call_links, short=len(facts.short_call_links),
                       text=facts.text_phones, forms=facts.personal_forms)
    read = "post_page_whole" if facts.complete else "post_page_cut"
    return [[texts.get(lang, "post_page_read"), texts.get(lang, read)],
            [texts.get(lang, "post_contacts"), counts],
            [texts.get(lang, "post_services"), NAMES_SEPARATOR.join(sorted(facts.markers)) or NO_VALUE],
            [texts.get(lang, "post_policy"), texts.get(lang, "post_yes" if facts.policy_link else "post_no")]]


def _readability_rows(texts: Texts, lang: Lang, facts: ReadabilityFacts | None,
                      preview: PagePreview | None) -> list[list[str]]:
    if facts is None:
        return []
    alt = texts.get(lang, "post_alt_value", missing=facts.alt_missing_count, icons=facts.alt_icons_skipped)
    return [[texts.get(lang, "post_contrast"), str(facts.contrast_count)],
            [texts.get(lang, "post_alt"), alt],
            [texts.get(lang, "post_unnamed"), str(facts.unnamed_controls)],
            [texts.get(lang, "post_unlabeled"), str(facts.unlabeled_fields)],
            [texts.get(lang, "post_lang"), _lang_text(texts, lang, facts, preview)]]


def _lang_text(texts: Texts, lang: Lang, facts: ReadabilityFacts, preview: PagePreview | None) -> str:
    code = preview.head.lang if preview and preview.head else None
    if code:
        return code
    return texts.get(lang, "post_lang_missing") if facts.lang is AuditState.FAILED else NO_VALUE


def _category_rows(texts: Texts, lang: Lang, scores: tuple[tuple[str, int], ...]) -> list[list[str]]:
    if not scores:
        return []
    values = dict(scores)
    shown = CATEGORY_JOIN.join(str(values.get(name, NO_VALUE)) for name in CATEGORY_NAMES)
    return [[texts.get(lang, "post_categories"), shown]]


def _screenshot_row(texts: Texts, lang: Lang, page: PageFacts) -> list[str]:
    value = texts.size(lang, len(page.screenshot)) if page.screenshot else texts.get(lang, "post_screenshot_none")
    return [texts.get(lang, "post_screenshot"), value]


def _meta_rows(texts: Texts, lang: Lang, page: PageFacts, security: SecurityFacts,
               measured_at: datetime) -> list[list[str]]:
    rows = []
    cert = next((facts.cert for facts in reversed(security.tls) if facts.cert), None)
    if cert:
        value = texts.get(lang, "post_cert_value", issuer=cert.issuer, start=texts.date(lang, cert.not_before.date()),
                          end=texts.date(lang, cert.not_after.date()), names=NAMES_SEPARATOR.join(cert.names))
        rows.append([texts.get(lang, "post_cert"), value])
    domain = security.domain
    rows.append([texts.get(lang, "post_domain"),
                 texts.get(lang, "post_domain_value", domain=domain.domain, date=texts.date(lang, domain.until),
                           source=domain.source) if domain else texts.get(lang, "post_domain_none")])
    final_url = strip_params(page.final_url)
    rows.append([texts.get(lang, "post_final_url"), final_url])
    rows += _redirect_row(texts, lang, page.requested_url, final_url)
    rows.append([texts.get(lang, "post_lighthouse"), page.lighthouse_version])
    rows.append([texts.get(lang, "post_measured_at"), measured_at.astimezone(OWNER_TIMEZONE).strftime(TIME_FORMAT)])
    return rows


def _redirect_row(texts: Texts, lang: Lang, requested_url: str, final_url: str) -> list[list[str]]:
    """Цепочка переадресаций (ТЗ, 7.5): что просили измерить и куда PageSpeed в итоге попал."""
    if not requested_url or requested_url == final_url:
        return []
    chain = REDIRECT_CHAIN.format(requested=requested_url, final=final_url)
    return [[texts.get(lang, "post_redirect"), chain]]
