"""Вся страница → способы связи, формы, телефоны текстом, политика персональных данных, сервисы (ТЗ, 5.10).
Без сети. Телефоны текстом ищет phonenumberslite (регион RU по умолчанию, строгость VALID — номер должен быть
настоящим по плану нумерации) — только в видимом тексте: не в <script>, <style>, <noscript>, <template>.
"""
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote

import phonenumbers

from bot.site_check.html_guard import guard_html
from bot.site_check.markers import find_markers

DEFAULT_REGION = "RU"
FULL_NUMBER_DIGITS = 10       # номер с кодом города или оператора (ТЗ, 5.10)
LOCAL_NUMBER_MIN_DIGITS = 5   # короче — служебные номера (112, 900): не кнопка «без кода»
TEXT_SCAN_MAX_CHARS = 200_000
HIDDEN_TAGS = frozenset({"script", "style", "noscript", "template"})
FIELD_TAGS = frozenset({"input", "textarea"})
PERSONAL_FIELD_TYPES = frozenset({"tel", "email"})
FIELD_TEXT_ATTRIBUTES = ("name", "placeholder", "aria-label", "id")
PERSONAL_FIELD_WORDS = re.compile(r"имя|name|phone|\btel|телефон|e-?mail|почт", re.IGNORECASE)
POLICY_WORDS = re.compile(r"политик|персональн|конфиденциальн|privacy|policy|politik|soglasi", re.IGNORECASE)
WHATSAPP = re.compile(r"^(?:https?://)?(?:wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com)/|^whatsapp:", re.IGNORECASE)
TELEGRAM = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/|^tg:", re.IGNORECASE)
TAP_BLOCK = re.compile(r"telephone\s*=\s*no", re.IGNORECASE)
DIGIT = re.compile(r"\d")
CALL_PREFIX = "tel:"
VIBER_PREFIX = "viber:"
MAIL_PREFIX = "mailto:"
FORMAT_DETECTION = "format-detection"
TEXT_JOIN = " "
NODES_JOIN = "\n"  # границы элементов: иначе два номера подряд phonenumbers склеит в один и отбросит


@dataclass(frozen=True)
class ContactFacts:
    call_links: int                     # ссылки tel: с полным номером
    short_call_links: tuple[str, ...]   # tel: без кода — как записаны на сайте
    text_phones: int                    # разных номеров в видимом тексте
    tap_blocked: bool                   # format-detection telephone=no: iPhone тоже не сделает номер нажимаемым
    whatsapp: bool
    telegram: bool
    viber: bool
    email: bool
    personal_forms: int                 # формы с полем имени, телефона или почты
    policy_link: bool                   # ссылка на политику персональных данных где-то на странице
    markers: frozenset[str]             # сервисы по коду страницы (markers.py)
    complete: bool                      # страница прочитана целиком (не обрезана PAGE_MAX_BYTES)


def parse_contacts(html: str, complete: bool) -> ContactFacts:
    html, whole = guard_html(html)  # задача 33, C1: гигантский тег не отдаём html.parser целиком
    complete = complete and whole
    page = _PageParser()
    page.feed(html)
    page.close()
    page.close_link()  # ссылка, не закрытая до конца страницы, — её адрес тоже нужен
    hrefs = [href for href, _ in page.links]
    calls = [unquote(href[len(CALL_PREFIX):]) for href in hrefs if href.lower().startswith(CALL_PREFIX)]
    return ContactFacts(
        call_links=sum(1 for call in calls if _digits(call) >= FULL_NUMBER_DIGITS),
        short_call_links=tuple(dict.fromkeys(call for call in calls
                                             if LOCAL_NUMBER_MIN_DIGITS <= _digits(call) < FULL_NUMBER_DIGITS)),
        text_phones=_text_phones(NODES_JOIN.join(page.text)), tap_blocked=page.tap_blocked,
        whatsapp=_any(hrefs, WHATSAPP), telegram=_any(hrefs, TELEGRAM),
        viber=any(href.lower().startswith(VIBER_PREFIX) for href in hrefs),
        email=any(href.lower().startswith(MAIL_PREFIX) for href in hrefs), personal_forms=page.personal_forms,
        policy_link=any(POLICY_WORDS.search(f"{href} {text}") for href, text in page.links),
        markers=find_markers(html), complete=complete)


def _digits(value: str) -> int:
    return len(DIGIT.findall(value))


def _any(hrefs: list[str], pattern: re.Pattern) -> bool:
    return any(pattern.search(href) for href in hrefs)


def _text_phones(text: str) -> int:
    matches = phonenumbers.PhoneNumberMatcher(text[:TEXT_SCAN_MAX_CHARS], DEFAULT_REGION)
    return len({phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164) for match in matches})


def _is_personal(values: dict[str, str]) -> bool:
    if values.get("type", "").lower() in PERSONAL_FIELD_TYPES:
        return True
    return any(PERSONAL_FIELD_WORDS.search(values.get(name, "")) for name in FIELD_TEXT_ATTRIBUTES)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []  # (адрес, текст ссылки)
        self.text: list[str] = []
        self.personal_forms = 0
        self.tap_blocked = False
        self._hidden = 0
        self._link: tuple[str, list[str]] | None = None
        self._form_personal: bool | None = None  # None — не внутри формы

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag in HIDDEN_TAGS:
            self._hidden += 1
        elif tag == "a":
            self._link = (values.get("href", "").strip(), [])
        elif tag == "form":
            self._form_personal = False
        elif tag in FIELD_TAGS and self._form_personal is not None:
            self._form_personal = self._form_personal or _is_personal(values)
        elif tag == "meta" and values.get("name", "").lower() == FORMAT_DETECTION:
            self.tap_blocked = self.tap_blocked or bool(TAP_BLOCK.search(values.get("content", "")))

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN_TAGS and self._hidden:
            self._hidden -= 1
        elif tag == "a":
            self.close_link()
        elif tag == "form" and self._form_personal is not None:
            self.personal_forms += int(self._form_personal)
            self._form_personal = None

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        self.text.append(data)
        if self._link is not None:
            self._link[1].append(data)

    def close_link(self) -> None:
        if self._link is not None:
            self.links.append((self._link[0], TEXT_JOIN.join(self._link[1])))
            self._link = None
