import asyncio
from datetime import date

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.site_check import domain_expiry
from bot.site_check.domain_expiry import (RDAP, WHOIS, DomainPaid, RegistryClient, bootstrap_servers,
                                          rdap_expiration, registrable_domain, whois_paid_till)
from tests.certs import issue, server_context

PAID = b"domain:        MOTOR43.RU\nstate:         REGISTERED, DELEGATED\npaid-till:     2027-07-30T07:02:46Z\n"


@pytest.mark.parametrize(("host", "domain"), [
    ("vn.neva.beauty", "neva.beauty"), ("www.jw-dev.pro", "jw-dev.pro"), ("shop.spb.ru", "shop.spb.ru"),
    ("xn---43-5cdkauz5cvjeg.xn--p1ai", "xn---43-5cdkauz5cvjeg.xn--p1ai"), ("a.b.example.co.uk", "example.co.uk"),
    ("spb.ru", None), ("salon4you.tilda.ws", None), ("serkamsmm.wixsite.com", None), ("shop.nethouse.ru", None),
])
def test_registrable_domain_table(host, domain):
    assert registrable_domain(host) == domain


def test_bootstrap_and_rdap_answers_are_read_defensively():
    servers = bootstrap_servers({"services": [[["pro", "Beauty"], ["https://rdap.example/"]], "junk", [[1], []]]})
    assert servers == {"pro": "https://rdap.example/", "beauty": "https://rdap.example/"}
    events = {"events": [{"eventAction": "registration", "eventDate": "2026-08-25T15:48:28Z"},
                         {"eventAction": "expiration", "eventDate": "2027-08-25T15:48:28.451Z"}]}
    assert rdap_expiration(events) == date(2027, 8, 25)
    assert rdap_expiration({"events": "junk"}) is rdap_expiration(None) is None


def test_whois_paid_till():
    assert whois_paid_till(PAID.decode()) == date(2027, 7, 30)
    assert whois_paid_till("") is whois_paid_till("paid-till: never") is None


class FakeReader:
    def __init__(self, answer: bytes):
        self.chunks = [answer, b""]

    async def read(self, size: int) -> bytes:
        return self.chunks.pop(0)[:size] if self.chunks else b""


class FakeWriter:
    transport = None

    def __init__(self, asked: list[bytes]):
        self.asked = asked

    def write(self, data: bytes) -> None:
        self.asked.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeWhois:
    def __init__(self, *answers: bytes):
        self.answers, self.asked, self.hosts = list(answers), [], []

    async def __call__(self, host: str, port: int):
        self.hosts.append((host, port))
        return FakeReader(self.answers.pop(0)), FakeWriter(self.asked)


def whois_client(fake: FakeWhois) -> RegistryClient:
    return RegistryClient(http=None, open_connection=fake)


async def test_ru_domain_goes_to_whois_with_only_its_name():
    fake = FakeWhois(PAID)
    found = await whois_client(fake).paid_until("www.motor43.ru")
    assert found == DomainPaid("motor43.ru", date(2027, 7, 30), WHOIS)
    assert (fake.hosts, fake.asked) == ([("whois.tcinet.ru", 43)], [b"motor43.ru\r\n"])


async def test_empty_whois_answer_is_retried_once_then_silent(monkeypatch):
    monkeypatch.setattr(domain_expiry, "WHOIS_RETRY_PAUSE_SECONDS", 0)
    assert (await whois_client(FakeWhois(b"", PAID)).paid_until("motor43.ru")).until == date(2027, 7, 30)
    assert await whois_client(FakeWhois(b"", b"")).paid_until("motor43.ru") is None


async def test_platform_and_public_suffix_hosts_are_not_looked_up():
    fake = FakeWhois()
    client = whois_client(fake)
    assert await client.paid_until("salon4you.tilda.ws") is None
    assert await client.paid_until("spb.ru") is None
    assert fake.hosts == []


async def test_whois_that_hangs_or_breaks_is_silent(monkeypatch):
    monkeypatch.setattr(domain_expiry, "REQUEST_TIMEOUT_SECONDS", 0.01)

    async def hanging(host, port):
        await asyncio.sleep(1)

    async def refusing(host, port):
        raise ConnectionRefusedError()

    for opener in (hanging, refusing):
        assert await RegistryClient(http=None, open_connection=opener).paid_until("motor43.ru") is None


async def https_server(app: web.Application, tmp_path):
    """IANA списывает только https-адреса RDAP-серверов (решение контроллёра, ТЗ, С15) — тестовый сервер тоже
    должен отвечать по TLS, иначе адрес из его же /dns.json был бы http:// и его отбросил бы bootstrap_servers.
    Сертификат самоподписанный, клиент его не проверяет (ssl=False) — тут это не предмет проверки."""
    server = TestServer(app)
    await server.start_server(ssl=server_context(tmp_path, issue("rdap.test")))
    return server


def https_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))


@pytest.fixture
async def registry(tmp_path):
    calls = []

    async def dns(request: web.Request) -> web.Response:
        calls.append("bootstrap")
        return web.json_response({"services": [[["pro"], [str(request.url.with_path("/rdap/"))]]]})

    async def rdap(request: web.Request) -> web.Response:
        calls.append(request.match_info["name"])
        return web.json_response({"events": [{"eventAction": "expiration", "eventDate": "2027-08-25T15:48:28Z"}]})

    app = web.Application()
    app.router.add_get("/dns.json", dns)
    app.router.add_get("/rdap/domain/{name}", rdap)
    server = await https_server(app, tmp_path)
    session = https_session()
    yield RegistryClient(session, bootstrap_url=str(server.make_url("/dns.json"))), calls
    await session.close()
    await server.close()


async def test_gtld_goes_to_rdap_from_the_iana_list_and_is_cached(registry):
    client, calls = registry
    assert await client.paid_until("www.jw-dev.pro") == DomainPaid("jw-dev.pro", date(2027, 8, 25), RDAP)
    assert await client.paid_until("jw-dev.pro") == DomainPaid("jw-dev.pro", date(2027, 8, 25), RDAP)
    assert await client.paid_until("site.unknownzone") is None
    assert calls == ["bootstrap", "jw-dev.pro"]


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_failed_bootstrap_list_is_retried_soon_not_after_a_day(monkeypatch):
    """Список IANA не 200 — не кешировать пустой результат на сутки (решение контроллёра, задача 33, I1)."""
    monkeypatch.setattr(domain_expiry, "FAILED_BOOTSTRAP_RETRY_SECONDS", 10)
    calls = []

    async def broken(request: web.Request) -> web.Response:
        calls.append("bootstrap")
        return web.Response(status=500)

    app = web.Application()
    app.router.add_get("/dns.json", broken)
    server = TestServer(app)
    await server.start_server()
    session = aiohttp.ClientSession()
    clock = ManualClock()
    try:
        client = RegistryClient(session, monotonic=clock, bootstrap_url=str(server.make_url("/dns.json")))
        assert await client.paid_until("jw-dev.pro") is None
        clock.now = 5
        assert await client.paid_until("jw-dev.pro") is None
        assert calls == ["bootstrap"]  # раньше FAILED_BOOTSTRAP_RETRY_SECONDS — без повторного запроса
        clock.now = 11
        assert await client.paid_until("jw-dev.pro") is None
        assert calls == ["bootstrap", "bootstrap"]  # срок вышел — RDAP пробуем снова, не молчим сутки
    finally:
        await session.close()
        await server.close()


async def test_a_failed_refresh_keeps_the_previous_good_bootstrap_list(tmp_path):
    """Обновление списка IANA сорвалось — прежний хороший список не затирается пустым (раунд 2, задача 33, I1)."""
    calls = []

    async def dns(request: web.Request) -> web.Response:
        calls.append("bootstrap")
        if len(calls) == 1:
            return web.json_response({"services": [[["pro"], [str(request.url.with_path("/rdap/"))]]]})
        return web.Response(status=500)

    async def rdap(request: web.Request) -> web.Response:
        return web.json_response({"events": [{"eventAction": "expiration", "eventDate": "2027-08-25T15:48:28Z"}]})

    app = web.Application()
    app.router.add_get("/dns.json", dns)
    app.router.add_get("/rdap/domain/{name}", rdap)
    server = await https_server(app, tmp_path)
    session = https_session()
    clock = ManualClock()
    try:
        client = RegistryClient(session, monotonic=clock, bootstrap_url=str(server.make_url("/dns.json")))
        assert await client.paid_until("jw-dev.pro") == DomainPaid("jw-dev.pro", date(2027, 8, 25), RDAP)
        clock.now = 24 * 3600 + 1  # хороший список устарел — обновление пробуем, а IANA сейчас отвечает 500
        assert await client.paid_until("site.pro") == DomainPaid("site.pro", date(2027, 8, 25), RDAP)
        assert calls == ["bootstrap", "bootstrap"]  # обновление пробовали, но старый список не потерялся
        assert client._servers == {"pro": str(server.make_url("/rdap/"))}
    finally:
        await session.close()
        await server.close()


# --- ТЗ, С15: реестры — не через защиту адресов сайта, но без переходов и с фиксированным whois-хостом ---

async def test_rdap_server_redirect_is_not_followed(tmp_path):
    """Адрес IANA присылает переадресацию вместо срока — своей защиты сайта здесь нет (реестр вне неё), но
    переход всё равно не делается: allow_redirects=False (решение контроллёра). Если бы переход выполнялся,
    /elsewhere отдал бы настоящий срок и тест бы это заметил."""
    calls = []

    async def dns(request: web.Request) -> web.Response:
        return web.json_response({"services": [[["pro"], [str(request.url.with_path("/rdap/"))]]]})

    async def rdap(request: web.Request) -> web.Response:
        calls.append(request.match_info["name"])
        raise web.HTTPFound(location="/elsewhere")

    async def elsewhere(request: web.Request) -> web.Response:
        calls.append("elsewhere")
        return web.json_response({"events": [{"eventAction": "expiration", "eventDate": "2027-08-25T15:48:28Z"}]})

    app = web.Application()
    app.router.add_get("/dns.json", dns)
    app.router.add_get("/rdap/domain/{name}", rdap)
    app.router.add_get("/elsewhere", elsewhere)
    server = await https_server(app, tmp_path)
    session = https_session()
    try:
        client = RegistryClient(session, bootstrap_url=str(server.make_url("/dns.json")))
        assert await client.paid_until("jw-dev.pro") is None
        assert calls == ["jw-dev.pro"]  # запрос ушёл, но переход по 302 не делался
    finally:
        await session.close()
        await server.close()


def test_http_iana_address_is_not_used():
    """Список IANA прислал адрес без https:// — зона считается без RDAP (решение контроллёра)."""
    servers = bootstrap_servers({"services": [[["pro"], ["http://rdap.example/"]]]})
    assert servers == {}


def test_secure_rdap_address_skips_a_leading_non_https_url():
    """Берём первый https-адрес зоны, а не только urls[0] (задача 33, M5)."""
    servers = bootstrap_servers({"services": [[["pro"], ["http://insecure.example/", "https://secure.example/"]]]})
    assert servers == {"pro": "https://secure.example/"}


async def test_cache_drops_expired_entries_on_a_new_write(monkeypatch):
    """Кеш не должен расти бессрочно (задача 33, M6): просроченная запись выбрасывается при записи новой."""
    monkeypatch.setattr(domain_expiry, "CACHE_SECONDS", 100)
    client = whois_client(FakeWhois(PAID))
    client._cache["stale.ru"] = (0.0, DomainPaid("stale.ru", date(2020, 1, 1), WHOIS))
    client._monotonic = lambda: 200.0
    await client.paid_until("motor43.ru")
    assert set(client._cache) == {"motor43.ru"}
