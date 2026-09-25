import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bot.site_check.tls_check import (RedirectState, TlsFacts, TlsOutcome, check_http_redirect, check_tls,
                                      root_request)
from tests.certs import Issued, client_context_trusting, issue, issue_with_malformed_san, server_context

HOST = "site.test"
HUGE_HEADER_BYTES = 70_000


@pytest.fixture
def root() -> Issued:
    return issue("Test Root", is_ca=True)


def opener(port: int):
    async def open_stream(host, _port, context):
        server_hostname = host if context else None
        return await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname=server_hostname)
    return open_stream


async def refused(host, port, context):
    raise ConnectionRefusedError()


async def run_tls_check(tmp_path, root: Issued, leaf: Issued, *chain) -> TlsFacts:
    async def just_close(reader, writer):
        writer.close()

    server = await asyncio.start_server(just_close, "127.0.0.1", 0, ssl=server_context(tmp_path, leaf, *chain))
    async with server:
        port = server.sockets[0].getsockname()[1]
        return await check_tls(opener(port), HOST, lambda: client_context_trusting(tmp_path, root))


async def test_trusted_certificate_is_ok_with_dates_names_and_issuer(tmp_path, root):
    leaf = issue(HOST, root, dns_names=(HOST, "www." + HOST))
    facts = await run_tls_check(tmp_path, root, leaf)
    assert facts.outcome is TlsOutcome.OK
    assert facts.cert.names == (HOST, "www." + HOST)
    assert facts.cert.not_after == leaf.cert.not_valid_after_utc
    assert facts.cert.issuer == "Test Root org"


async def test_expired_certificate_keeps_its_date(tmp_path, root):
    ended = datetime.now(UTC) - timedelta(days=3)
    leaf = issue(HOST, root, dns_names=(HOST,), not_before=ended - timedelta(days=90), not_after=ended)
    facts = await run_tls_check(tmp_path, root, leaf)
    assert facts.outcome is TlsOutcome.EXPIRED
    assert facts.cert.not_after == leaf.cert.not_valid_after_utc


async def test_certificate_for_other_name(tmp_path, root):
    leaf = issue("other.test", root, dns_names=("other.test",))
    assert (await run_tls_check(tmp_path, root, leaf)).outcome is TlsOutcome.WRONG_HOST


async def test_self_signed_certificate(tmp_path, root):
    assert (await run_tls_check(tmp_path, root, issue(HOST, dns_names=(HOST,)))).outcome is TlsOutcome.SELF_SIGNED


async def test_missing_intermediate_is_incomplete_chain(tmp_path, root):
    intermediate = issue("Test Intermediate", root, is_ca=True)
    leaf = issue(HOST, intermediate, dns_names=(HOST,))
    assert (await run_tls_check(tmp_path, root, leaf)).outcome is TlsOutcome.INCOMPLETE_CHAIN


async def test_full_chain_with_intermediate_is_ok(tmp_path, root):
    intermediate = issue("Test Intermediate", root, is_ca=True)
    leaf = issue(HOST, intermediate, dns_names=(HOST,))
    assert (await run_tls_check(tmp_path, root, leaf, intermediate.cert)).outcome is TlsOutcome.OK


async def test_malformed_certificate_extension_keeps_outcome_and_dates(tmp_path, root):
    """Сертификат с не разбираемым SAN не роняет проверку: исход и даты остаются, имена — пустой кортеж."""
    leaf = issue_with_malformed_san(HOST)
    facts = await run_tls_check(tmp_path, root, leaf)
    assert facts.outcome is TlsOutcome.SELF_SIGNED
    assert facts.cert is not None
    assert facts.cert.not_after == leaf.cert.not_valid_after_utc
    assert facts.cert.names == ()


async def test_closed_port_is_connect_failure():
    assert (await check_tls(refused, HOST)).outcome is TlsOutcome.CONNECT_FAILED


async def test_plain_http_on_tls_port_is_handshake_failure():
    async def answer_http(reader, writer):
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer_http, "127.0.0.1", 0)
    async with server:
        facts = await check_tls(opener(server.sockets[0].getsockname()[1]), HOST)
    assert facts.outcome is TlsOutcome.HANDSHAKE_FAILED


async def serve_http(response: bytes) -> asyncio.Server:
    async def answer(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(response)
        await writer.drain()
        writer.close()
    return await asyncio.start_server(answer, "127.0.0.1", 0)


@pytest.mark.parametrize(("response", "state"), [
    (b"HTTP/1.1 301 Moved\r\nLocation: https://site.test/\r\n\r\n", RedirectState.REDIRECTS),
    (b"HTTP/1.1 308 Permanent\r\nlocation: HTTPS://www.site.test/\r\n\r\n", RedirectState.REDIRECTS),
    # Location не на https — не доказывает ни переадресацию, ни её отсутствие (бот сам переходы не делает,
    # ТЗ 5.4): редирект на другой http-адрес, относительный путь, protocol-relative и отсутствующий Location.
    (b"HTTP/1.1 302 Found\r\nLocation: http://site.test/home\r\n\r\n", RedirectState.UNKNOWN),
    (b"HTTP/1.1 301 Moved\r\nLocation: /home\r\n\r\n", RedirectState.UNKNOWN),
    (b"HTTP/1.1 301 Moved\r\nLocation: //www.site.test/\r\n\r\n", RedirectState.UNKNOWN),
    (b"HTTP/1.1 301 Moved\r\n\r\n", RedirectState.UNKNOWN),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", RedirectState.NO_REDIRECT),
    (b"HTTP/1.1 403 Forbidden\r\n\r\n", RedirectState.UNKNOWN),
    (b"garbage\r\n\r\n", RedirectState.UNKNOWN),
    (b"HTTP/1.1 \xb2 OK\r\n\r\n", RedirectState.UNKNOWN),
    (b"HTTP/1.1 3O1 x\r\n\r\n", RedirectState.UNKNOWN),
])
async def test_http_redirect_states(response, state):
    server = await serve_http(response)
    async with server:
        assert await check_http_redirect(opener(server.sockets[0].getsockname()[1]), HOST) is state


async def test_closed_http_port():
    assert await check_http_redirect(refused, HOST) is RedirectState.CLOSED


async def test_huge_headers_are_not_read_to_the_end():
    server = await serve_http(b"HTTP/1.1 200 OK\r\nX-Big: " + b"a" * HUGE_HEADER_BYTES + b"\r\n\r\n")
    async with server:
        assert await check_http_redirect(opener(server.sockets[0].getsockname()[1]), HOST) is RedirectState.UNKNOWN


def test_request_asks_only_for_root():
    assert root_request(HOST).startswith(b"GET / HTTP/1.1\r\nHost: site.test\r\n")
