import asyncio
import gzip
import socket
import time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.site_check import page_fetch
from bot.site_check.head_tags import parse_head
from bot.site_check.net_guard import AddressGuard, NoIPv4
from bot.site_check.page_fetch import (READ_CHUNK_BYTES, AiohttpSender, Answer, FetchFailure,
                                       GuardedResolver, ImageState, PreviewLoader, RefusedAddress, check_image,
                                       decode_page, fetch_page, follow, guarded_session)

PUBLIC = "93.184.215.14"
PAGE = "https://shop.example/"
IMAGE = "https://cdn.example/og.jpg"
HTML = "text/html"
HEAD = (b"<html lang='ru'><head><title>Shop</title>"
        b"<meta property='og:image' content='https://cdn.example/og.jpg'></head><body>")
FORBIDDEN_LOCATIONS = [
    "http://localhost/", "http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://169.254.169.254/latest/meta-data/",
    "http://100.100.100.100/", "http://10.0.0.1/", "http://172.16.0.1/", "http://192.168.0.1/", "http://[fc00::1]/",
    "http://[fe80::1]/", "http://[64:ff9b::7f00:1]/", "http://10.0.0.1:8080/", "http://shop.example:8080/",
    "http://user:pass@shop.example/", "ftp://shop.example/file", "http://router.lan/", "http://printer/",
]


class FakeSender:
    """Отвечает заготовками по адресу и запоминает запросы — сеть не трогается."""

    def __init__(self, answers: dict[str, Answer] | None = None):
        self.answers = answers or {}
        self.urls: list[str] = []

    async def __call__(self, url: str, read_head: bool) -> Answer:
        self.urls.append(url)
        return self.answers[url]


class HangingSender:
    async def __call__(self, url: str, read_head: bool) -> Answer:
        await asyncio.sleep(10)
        raise AssertionError("недостижимо")


class BrokenSender:
    def __init__(self, error: Exception):
        self.error = error

    async def __call__(self, url: str, read_head: bool) -> Answer:
        raise self.error


def page_answer(body: bytes = HEAD, **changes) -> Answer:
    return Answer(**{"status": 200, "content_type": HTML, "body": body, **changes})


def redirect(location: str | None, status: int = 301) -> Answer:
    return Answer(status=status, location=location)


def resolver_for(mapping: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        value = mapping[host]
        if isinstance(value, Exception):
            raise value
        return value
    return resolve


@pytest.mark.parametrize("location", FORBIDDEN_LOCATIONS)
async def test_redirect_to_forbidden_address_stops_before_network(location):
    sender = FakeSender({PAGE: redirect(location)})
    landing = await follow(sender, PAGE, read_head=True)
    assert landing.failure is FetchFailure.REFUSED
    assert sender.urls == [PAGE]


async def test_three_redirects_are_followed_and_the_fourth_is_given_up():
    chain = {f"https://shop.example/{step}": redirect(f"/{step + 1}") for step in range(3)}
    chain["https://shop.example/3"] = page_answer()
    landing = await follow(FakeSender(chain), "https://shop.example/0", read_head=True)
    assert (landing.failure, landing.url) == (None, "https://shop.example/3")
    longer = {f"https://shop.example/{step}": redirect(f"/{step + 1}") for step in range(5)}
    assert (await follow(FakeSender(longer), "https://shop.example/0", read_head=True)).failure \
        is FetchFailure.REDIRECTS


async def test_redirect_without_location_is_given_up():
    assert (await follow(FakeSender({PAGE: redirect(None)}), PAGE, read_head=True)).failure is FetchFailure.REDIRECTS


@pytest.mark.parametrize(("answer", "failure"), [
    (Answer(status=429, content_type=HTML), FetchFailure.STATUS),
    (Answer(status=503, content_type=HTML), FetchFailure.STATUS),
    (page_answer(content_type="application/pdf"), FetchFailure.NOT_HTML),
    (page_answer(content_encoding="gzip"), FetchFailure.COMPRESSED),
])
async def test_only_plain_html_200_is_a_page(answer, failure):
    assert (await fetch_page(FakeSender({PAGE: answer}), PAGE)).failure is failure


async def test_slow_site_is_given_up_on_its_own_deadline(monkeypatch):
    monkeypatch.setattr(page_fetch, "PAGE_TIMEOUT_SECONDS", 0.01)
    assert (await fetch_page(HangingSender(), PAGE)).failure is FetchFailure.TIMEOUT


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("обрыв"), RefusedAddress("внутренний адрес"),
                                   UnicodeDecodeError("utf-8", b"\xff", 0, 1, "кривой заголовок")])
async def test_network_trouble_is_a_failure_not_an_exception(error):
    assert (await fetch_page(BrokenSender(error), PAGE)).failure is FetchFailure.NETWORK


def test_charset_from_header_then_meta_then_utf8():
    title = "<head><title>Шиномонтаж</title></head>"
    assert "Шиномонтаж" in decode_page(title.encode("cp1251"), "windows-1251")
    koi = '<head><meta charset="koi8-r"><title>Кафе</title></head>'.encode("koi8_r")
    assert "Кафе" in decode_page(koi, None)
    assert "Кафе" in decode_page("<title>Кафе</title>".encode(), "no-such-charset")


def test_windows_1251_declared_only_in_meta_is_decoded():
    html = ('<head><meta http-equiv="Content-Type" content="text/html; charset=windows-1251">'
            '<title>Шиномонтаж в Кирове</title></head>')
    assert parse_head(decode_page(html.encode("cp1251"), None)).title == "Шиномонтаж в Кирове"


DANGEROUS_OR_BINARY_CODECS = ["punycode", "idna", "rot13", "base64", "zlib", "hex_codec"]


@pytest.mark.parametrize("name", DANGEROUS_OR_BINARY_CODECS)
def test_dangerous_or_binary_codec_in_header_falls_back_to_utf8(name):
    assert decode_page("Привет".encode(), name) == "Привет"


@pytest.mark.parametrize("name", DANGEROUS_OR_BINARY_CODECS)
def test_dangerous_or_binary_codec_in_meta_falls_back_to_utf8(name):
    html = f'<head><meta charset="{name}"><title>Кафе</title></head>'.encode()
    assert parse_head(decode_page(html, None)).title == "Кафе"


PUNYCODE_ATTACK_BYTES = 400_000       # квадратичный декодер: 2 МБ ≈ 15 минут без защиты (задача 33, C2)
PUNYCODE_TIME_LIMIT_SECONDS = 1.0


def test_punycode_header_does_not_hang_on_a_large_page():
    body = b"<html>-" + b"a" * PUNYCODE_ATTACK_BYTES
    started = time.perf_counter()
    decode_page(body, "punycode")
    assert time.perf_counter() - started < PUNYCODE_TIME_LIMIT_SECONDS


@pytest.mark.parametrize(("answer", "state", "status"), [
    (Answer(status=200, content_type="image/jpeg", size_bytes=70_415), ImageState.OK, 200),
    (Answer(status=404, content_type=HTML), ImageState.BROKEN, 404),
    (Answer(status=200, content_type=HTML), ImageState.NOT_IMAGE, 200),
    (Answer(status=200, content_type="image/svg+xml"), ImageState.SVG, 200),
])
async def test_preview_image_states(answer, state, status):
    check = await check_image(FakeSender({IMAGE: answer}), IMAGE)
    assert (check.state, check.status, check.name) == (state, status, "og.jpg")


@pytest.mark.parametrize("raw", ["/img/og.png", "//cdn.example/og.png", "img/og.png", "data:image/png;base64,AA"])
async def test_incomplete_preview_image_address_is_a_finding_without_network(raw):
    sender = FakeSender()
    assert (await check_image(sender, raw)).state is ImageState.RELATIVE
    assert sender.urls == []


@pytest.mark.parametrize("raw", ["http://127.0.0.1/og.png", "http://[::1]/og.png", "http://10.0.0.1:8080/og.png",
                                 "http://user:pass@cdn.example/og.png"])
async def test_preview_image_on_forbidden_address_is_not_requested(raw):
    sender = FakeSender()
    assert (await check_image(sender, raw)).state is ImageState.UNKNOWN
    assert sender.urls == []


async def test_preview_image_that_cannot_be_reached_is_unknown_not_broken():
    assert (await check_image(BrokenSender(aiohttp.ClientConnectionError()), IMAGE)).state is ImageState.UNKNOWN


async def test_loader_reads_head_and_checks_the_image():
    sender = FakeSender({PAGE: page_answer(), IMAGE: Answer(status=200, content_type="image/jpeg")})
    preview = await PreviewLoader(sender).load(PAGE)
    assert (preview.failure, preview.status, preview.head.title, preview.image.state) == \
        (None, 200, "Shop", ImageState.OK)
    assert (preview.url, preview.head_bytes, preview.complete) == (PAGE, len(HEAD), True)
    assert preview.html.startswith("<html lang='ru'><head>")


async def test_loader_parses_contacts_of_the_whole_page():
    body = HEAD + b'<p>text</p><footer><a href="tel:+79127127004">call</a></footer>'
    sender = FakeSender({PAGE: page_answer(body), IMAGE: Answer(status=200, content_type="image/jpeg")})
    preview = await PreviewLoader(sender).load(PAGE)
    assert (preview.contacts.call_links, preview.contacts.complete) == (1, True)


async def test_failed_load_has_no_contacts():
    preview = await PreviewLoader(FakeSender({PAGE: Answer(status=429, content_type=HTML)})).load(PAGE)
    assert preview.contacts is None


async def test_loader_page_without_image_does_not_request_one():
    sender = FakeSender({PAGE: page_answer(b"<head><title>Shop</title></head>")})
    preview = await PreviewLoader(sender).load(PAGE)
    assert (preview.image, sender.urls) == (None, [PAGE])


async def test_loader_keeps_the_address_without_parameters():
    sender = FakeSender({"https://shop.example/?utm_source=x": page_answer()})
    sender.answers[IMAGE] = Answer(status=200, content_type="image/jpeg")
    assert (await PreviewLoader(sender).load("https://shop.example/?utm_source=x")).url == PAGE


async def test_loader_bot_protection_stub_gives_no_head():
    preview = await PreviewLoader(FakeSender({PAGE: Answer(status=429, content_type=HTML)})).load(PAGE)
    assert (preview.head, preview.failure, preview.status, preview.image) == (None, FetchFailure.STATUS, 429, None)


async def test_guarded_resolver_gives_only_checked_addresses():
    resolver = GuardedResolver(AddressGuard(resolver_for({"shop.example": [PUBLIC]})))
    [result] = await resolver.resolve("shop.example", 443)
    assert (result["host"], result["hostname"], result["port"], result["family"]) == \
        (PUBLIC, "shop.example", 443, socket.AF_INET)


@pytest.mark.parametrize("addresses", [["10.0.0.1"], [PUBLIC, "127.0.0.1"], ["100.100.100.100"],
                                       NoIPv4("shop.example")])
async def test_guarded_resolver_refuses_internal_and_ipv6_only_names(addresses):
    resolver = GuardedResolver(AddressGuard(resolver_for({"shop.example": addresses})))
    with pytest.raises(RefusedAddress):
        await resolver.resolve("shop.example", 80)


async def test_guarded_resolver_refuses_the_home_address():
    guard = AddressGuard(resolver_for({"shop.example": [PUBLIC]}))
    guard.home_ip = PUBLIC
    with pytest.raises(RefusedAddress):
        await GuardedResolver(guard).resolve("shop.example", 80)


async def test_guarded_session_has_no_proxy_cookies_dns_cache_or_decompression():
    session = guarded_session(AddressGuard(resolver_for({})))
    try:
        connector = session.connector
        assert session.trust_env is False and session.auto_decompress is False
        assert isinstance(session.cookie_jar, aiohttp.DummyCookieJar)
        assert connector.force_close and not connector.use_dns_cache and connector.family == socket.AF_INET
    finally:
        await session.close()


LONG_TAIL = b"x" * 16_384
LONG_TAIL_CHUNKS = 64  # 1 МБ тела после </head>
ENDLESS_META = b"<meta name='x' content='y'>"
ENDLESS_REPEAT = 40_000  # около 1 МБ без </head>
CUT_LIMIT_BYTES = 100_000
WHOLE_PAGE_LIMIT_BYTES = 2 * 1024 * 1024


async def long_page(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/html; charset=utf-8"})
    await response.prepare(request)
    await response.write(b"<html><head><title>Long</title></head><body>")
    for _ in range(LONG_TAIL_CHUNKS):
        await response.write(LONG_TAIL)
    return response


async def endless_head(request: web.Request) -> web.Response:
    return web.Response(body=b"<html><head>" + ENDLESS_META * ENDLESS_REPEAT, headers={"Content-Type": HTML})


async def gzipped(request: web.Request) -> web.Response:
    return web.Response(body=gzip.compress(b"<html><head><title>Z</title></head>"),
                        headers={"Content-Type": HTML, "Content-Encoding": "gzip"})


async def cp1251_page(request: web.Request) -> web.Response:
    body = '<html><head><meta charset="windows-1251"><title>Шиномонтаж</title></head>'.encode("cp1251")
    return web.Response(body=body, headers={"Content-Type": HTML})


async def bot_stub(request: web.Request) -> web.Response:
    return web.Response(status=429, text='<html><head><meta name="robots" content="noindex,nofollow"></head>',
                        content_type=HTML)


async def host_echo(request: web.Request) -> web.Response:
    return web.Response(text=f"<html><head><title>{request.host}</title></head>", content_type=HTML)


@pytest.fixture
async def site():
    """Тестовый сайт на 127.0.0.1: здесь проверяется чтение ответа aiohttp, защита адреса — в тестах выше."""
    app = web.Application()
    for path, handler in (("/long", long_page), ("/endless", endless_head), ("/gzip", gzipped),
                          ("/cp1251", cp1251_page), ("/stub", bot_stub), ("/host", host_echo)):
        app.router.add_get(path, handler)
    server = TestServer(app)
    await server.start_server()
    session = aiohttp.ClientSession(auto_decompress=False)
    yield server, AiohttpSender(session)
    await session.close()
    await server.close()


async def test_reading_stops_right_after_the_head(site, monkeypatch):
    monkeypatch.setattr(page_fetch, "STOP_AT_HEAD", True)
    server, send = site
    answer = await send(str(server.make_url("/long")), True)
    assert b"</head>" in answer.body and len(answer.body) <= READ_CHUNK_BYTES and answer.complete


async def test_page_is_cut_at_the_limit_and_marked_incomplete(site, monkeypatch):
    monkeypatch.setattr(page_fetch, "PAGE_MAX_BYTES", CUT_LIMIT_BYTES)
    server, send = site
    answer = await send(str(server.make_url("/endless")), True)
    assert (len(answer.body), answer.complete) == (CUT_LIMIT_BYTES, False)


async def test_whole_page_is_read_when_asked(site, monkeypatch):
    monkeypatch.setattr(page_fetch, "STOP_AT_HEAD", False)
    monkeypatch.setattr(page_fetch, "PAGE_MAX_BYTES", WHOLE_PAGE_LIMIT_BYTES)
    server, send = site
    answer = await send(str(server.make_url("/long")), True)
    assert answer.complete and answer.body.endswith(LONG_TAIL)


async def test_version_1_2_reads_the_whole_page(site):
    server, send = site
    answer = await send(str(server.make_url("/long")), True)
    assert answer.complete and answer.body.endswith(LONG_TAIL)


async def test_compressed_page_is_not_read(site):
    server, send = site
    answer = await send(str(server.make_url("/gzip")), True)
    assert (answer.content_encoding, answer.body) == ("gzip", b"")


async def test_windows_1251_page_from_the_network_reads_right(site):
    server, send = site
    answer = await send(str(server.make_url("/cp1251")), True)
    assert parse_head(decode_page(answer.body, answer.charset)).title == "Шиномонтаж"


async def test_bot_protection_stub_is_not_a_page(site):
    server, send = site
    answer = await send(str(server.make_url("/stub")), True)
    assert (answer.status, answer.body) == (429, b"")


async def test_guarded_session_connects_to_the_checked_address_with_the_site_name(site):
    server, _ = site
    guard = AddressGuard(resolver_for({"shop.example": ["127.0.0.1"]}), is_allowed=lambda address, home: True)
    session = guarded_session(guard)
    try:
        answer = await AiohttpSender(session)(f"http://shop.example:{server.port}/host", True)
    finally:
        await session.close()
    assert parse_head(decode_page(answer.body, answer.charset)).title == f"shop.example:{server.port}"


async def test_guarded_session_never_connects_to_an_internal_address(site):
    server, _ = site
    session = guarded_session(AddressGuard(resolver_for({"shop.example": ["127.0.0.1"]})))
    try:
        with pytest.raises(aiohttp.ClientConnectorError):
            await AiohttpSender(session)(f"http://shop.example:{server.port}/host", True)
    finally:
        await session.close()
