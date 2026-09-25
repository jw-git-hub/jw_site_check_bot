"""Защита сайта своими запросами (ТЗ, 5.4): TLS-рукопожатие, сертификат, переадресация http → https.

Только корень сайта: без путей, параметров и переходов (ТЗ, С4). Соединения открывает net_guard (StreamOpener).
Исход проверки сертификата — по коду OpenSSL (verify_code).
"""
import asyncio
import contextlib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from cryptography import x509
from cryptography.x509.oid import NameOID

from bot.site_check.net_guard import StreamOpener

HTTPS_PORT = 443
HTTP_PORT = 80
CLOSE_TIMEOUT_SECONDS = 2
HEADER_END = b"\r\n\r\n"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
SUCCESS_STATUSES = range(200, 300)
HTTPS_PREFIX = "https://"
USER_AGENT = "Mozilla/5.0 (compatible; jw_site_check_bot; +https://t.me/jw_site_check_bot)"


class TlsOutcome(StrEnum):
    OK = "ok"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    WRONG_HOST = "wrong_host"
    SELF_SIGNED = "self_signed"
    INCOMPLETE_CHAIN = "incomplete_chain"
    OTHER = "other"
    NO_HTTPS = "no_https"
    HANDSHAKE_FAILED = "handshake_failed"
    CONNECT_FAILED = "connect_failed"


VERIFY_OUTCOMES = {10: TlsOutcome.EXPIRED, 9: TlsOutcome.NOT_YET_VALID, 62: TlsOutcome.WRONG_HOST,
                   18: TlsOutcome.SELF_SIGNED, 19: TlsOutcome.SELF_SIGNED, 20: TlsOutcome.INCOMPLETE_CHAIN}


class RedirectState(StrEnum):
    REDIRECTS = "redirects"
    NO_REDIRECT = "no_redirect"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CertInfo:
    not_before: datetime
    not_after: datetime
    issuer: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class TlsFacts:
    host: str
    outcome: TlsOutcome
    cert: CertInfo | None = None


def verified_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def unverified_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def check_tls(open_stream: StreamOpener, host: str,
                    make_context: Callable[[], ssl.SSLContext] = verified_context) -> TlsFacts:
    try:
        _reader, writer = await open_stream(host, HTTPS_PORT, make_context())
    except ssl.SSLCertVerificationError as error:
        outcome = VERIFY_OUTCOMES.get(error.verify_code, TlsOutcome.OTHER)
        return TlsFacts(host, outcome, await read_cert_unverified(open_stream, host))
    except ssl.SSLError:
        return TlsFacts(host, TlsOutcome.HANDSHAKE_FAILED)
    except (OSError, TimeoutError):
        return TlsFacts(host, TlsOutcome.CONNECT_FAILED)
    try:
        return TlsFacts(host, TlsOutcome.OK, _peer_cert(writer))
    finally:
        await close_quietly(writer)


async def read_cert_unverified(open_stream: StreamOpener, host: str) -> CertInfo | None:
    """Сертификат без проверки — только прочитать даты и имена, когда проверка не прошла."""
    try:
        _reader, writer = await open_stream(host, HTTPS_PORT, unverified_context())
    except (OSError, TimeoutError):
        return None
    try:
        return _peer_cert(writer)
    finally:
        await close_quietly(writer)


def _peer_cert(writer: asyncio.StreamWriter) -> CertInfo | None:
    ssl_object = writer.get_extra_info("ssl_object")
    der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    return parse_cert(der) if der else None


def parse_cert(der: bytes) -> CertInfo:
    cert = x509.load_der_x509_certificate(der)
    return CertInfo(cert.not_valid_before_utc, cert.not_valid_after_utc, _issuer(cert), _dns_names(cert))


def _issuer(cert: x509.Certificate) -> str:
    for oid in (NameOID.ORGANIZATION_NAME, NameOID.COMMON_NAME):
        attributes = cert.issuer.get_attributes_for_oid(oid)
        if attributes:
            return str(attributes[0].value)
    return ""


def _dns_names(cert: x509.Certificate) -> tuple[str, ...]:
    try:
        extension = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return ()
    return tuple(extension.value.get_values_for_type(x509.DNSName))


async def check_http_redirect(open_stream: StreamOpener, host: str) -> RedirectState:
    try:
        reader, writer = await open_stream(host, HTTP_PORT, None)
    except (OSError, TimeoutError):
        return RedirectState.CLOSED
    try:
        writer.write(root_request(host))
        await writer.drain()
        head = await reader.readuntil(HEADER_END)
    except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return RedirectState.UNKNOWN
    finally:
        await close_quietly(writer)
    return redirect_state(head)


def root_request(host: str) -> bytes:
    lines = ("GET / HTTP/1.1", f"Host: {host}", f"User-Agent: {USER_AGENT}", "Accept: */*", "Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def redirect_state(head: bytes) -> RedirectState:
    lines = head.decode("latin-1").split("\r\n")
    status = _status(lines[0])
    if status in REDIRECT_STATUSES:
        secure = _header(lines, "location").lower().startswith(HTTPS_PREFIX)
        return RedirectState.REDIRECTS if secure else RedirectState.NO_REDIRECT
    return RedirectState.NO_REDIRECT if status in SUCCESS_STATUSES else RedirectState.UNKNOWN


def _status(status_line: str) -> int | None:
    parts = status_line.split(" ", 2)
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def _header(lines: list[str], name: str) -> str:
    for line in lines[1:]:
        key, _, value = line.partition(":")
        if key.strip().lower() == name:
            return value.strip()
    return ""


async def close_quietly(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError, TimeoutError, ssl.SSLError):
        await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT_SECONDS)
