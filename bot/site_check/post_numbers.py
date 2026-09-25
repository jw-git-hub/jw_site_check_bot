"""«Цифры для поста» — только владельцу (ТЗ, 7.5, Р8): все замеры таблицей в раскрывающемся блоке."""
from datetime import datetime, timedelta, timezone

from bot.core import rich
from bot.core.i18n import Lang, Texts
from bot.site_check.lighthouse import FileWeight, PageFacts, SpeedFacts, strip_params
from bot.site_check.verdict import SecurityFacts

OWNER_TIMEZONE = timezone(timedelta(hours=7))  # Бангкок, Дананг
TIME_FORMAT = "%d.%m.%Y %H:%M"
CLS_FORMAT = "{:.2f}"
NAMES_SEPARATOR = ", "
REDIRECT_CHAIN = "{requested} → {final}"


def post_numbers(texts: Texts, lang: Lang, page: PageFacts, security: SecurityFacts, measured_at: datetime) -> dict:
    rows = [[texts.get(lang, "post_what"), texts.get(lang, "post_value")]]
    rows += _speed_rows(texts, lang, page.speed)
    rows += _weight_rows(texts, lang, page)
    rows += [_file_row(texts, lang, item) for item in page.post.heaviest_files]
    rows += [[texts.get(lang, "post_third_party", name=name), texts.size(lang, size)]
             for name, size in page.post.third_parties]
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


def _meta_rows(texts: Texts, lang: Lang, page: PageFacts, security: SecurityFacts,
               measured_at: datetime) -> list[list[str]]:
    rows = []
    cert = next((facts.cert for facts in reversed(security.tls) if facts.cert), None)
    if cert:
        value = texts.get(lang, "post_cert_value", issuer=cert.issuer, start=texts.date(lang, cert.not_before.date()),
                          end=texts.date(lang, cert.not_after.date()), names=NAMES_SEPARATOR.join(cert.names))
        rows.append([texts.get(lang, "post_cert"), value])
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
