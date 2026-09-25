"""Служебные сообщения сайт-чекера (ТЗ, 7.4): статус, очередь, отказы и ошибки. Все — rich с шапкой."""
from bot.core import rich
from bot.core.commands import Brand, simple_message
from bot.core.i18n import Lang, Texts

FALLBACK_CODE = "measure_failed"
FAILURE_CODES = (
    "not_a_link", "not_text", "bad_address", "private_address", "unreachable_dns", "unreachable", "timeout", "blocked",
    "not_found", "server_error", "not_html", "measure_failed", "service_down", "limit_global", "queue_full",
    "interrupted",
)


def _simple(texts: Texts, lang: Lang, brand: Brand, key: str, **params: object) -> dict:
    return simple_message(brand, texts.get(lang, key, **params))


def checking(texts: Texts, lang: Lang, brand: Brand, display: str) -> dict:
    return _simple(texts, lang, brand, "checking", site=display)


def queued(texts: Texts, lang: Lang, brand: Brand, ahead: int, minutes: int) -> dict:
    return _simple(texts, lang, brand, "queued", sites=texts.count(lang, ahead, "site"), minutes=minutes)


def again(texts: Texts, lang: Lang, brand: Brand) -> dict:
    return _simple(texts, lang, brand, "again")


def busy(texts: Texts, lang: Lang, brand: Brand, display: str) -> dict:
    return _simple(texts, lang, brand, "busy", site=display)


def limit_user(texts: Texts, lang: Lang, brand: Brand, limit: int, hours: int) -> dict:
    return _simple(texts, lang, brand, "limit_user", checks=texts.count(lang, limit, "check"), hours=hours)


def failure(texts: Texts, lang: Lang, brand: Brand, code: str, **params: object) -> dict:
    key = code if code in FAILURE_CODES else FALLBACK_CODE
    return _simple(texts, lang, brand, key, **params)


def social(texts: Texts, lang: Lang, brand: Brand, platform: str) -> dict:
    link = rich.dm_link(brand.dm_username, texts.get(lang, "order_prefill"))
    button = rich.pill_url(texts.get(lang, "discuss_button"), link, rich.STYLE_PRIMARY)
    return rich.message([rich.header(brand.section), rich.paragraph(texts.get(lang, "social", platform=platform)),
                         rich.pills(button)])
