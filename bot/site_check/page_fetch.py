"""Своя загрузка начала страницы и картинки превью (ТЗ, 5.6–5.7; 10.1: С3–С5, С7, С14; Р16).

- Адрес каждого шага — итоговый адрес PageSpeed, каждый переход, og:image — сначала проходит правила ввода
  человека (url_input.own_request_target): aiohttp не зовёт резолвер для IP-литералов, и переход на
  http://127.0.0.1/ иначе обошёл бы защиту.
- Сеть — только через отдельную сессию со своим резолвером (GuardedResolver): aiohttp подключается ровно к тем
  адресам, которые проверил AddressGuard, имя сайта идёт только в SNI и Host — окна DNS rebinding нет.
- Переходы — вручную, не больше MAX_OWN_REDIRECTS. У страницы — только 200, HTML, без сжатия, не больше
  PAGE_MAX_BYTES (в версии 1.1 — только до </head>, STOP_AT_HEAD); у картинки — только статус и заголовки.
- Наружу ничего не падает: неудача — причина в PagePreview.failure или ImageState.UNKNOWN.
"""
import asyncio
import codecs
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from urllib.parse import urljoin

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from bot.site_check.audits import file_name, strip_params
from bot.site_check.head_tags import HeadTags, parse_head
from bot.site_check.net_guard import AddressGuard
from bot.site_check.page_contacts import ContactFacts, parse_contacts
from bot.site_check.probe import GUARD_REFUSALS
from bot.site_check.tls_check import REDIRECT_STATUSES, USER_AGENT
from bot.site_check.url_input import Target, own_request_target

PAGE_MAX_BYTES = 2 * 1024 * 1024  # ТЗ, С5, версия 1.2: вся страница
STOP_AT_HEAD = False              # контакты — по всей странице (ТЗ, 5.10)
READ_CHUNK_BYTES = 64 * 1024
PAGE_TIMEOUT_SECONDS = 8         # вся загрузка страницы с переходами; внешний срок 12 с — в pipeline.py
IMAGE_TIMEOUT_SECONDS = 4
CONNECT_TIMEOUT_SECONDS = 4
MAX_OWN_REDIRECTS = 3            # ТЗ, С4
HTTP_OK = 200
SUCCESS_STATUSES = range(200, 300)
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
IDENTITY_ENCODINGS = frozenset({"", "identity"})
SVG_TYPE = "image/svg+xml"
IMAGE_TYPE_PREFIX = "image/"
FULL_URL_PREFIXES = ("http://", "https://")
HEAD_END = re.compile(rb"</head\s*>|<body[\s>]", re.IGNORECASE)
META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
CHARSET_SNIFF_BYTES = 4096
FALLBACK_CHARSET = "utf-8"
MS_IN_SECOND = 1000
CONTENT_TYPE_HEADER = "Content-Type"
CONTENT_ENCODING_HEADER = "Content-Encoding"
LOCATION_HEADER = "Location"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
                   "Accept-Encoding": "identity"}
# Сбои сети и кривые ответы: aiohttp.ClientError (и отказ резолвера), OSError, битые заголовки (UnicodeError).
NETWORK_ERRORS = (aiohttp.ClientError, OSError, ValueError)
IMAGE_CHECK_ERRORS = (TimeoutError, *NETWORK_ERRORS)


class FetchFailure(StrEnum):
    REFUSED = "refused"          # адрес шага не прошёл правила П3/С1 — сеть не трогали
    NETWORK = "network"          # не соединились: отказ защиты адреса, обрыв, сертификат
    TIMEOUT = "timeout"
    REDIRECTS = "redirects"      # больше MAX_OWN_REDIRECTS переходов или переход без Location
    STATUS = "status"            # ответ не 200 — например, заглушка защиты от ботов
    NOT_HTML = "not_html"
    COMPRESSED = "compressed"    # сервер сжал ответ вопреки Accept-Encoding: identity — не читаем (С5)


class ImageState(StrEnum):
    OK = "ok"
    BROKEN = "broken"            # 4xx/5xx
    NOT_IMAGE = "not_image"      # отвечает, но не картинкой
    SVG = "svg"
    RELATIVE = "relative"        # в HTML адрес без http:// или https:// — Telegram такую не показывает (ТЗ, 20)
    UNKNOWN = "unknown"          # не проверить: сеть, срок, адрес не прошёл защиту — не находка


@dataclass(frozen=True)
class Answer:
    """Один ответ без переходов. body — только начало страницы HTML; у переходов и картинок пусто."""
    status: int
    content_type: str = ""
    charset: str | None = None
    content_encoding: str = ""
    location: str | None = None
    size_bytes: int | None = None
    body: bytes = b""
    complete: bool = True        # тело прочитано, сколько просили; False — обрезано PAGE_MAX_BYTES


Sender = Callable[[str, bool], Awaitable[Answer]]


@dataclass(frozen=True)
class Landing:
    url: str
    answer: Answer | None
    failure: FetchFailure | None


@dataclass(frozen=True)
class ImageCheck:
    name: str
    state: ImageState
    status: int | None = None
    content_type: str = ""
    size_bytes: int | None = None


@dataclass(frozen=True)
class PagePreview:
    url: str                     # где оказалась своя загрузка, без параметров
    head: HeadTags | None        # None — загрузка не удалась, причина в failure
    failure: FetchFailure | None
    status: int | None
    head_bytes: int
    elapsed_ms: float
    image: ImageCheck | None     # None — в head нет картинки превью или head не получен
    html: str = ""               # декодированное прочитанное: разбор head, в версии 1.2 — и контактов (задача 35)
    complete: bool = False       # прочитано всё, что просили, — а не обрезано PAGE_MAX_BYTES
    contacts: ContactFacts | None = None  # None — загрузка не удалась (page_contacts.py, задача 35)


def is_readable_page(answer: Answer) -> bool:
    """Страница, которую читаем (ТЗ, С5): 200, HTML, без сжатия."""
    return (answer.status == HTTP_OK and answer.content_type in HTML_TYPES
            and answer.content_encoding in IDENTITY_ENCODINGS)


async def follow(send: Sender, url: str, read_head: bool) -> Landing:
    """Переходы вручную (ТЗ, С4): каждый адрес — через правила П3/С1 до сети, не больше MAX_OWN_REDIRECTS шагов."""
    for _step in range(MAX_OWN_REDIRECTS + 1):
        target = own_request_target(url)
        if not isinstance(target, Target):
            return Landing(url, None, FetchFailure.REFUSED)
        answer = await send(target.url, read_head)
        if answer.status not in REDIRECT_STATUSES:
            return Landing(target.url, answer, None)
        if not answer.location:
            return Landing(target.url, answer, FetchFailure.REDIRECTS)
        url = urljoin(target.url, answer.location)
    return Landing(url, None, FetchFailure.REDIRECTS)


async def fetch_page(send: Sender, url: str) -> Landing:
    try:
        landing = await asyncio.wait_for(follow(send, url, read_head=True), PAGE_TIMEOUT_SECONDS)
    except TimeoutError:  # раньше NETWORK_ERRORS: TimeoutError — подкласс OSError
        return Landing(url, None, FetchFailure.TIMEOUT)
    except NETWORK_ERRORS:
        return Landing(url, None, FetchFailure.NETWORK)
    return landing if landing.failure else _page_failure(landing)


def _page_failure(landing: Landing) -> Landing:
    answer = landing.answer
    if answer.status != HTTP_OK:
        return replace(landing, failure=FetchFailure.STATUS)
    if answer.content_type not in HTML_TYPES:
        return replace(landing, failure=FetchFailure.NOT_HTML)
    if answer.content_encoding not in IDENTITY_ENCODINGS:
        return replace(landing, failure=FetchFailure.COMPRESSED)
    return landing


def decode_page(body: bytes, charset: str | None) -> str:
    """Кодировка: из Content-Type, иначе из <meta charset> в начале страницы, иначе UTF-8 (ТЗ, 16)."""
    for name in (charset, _meta_charset(body)):
        if name and _known_codec(name):
            return body.decode(name, errors="replace")
    return body.decode(FALLBACK_CHARSET, errors="replace")


def _meta_charset(body: bytes) -> str | None:
    found = META_CHARSET.search(body[:CHARSET_SNIFF_BYTES])
    return found.group(1).decode("ascii") if found else None


def _known_codec(name: str) -> bool:
    try:
        codecs.lookup(name)
    except LookupError:
        return False
    return True


async def check_image(send: Sender, raw: str) -> ImageCheck:
    """Картинка превью (ТЗ, 5.7): адрес не полностью — находка без сети; иначе только статус и заголовки."""
    name = file_name(raw) or raw
    if not raw.lower().startswith(FULL_URL_PREFIXES):
        return ImageCheck(name, ImageState.RELATIVE)
    try:
        landing = await asyncio.wait_for(follow(send, raw, read_head=False), IMAGE_TIMEOUT_SECONDS)
    except IMAGE_CHECK_ERRORS:
        return ImageCheck(name, ImageState.UNKNOWN)
    if landing.failure or landing.answer is None:
        return ImageCheck(name, ImageState.UNKNOWN)
    answer = landing.answer
    return ImageCheck(name, _image_state(answer), answer.status, answer.content_type, answer.size_bytes)


def _image_state(answer: Answer) -> ImageState:
    if answer.status not in SUCCESS_STATUSES:
        return ImageState.BROKEN
    if answer.content_type == SVG_TYPE:
        return ImageState.SVG
    return ImageState.OK if answer.content_type.startswith(IMAGE_TYPE_PREFIX) else ImageState.NOT_IMAGE


class PreviewLoader:
    """Своя загрузка для «Поиска» и «Ссылки в мессенджерах» (ТЗ, 5.6–5.7): head страницы и проверка её картинки."""

    def __init__(self, send: Sender, monotonic: Callable[[], float] = time.monotonic):
        self._send = send
        self._monotonic = monotonic

    async def load(self, url: str) -> PagePreview:
        started = self._monotonic()
        landing = await fetch_page(self._send, url)
        elapsed_ms = (self._monotonic() - started) * MS_IN_SECOND
        if landing.failure:
            status = landing.answer.status if landing.answer else None
            return PagePreview(strip_params(landing.url), None, landing.failure, status, 0, elapsed_ms, None)
        answer = landing.answer
        html, head, contacts = await asyncio.to_thread(_parse_preview, answer.body, answer.charset, answer.complete)
        image = await check_image(self._send, head.preview_image) if head.preview_image else None
        return PagePreview(strip_params(landing.url), head, None, answer.status, len(answer.body), elapsed_ms, image,
                           html, answer.complete, contacts)


def _parse_preview(body: bytes, charset: str | None, complete: bool) -> tuple[str, HeadTags, ContactFacts]:
    """Decode + parse_head + parse_contacts вместе, вне цикла событий (задача 33, C1): CPU-ёмкий разбор даже
    под защитой html_guard не должен держать опрос Telegram и вторую проверку."""
    html = decode_page(body, charset)
    return html, parse_head(html), parse_contacts(html, complete)


class AiohttpSender:
    """Один запрос без переходов через защищённую сессию (ТЗ, С3, С14). Тело читается только у страницы HTML."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def __call__(self, url: str, read_head: bool) -> Answer:
        async with self._session.get(url, allow_redirects=False, headers=REQUEST_HEADERS) as response:
            answer = _answer(response)
            if read_head and is_readable_page(answer):
                body, complete = await read_page_bytes(response.content, PAGE_MAX_BYTES, STOP_AT_HEAD)
                answer = replace(answer, body=body, complete=complete)
            return answer


def _answer(response: aiohttp.ClientResponse) -> Answer:
    typed = CONTENT_TYPE_HEADER in response.headers  # без заголовка aiohttp подставляет octet-stream
    return Answer(status=response.status, content_type=response.content_type.lower() if typed else "",
                  charset=response.charset, location=response.headers.get(LOCATION_HEADER),
                  content_encoding=response.headers.get(CONTENT_ENCODING_HEADER, "").strip().lower(),
                  size_bytes=response.content_length)


async def read_page_bytes(content: aiohttp.StreamReader, limit: int, stop_at_head: bool) -> tuple[bytes, bool]:
    """Тело страницы не больше limit (ТЗ, С5); stop_at_head — хватит </head> или <body. Второе значение — прочитано
    ли всё, что просили: False — обрезано пределом (тогда находок «на странице нет …» не выносим, ТЗ 5.10)."""
    body = bytearray()
    while len(body) < limit:
        chunk = await content.read(min(READ_CHUNK_BYTES, limit - len(body)))
        if not chunk:
            return bytes(body), True
        body.extend(chunk)
        if stop_at_head and HEAD_END.search(body):
            return bytes(body), True
    return bytes(body), False


class RefusedAddress(OSError):
    """Защита адреса отказала. OSError — чтобы aiohttp превратил отказ резолвера в ошибку соединения (С14)."""


class GuardedResolver(AbstractResolver):
    """Резолвер aiohttp поверх AddressGuard (ТЗ, С2, С3, С14): отдаёт только проверенные IPv4."""

    def __init__(self, guard: AddressGuard):
        self._guard = guard

    async def resolve(self, host: str, port: int = 0,
                      family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        try:
            addresses = await self._guard.resolve(host)
        except GUARD_REFUSALS as error:
            raise RefusedAddress(f"{type(error).__name__}: {host}") from None
        return [ResolveResult(hostname=host, host=address, port=port, family=socket.AF_INET, proto=0,
                              flags=socket.AI_NUMERICHOST) for address in addresses]

    async def close(self) -> None:
        return None


def guarded_session(guard: AddressGuard) -> aiohttp.ClientSession:
    """Отдельная сессия своих запросов к сайтам (ТЗ, С14): свой резолвер, только IPv4, без кеша DNS, без
    переиспользования соединений, без прокси из окружения, без кук, без распаковки."""
    connector = aiohttp.TCPConnector(resolver=GuardedResolver(guard), family=socket.AF_INET, use_dns_cache=False,
                                     force_close=True)
    timeout = aiohttp.ClientTimeout(total=PAGE_TIMEOUT_SECONDS, sock_connect=CONNECT_TIMEOUT_SECONDS)
    return aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False,
                                 cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False)
