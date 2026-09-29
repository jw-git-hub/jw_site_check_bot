"""Срок оплаты домена (ТЗ, 5.4, 5.11; 10.1, С15): регистрируемый домен по списку суффиксов, RDAP по списку IANA,
whois ТЦИ для .ru, .рф, .su, кеш на сутки. Только фиксированные хосты реестров — не адреса со страницы, поэтому не
через защиту адресов сайта. Наружу уходит только регистрируемое имя. Ничего не падает: не узнали — None.

Решение контроллёра (ТЗ, С15): все запросы (список IANA и RDAP) — с allow_redirects=False, ответ не 200 — «срок не
узнали», не ошибка проверки; адрес RDAP-сервера из списка IANA годится, только если начинается с https:// — иначе
зона считается без RDAP; whois — только фиксированный хост из WHOIS_HOST, порт WHOIS_PORT."""
import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import aiohttp
from publicsuffixlist import PublicSuffixList

from bot.site_check.tls_check import close_quietly

BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
WHOIS_HOST = "whois.tcinet.ru"
WHOIS_PORT = 43
WHOIS_ZONES = frozenset({"ru", "xn--p1ai", "su"})
WHOIS_ATTEMPTS = 2                 # реестр .ru иногда отвечает пусто — один повтор (ТЗ, 5.11)
WHOIS_RETRY_PAUSE_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 4        # ТЗ, С15
MAX_ANSWER_BYTES = 64 * 1024       # ТЗ, С15
BOOTSTRAP_MAX_BYTES = 512 * 1024   # список IANA — около 70 КБ
CACHE_SECONDS = 24 * 3600
FAILED_BOOTSTRAP_RETRY_SECONDS = 10 * 60  # список IANA не получен — не молчать сутки (задача 33, I1)
HTTP_OK = 200
DATE_LENGTH = len("2027-07-30")
PAID_TILL = re.compile(r"^paid-till:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
EXPIRATION_ACTION = "expiration"
RDAP_HEADERS = {"Accept": "application/rdap+json"}
SECURE_RDAP_PREFIX = "https://"    # решение контроллёра: адрес IANA без https:// — зона без RDAP (ТЗ, С15)
PLATFORM_DOMAINS = ("tilda.ws", "wixsite.com", "wix.com", "nethouse.ru", "clients.site", "orgs.biz", "ucoz.ru",
                    "ucoz.net", "narod.ru", "wordpress.com", "tb.ru", "taplink.cc", "taplink.ws")
RDAP = "rdap"
WHOIS = "whois"
LOOKUP_ERRORS = (aiohttp.ClientError, OSError, TimeoutError, ValueError, UnicodeError)
SUFFIXES = PublicSuffixList()


@dataclass(frozen=True)
class DomainPaid:
    domain: str   # регистрируемый, в ASCII (punycode)
    until: date
    source: str   # RDAP или WHOIS — для «Подробных замеров»


def registrable_domain(host: str) -> str | None:
    """vn.neva.beauty → neva.beauty, shop.spb.ru → shop.spb.ru; конструктор или сам суффикс — None (ТЗ, 5.4)."""
    host = host.lower().rstrip(".")
    if any(host == platform or host.endswith("." + platform) for platform in PLATFORM_DOMAINS):
        return None
    return SUFFIXES.privatesuffix(host)


def bootstrap_servers(payload: Any) -> dict[str, str]:
    """Список IANA {"services": [[[зоны], [адреса]], …]} → зона → первый адрес RDAP, только если он https://
    (решение контроллёра, ТЗ, С15) — иначе зона в список RDAP-серверов не попадает."""
    services = payload.get("services") if isinstance(payload, dict) else None
    servers: dict[str, str] = {}
    for entry in services if isinstance(services, list) else []:
        zones, urls = (entry[0], entry[1]) if isinstance(entry, list) and len(entry) == 2 else ([], [])
        server = _secure_rdap_address(urls)
        if isinstance(zones, list) and server:
            servers.update({zone.lower(): server for zone in zones if isinstance(zone, str)})
    return servers


def _secure_rdap_address(urls: Any) -> str | None:
    if not isinstance(urls, list) or not urls or not isinstance(urls[0], str):
        return None
    return urls[0] if urls[0].startswith(SECURE_RDAP_PREFIX) else None


def rdap_expiration(payload: Any) -> date | None:
    events = payload.get("events") if isinstance(payload, dict) else None
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("eventAction") == EXPIRATION_ACTION:
            return _day(event.get("eventDate"))
    return None


def whois_paid_till(text: str) -> date | None:
    found = PAID_TILL.search(text)
    return _day(found.group(1)) if found else None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(value[:DATE_LENGTH]) if isinstance(value, str) else None
    except ValueError:
        return None


async def read_limited(stream: Any, limit: int) -> bytes:
    """До конца потока, не больше limit (ТЗ, С15): поток asyncio или aiohttp — у обоих read(n) и b"" в конце."""
    body = bytearray()
    while chunk := await stream.read(limit + 1 - len(body)):
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError("ответ реестра больше предела")
    return bytes(body)


class RegistryClient:
    """http — общая сессия бота (PageSpeed, адрес дома): хосты реестров фиксированы, защита адресов сайта не нужна."""

    def __init__(self, http: aiohttp.ClientSession | None, monotonic: Callable[[], float] = time.monotonic,
                 open_connection: Callable = asyncio.open_connection, bootstrap_url: str = BOOTSTRAP_URL):
        self._http = http
        self._monotonic = monotonic
        self._open = open_connection
        self._bootstrap_url = bootstrap_url
        self._servers: dict[str, str] = {}
        self._servers_at: float | None = None
        self._cache: dict[str, tuple[float, DomainPaid]] = {}

    async def paid_until(self, host: str) -> DomainPaid | None:
        domain = registrable_domain(host)
        if domain is None:
            return None
        cached = self._cache.get(domain)
        if cached and self._monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        found = await self._lookup(domain)
        if found:
            self._cache[domain] = (self._monotonic(), found)
        return found

    async def _lookup(self, domain: str) -> DomainPaid | None:
        zone = domain.rsplit(".", 1)[-1]
        try:
            return await (self._whois(domain) if zone in WHOIS_ZONES else self._rdap(domain, zone))
        except LOOKUP_ERRORS:
            return None

    async def _rdap(self, domain: str, zone: str) -> DomainPaid | None:
        server = (await self._rdap_servers()).get(zone)
        if server is None:
            return None
        found = rdap_expiration(await self._get_json(f"{server.rstrip('/')}/domain/{domain}", MAX_ANSWER_BYTES))
        return DomainPaid(domain, found, RDAP) if found else None

    async def _rdap_servers(self) -> dict[str, str]:
        """Пустой список — сорвавшийся список IANA, не «в мире нет RDAP»: кешируем его ненадолго, а не на сутки,
        иначе один сбойный ответ выключает RDAP до следующего дня (задача 33, I1)."""
        ttl = CACHE_SECONDS if self._servers else FAILED_BOOTSTRAP_RETRY_SECONDS
        fresh = self._servers_at is not None and self._monotonic() - self._servers_at < ttl
        if not fresh:
            self._servers = bootstrap_servers(await self._get_json(self._bootstrap_url, BOOTSTRAP_MAX_BYTES))
            self._servers_at = self._monotonic()
        return self._servers

    async def _get_json(self, url: str, limit: int) -> Any:
        """ТЗ, С15: без переходов — ответ не 200 (в том числе переадресация) считаем «срок не узнали», не ошибкой."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._http.get(url, headers=RDAP_HEADERS, timeout=timeout, allow_redirects=False) as response:
            if response.status != HTTP_OK:
                return None
            return json.loads(await read_limited(response.content, limit))

    async def _whois(self, domain: str) -> DomainPaid | None:
        for attempt in range(WHOIS_ATTEMPTS):
            if attempt:
                await asyncio.sleep(WHOIS_RETRY_PAUSE_SECONDS)
            found = whois_paid_till(await self._whois_answer(domain))
            if found:
                return DomainPaid(domain, found, WHOIS)
        return None

    async def _whois_answer(self, domain: str) -> str:
        reader, writer = await asyncio.wait_for(self._open(WHOIS_HOST, WHOIS_PORT), REQUEST_TIMEOUT_SECONDS)
        try:
            writer.write(f"{domain}\r\n".encode("ascii"))
            await writer.drain()
            answer = await asyncio.wait_for(read_limited(reader, MAX_ANSWER_BYTES), REQUEST_TIMEOUT_SECONDS)
        finally:
            await close_quietly(writer)
        return answer.decode("utf-8", errors="replace")
