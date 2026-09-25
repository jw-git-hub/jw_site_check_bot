import asyncio
import socket

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.site_check.net_guard import (MAX_HOME_IP_BODY_BYTES, AddressGuard, NameLookupFailed, NameNotFound, NoIPv4,
                                      PrivateAddress, fetch_home_ip, is_public_ipv4, parse_ip, system_resolver)

PUBLIC = "93.184.215.14"


@pytest.mark.parametrize("address", [
    "10.0.0.1", "172.16.0.1", "192.168.0.1", "127.0.0.1", "169.254.169.254", "100.100.100.100", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255", "2001:4860:4860::8888", "64:ff9b::7f00:1",
    "::ffff:127.0.0.1", "fc00::1", "fe80::1",
])
def test_not_public(address):
    assert not is_public_ipv4(address, None)


def test_public_address_passes_unless_it_is_home():
    assert is_public_ipv4(PUBLIC, None)
    assert not is_public_ipv4(PUBLIC, PUBLIC)


def resolver_for(mapping: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        return mapping[host]
    return resolve


async def test_any_private_address_refuses_the_name():
    guard = AddressGuard(resolver_for({"mixed.test": [PUBLIC, "10.0.0.1"], "fine.test": [PUBLIC]}))
    assert await guard.resolve("fine.test") == [PUBLIC]
    with pytest.raises(PrivateAddress):
        await guard.resolve("mixed.test")


async def test_home_address_is_refused():
    guard = AddressGuard(resolver_for({"home.test": [PUBLIC]}))
    guard.home_ip = PUBLIC
    with pytest.raises(PrivateAddress):
        await guard.resolve("home.test")


async def test_system_resolver_sees_localhost_as_private():
    with pytest.raises(PrivateAddress):
        await AddressGuard(system_resolver).resolve("localhost")


def fake_getaddrinfo(outcomes: dict[int, Exception | list[str]]):
    async def getaddrinfo(host, port, *, family=0, type=0, proto=0, flags=0):
        outcome = outcomes[family]
        if isinstance(outcome, Exception):
            raise outcome
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0)) for address in outcome]
    return getaddrinfo


async def test_unknown_name_is_not_found(monkeypatch):
    missing = socket.gaierror(socket.EAI_NONAME, "not known")
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo({socket.AF_INET: missing, socket.AF_UNSPEC: missing}))
    with pytest.raises(NameNotFound):
        await system_resolver("net-takogo.example")


async def test_ipv6_only_name_is_reported(monkeypatch):
    missing = socket.gaierror(socket.EAI_NONAME, "no A record")
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo({socket.AF_INET: missing,
                                                                socket.AF_UNSPEC: ["2001:db8::1"]}))
    with pytest.raises(NoIPv4):
        await system_resolver("v6only.example")


async def test_dns_trouble_is_lookup_failure(monkeypatch):
    trouble = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo({socket.AF_INET: trouble}))
    with pytest.raises(NameLookupFailed):
        await system_resolver("example.com")


async def test_open_stream_connects_to_checked_address():
    async def greet(reader, writer):
        writer.write(b"hello")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(greet, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    guard = AddressGuard(resolver_for({"site.test": ["127.0.0.1"]}), is_allowed=lambda address, home: True)
    async with server:
        reader, writer = await guard.open_stream("site.test", port, None)
        assert await reader.read(5) == b"hello"
        writer.close()


@pytest.mark.parametrize(("body", "address"), [
    ("fl=1\nip=93.184.215.14\nts=1", PUBLIC), ("93.184.215.14\n", PUBLIC), ("<html>error</html>", None),
    ("ip=2001:db8::1", None), ("ip=10.0.0.1", None), ("0.0.0.0", None), ("ip=100.64.0.1", None),
])
def test_parse_ip(body, address):
    assert parse_ip(body) == address


async def test_fetch_home_ip_tries_next_source():
    async def broken(request):
        return web.Response(status=500, text="oops")

    async def trace(request):
        return web.Response(text=f"fl=1\nip={PUBLIC}\n")

    app = web.Application()
    app.router.add_get("/broken", broken)
    app.router.add_get("/trace", trace)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/broken")), str(server.make_url("/trace")))
        assert await fetch_home_ip(session, sources) == PUBLIC


async def test_fetch_home_ip_skips_bad_status_even_with_an_address_in_the_body():
    other_public = "8.8.8.8"

    async def bad_status(request):
        return web.Response(status=503, text=f"{PUBLIC}\n")

    async def trace(request):
        return web.Response(text=f"{other_public}\n")

    app = web.Application()
    app.router.add_get("/bad", bad_status)
    app.router.add_get("/trace", trace)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/bad")), str(server.make_url("/trace")))
        assert await fetch_home_ip(session, sources) == other_public


async def test_fetch_home_ip_skips_undecodable_body():
    async def broken_encoding(request):
        return web.Response(body=b"\xff\xfe not utf-8 \xff", content_type="text/plain")

    async def trace(request):
        return web.Response(text=f"{PUBLIC}\n")

    app = web.Application()
    app.router.add_get("/broken", broken_encoding)
    app.router.add_get("/trace", trace)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/broken")), str(server.make_url("/trace")))
        assert await fetch_home_ip(session, sources) == PUBLIC


async def test_fetch_home_ip_parses_a_body_of_exactly_the_limit():
    filler = "0" * (MAX_HOME_IP_BODY_BYTES - 1 - len(PUBLIC))
    body = f"{filler}\n{PUBLIC}"
    assert len(body) == MAX_HOME_IP_BODY_BYTES

    async def at_limit(request):
        return web.Response(text=body)

    app = web.Application()
    app.router.add_get("/at-limit", at_limit)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/at-limit")),)
        assert await fetch_home_ip(session, sources) == PUBLIC


async def test_fetch_home_ip_refuses_an_oversize_body_instead_of_parsing_its_truncation():
    """4082 байта мусора + перенос строки + "93.184.215.140" (14 символов) = на 1 байт больше предела: обрезка
    по старому правилу дала бы правдоподобный, но чужой адрес — "93.184.215.14"."""
    truncatable_address = "93.184.215.140"
    other_public = "8.8.8.8"
    filler = "0" * (MAX_HOME_IP_BODY_BYTES - len(truncatable_address))
    body = f"{filler}\n{truncatable_address}"
    assert len(body) == MAX_HOME_IP_BODY_BYTES + 1

    async def oversize(request):
        return web.Response(text=body)

    async def trace(request):
        return web.Response(text=f"{other_public}\n")

    app = web.Application()
    app.router.add_get("/oversize", oversize)
    app.router.add_get("/trace", trace)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/oversize")), str(server.make_url("/trace")))
        assert await fetch_home_ip(session, sources) == other_public


async def piece_handler(request, pieces: list[bytes]):
    """Отвечает пришедшими частями по отдельности, с паузой между ними — как настоящая сеть, где
    `content.read(n)` может не дождаться n байт и отдать только то, что уже пришло."""
    response = web.StreamResponse()
    await response.prepare(request)
    for piece in pieces:
        await response.write(piece)
        await asyncio.sleep(0.01)
    await response.write_eof()
    return response


async def test_fetch_home_ip_reassembles_a_body_delivered_in_pieces():
    async def in_pieces(request):
        return await piece_handler(request, [b"93.184.215.14", b"0\n"])

    app = web.Application()
    app.router.add_get("/pieces", in_pieces)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/pieces")),)
        assert await fetch_home_ip(session, sources) == "93.184.215.140"


async def test_fetch_home_ip_detects_oversize_body_delivered_in_pieces():
    truncatable_address = "93.184.215.140"
    other_public = "8.8.8.8"
    filler = "0" * (MAX_HOME_IP_BODY_BYTES - len(truncatable_address))
    body = f"{filler}\n{truncatable_address}".encode()
    assert len(body) == MAX_HOME_IP_BODY_BYTES + 1
    pieces = [body[start:start + 1000] for start in range(0, len(body), 1000)]

    async def oversize_in_pieces(request):
        return await piece_handler(request, pieces)

    async def trace(request):
        return web.Response(text=f"{other_public}\n")

    app = web.Application()
    app.router.add_get("/oversize", oversize_in_pieces)
    app.router.add_get("/trace", trace)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/oversize")), str(server.make_url("/trace")))
        assert await fetch_home_ip(session, sources) == other_public


async def test_fetch_home_ip_refuses_oversize_body_even_when_address_is_in_the_first_piece():
    other_public = "8.8.8.8"
    filler = ("0" * MAX_HOME_IP_BODY_BYTES).encode()

    async def address_first(request):
        return await piece_handler(request, [f"{PUBLIC}\n".encode(), filler])

    async def trace(request):
        return web.Response(text=f"{other_public}\n")

    app = web.Application()
    app.router.add_get("/address-first", address_first)
    app.router.add_get("/trace", trace)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        sources = (str(server.make_url("/address-first")), str(server.make_url("/trace")))
        assert await fetch_home_ip(session, sources) == other_public
