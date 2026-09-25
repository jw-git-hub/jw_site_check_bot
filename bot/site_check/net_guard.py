"""Защита домашней сети в коде (ТЗ, раздел 10.1).

- Только IPv4 (A-записи): у домашних устройств есть глобальные IPv6, а NAT64 Python считает глобальным.
- Все адреса имени должны быть глобальными. Запрещены и 100.64.0.0/10 (Tailscale), и внешний адрес самого дома:
  иначе домен с таким адресом приведёт бота через роутер обратно в домашнюю сеть.
- Соединение — с тем самым проверенным адресом, имя сайта — только в SNI: DNS не подменит ответ между проверкой
  и запросом.
"""
import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable

import aiohttp

TAILSCALE_RANGE = ipaddress.ip_network("100.64.0.0/10")
DNS_TIMEOUT_SECONDS = 5
CONNECT_TIMEOUT_SECONDS = 10
HOME_IP_TIMEOUT_SECONDS = 10
MAX_HOME_IP_BODY_BYTES = 4096
HOME_IP_SOURCES = ("https://1.1.1.1/cdn-cgi/trace", "https://api.ipify.org")
TRACE_PREFIX = "ip="
NOT_FOUND_ERRORS = frozenset({socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)})

Resolver = Callable[[str], Awaitable[list[str]]]
Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
StreamOpener = Callable[[str, int, ssl.SSLContext | None], Awaitable[Streams]]


class NameNotFound(Exception):
    """DNS не знает такого имени."""


class NameLookupFailed(Exception):
    """DNS не ответил — сбой на нашей стороне."""


class NoIPv4(Exception):
    """У имени есть только IPv6-адреса: своими запросами сайт не проверить."""


class PrivateAddress(Exception):
    """Имя ведёт во внутреннюю сеть или в сам дом."""


def is_public_ipv4(address: str, home_ip: str | None) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version != 4 or ip in TAILSCALE_RANGE or address == home_ip:
        return False
    special = (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
               or ip.is_unspecified)
    return ip.is_global and not special


class AddressGuard:
    def __init__(self, resolver: Resolver, is_allowed: Callable[[str, str | None], bool] = is_public_ipv4):
        self._resolver = resolver
        self._is_allowed = is_allowed
        self.home_ip: str | None = None

    async def resolve(self, host: str) -> list[str]:
        addresses = await self._resolver(host)
        if not all(self._is_allowed(address, self.home_ip) for address in addresses):
            raise PrivateAddress(host)
        return addresses

    async def open_stream(self, host: str, port: int, context: ssl.SSLContext | None) -> Streams:
        addresses = await self.resolve(host)
        server_hostname = host if context else None
        connection = asyncio.open_connection(addresses[0], port, ssl=context, server_hostname=server_hostname)
        return await asyncio.wait_for(connection, CONNECT_TIMEOUT_SECONDS)


async def system_resolver(host: str) -> list[str]:
    """A-записи через системный DNS; в контейнере это публичные серверы (ТЗ, С9)."""
    try:
        return await _lookup(host, socket.AF_INET)
    except NameNotFound:
        if await _has_any_address(host):
            raise NoIPv4(host) from None
        raise


async def _lookup(host: str, family: int) -> list[str]:
    loop = asyncio.get_running_loop()
    lookup = loop.getaddrinfo(host, None, family=family, type=socket.SOCK_STREAM)
    try:
        infos = await asyncio.wait_for(lookup, DNS_TIMEOUT_SECONDS)
    except socket.gaierror as error:
        raise (NameNotFound(host) if error.errno in NOT_FOUND_ERRORS else NameLookupFailed(host)) from None
    except TimeoutError:
        raise NameLookupFailed(host) from None
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise NameNotFound(host)
    return addresses


async def _has_any_address(host: str) -> bool:
    try:
        return bool(await _lookup(host, socket.AF_UNSPEC))
    except (NameNotFound, NameLookupFailed):
        return False


async def fetch_home_ip(session: aiohttp.ClientSession, sources: tuple[str, ...] = HOME_IP_SOURCES) -> str | None:
    """Внешний адрес дома: по нему бот не ходит (ТЗ, С2). Не узнал — None."""
    for url in sources:
        body = await _get_text(session, url)
        address = parse_ip(body) if body else None
        if address:
            return address
    return None


async def _get_text(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=HOME_IP_TIMEOUT_SECONDS)) as response:
            if not response.ok:
                return None
            body = await _read_within_limit(response.content)
            return body.decode("utf-8", errors="replace") if body is not None else None
    except (aiohttp.ClientError, TimeoutError):
        return None


async def _read_within_limit(content: aiohttp.StreamReader) -> bytes | None:
    """Копит поток, пока не наступит конец или тело не превысит предел.

    `read(n)` у aiohttp не ждёт n байт — отдаёт что уже пришло, поэтому тело, доставленное несколькими
    кусками, читаем в цикле. Превысили предел — источник ненадёжен целиком, разбирать обрезанный хвост
    как правдоподобный чужой адрес нельзя (ТЗ, С2).
    """
    body = b""
    while True:
        piece = await content.read(MAX_HOME_IP_BODY_BYTES + 1 - len(body))
        if not piece:
            return body
        body += piece
        if len(body) > MAX_HOME_IP_BODY_BYTES:
            return None


def parse_ip(body: str) -> str | None:
    """Только настоящий внешний адрес: не любая IPv4-строка в ответе источника (ТЗ, С2)."""
    for line in body.splitlines():
        candidate = line.strip().removeprefix(TRACE_PREFIX)
        try:
            if is_public_ipv4(candidate, None):
                return candidate
        except ValueError:
            continue
    return None
