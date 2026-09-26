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


def _simple(texts: Texts, lang: Lang, key: str, **params: object) -> dict:
    return simple_message(texts, lang, texts.get(lang, key, **params))


def checking(texts: Texts, lang: Lang, display: str) -> dict:
    return _simple(texts, lang, "checking", site=display)


def queued(texts: Texts, lang: Lang, ahead: int, minutes: int) -> dict:
    key = "queued_one" if ahead == 1 else "queued"  # en "are" needs "is" for exactly one site ahead
    return _simple(texts, lang, key, sites=texts.count(lang, ahead, "site"), minutes=minutes)


def again(texts: Texts, lang: Lang) -> dict:
    return _simple(texts, lang, "again")


def busy(texts: Texts, lang: Lang, display: str) -> dict:
    return _simple(texts, lang, "busy", site=display)


def limit_user(texts: Texts, lang: Lang, limit: int, hours: int) -> dict:
    return _simple(texts, lang, "limit_user", checks=texts.count(lang, limit, "check"), hours=hours)


def failure(texts: Texts, lang: Lang, code: str, **params: object) -> dict:
    key = code if code in FAILURE_CODES else FALLBACK_CODE
    return _simple(texts, lang, key, **params)


def social(texts: Texts, lang: Lang, brand: Brand, platform: str) -> tuple[dict, dict]:
    link = rich.dm_link(brand.dm_username, texts.get(lang, "order_prefill"))
    button = rich.button_url(texts.get(lang, "discuss_button"), link, rich.STYLE_PRIMARY)
    message = rich.message([rich.header(texts.get(lang, "header_section")),
                            rich.paragraph(texts.get(lang, "social", platform=platform))])
    return message, rich.keyboard(button)
