"""До замера (ТЗ, П6): адрес для PageSpeed и защита присланного хоста. Не дольше 10 секунд — срок ставит pipeline."""
import asyncio
from dataclasses import dataclass
from typing import Protocol

from bot.site_check.net_guard import AddressGuard, NameLookupFailed, NameNotFound, NoIPv4, PrivateAddress
from bot.site_check.tls_check import RedirectState, TlsFacts, TlsOutcome, check_http_redirect, check_tls
from bot.site_check.url_input import HTTP, HTTPS, Target

UNREACHABLE_TLS = frozenset({TlsOutcome.CONNECT_FAILED, TlsOutcome.HANDSHAKE_FAILED})
HTTP_RESPONDING_STATES = frozenset({RedirectState.NO_REDIRECT, RedirectState.REDIRECTS})  # ТЗ П6
GUARD_REFUSALS = (PrivateAddress, NameNotFound, NameLookupFailed, NoIPv4)
UNREACHABLE_DNS = "unreachable_dns"
PRIVATE_ADDRESS = "private_address"
SERVICE_DOWN = "service_down"
DNS_REASON = "dns"  # наш DNS не ответил — pipeline отличит это от сбоя PageSpeed
# ТЗ С5 — у каждого шага свой срок: порт 443, который тихо роняет пакеты, иначе держит check_tls (в net_guard —
# до 5 с DNS и 10 с подключения) дольше, чем pipeline оставляет на весь шаг измерения.
TLS_CHECK_TIMEOUT_SECONDS = 4
# После замера порт 80 читаем без предела на чтение (check_http_redirect) — медленно или совсем не отвечающий
# сервер не должен занимать всё время, отведённое на проверки после замера: у переадресации свой короткий срок,
# как и у TLS-проверки выше.
REDIRECT_CHECK_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class ProbeResult:
    url: str               # адрес для PageSpeed
    tls: TlsFacts | None   # None — своими запросами сайт не проверить


class ProbeRejected(Exception):
    def __init__(self, code: str, reason: str | None = None):
        super().__init__(code)
        self.code = code
        self.reason = reason


class SiteProbes(Protocol):
    async def resolve(self, host: str) -> list[str]: ...

    async def check_tls(self, host: str) -> TlsFacts | None: ...

    async def check_redirect(self, host: str) -> RedirectState: ...


class GuardedProbes:
    """Свои запросы к сайту — только через защиту сети (ТЗ, раздел 10)."""

    def __init__(self, guard: AddressGuard):
        self._guard = guard

    async def resolve(self, host: str) -> list[str]:
        return await self._guard.resolve(host)

    async def check_tls(self, host: str) -> TlsFacts | None:
        try:
            return await check_tls(self._guard.open_stream, host)
        except GUARD_REFUSALS:
            return None

    async def check_redirect(self, host: str) -> RedirectState:
        try:
            return await check_http_redirect(self._guard.open_stream, host)
        except GUARD_REFUSALS:
            return RedirectState.UNKNOWN


async def resolve_target(target: Target, probes: SiteProbes) -> list[str] | None:
    """Адреса имени или None — если у сайта только IPv6 (дальше решает PageSpeed). Иначе — ProbeRejected.

    Отдельная функция (не часть probe()): pipeline проверяет её на свой срок, отдельно от измерения.
    """
    try:
        return await probes.resolve(target.host)
    except NoIPv4:
        return None
    except NameNotFound:
        raise ProbeRejected(UNREACHABLE_DNS) from None
    except PrivateAddress:
        raise ProbeRejected(PRIVATE_ADDRESS) from None
    except NameLookupFailed:
        raise ProbeRejected(SERVICE_DOWN, reason=DNS_REASON) from None


async def probe(target: Target, probes: SiteProbes) -> ProbeResult:
    addresses = await resolve_target(target, probes)
    if addresses is None:
        return ProbeResult(target.url, None)
    return await measure(target, probes)


async def measure(target: Target, probes: SiteProbes) -> ProbeResult:
    """https или http — когда имя уже проверено (после resolve_target)."""
    tls = await check_tls_within_budget(target.host, probes)
    if target.scheme_given:
        if target.scheme == HTTP and tls and tls.outcome in UNREACHABLE_TLS:
            return await _confirm_no_https_on_port_80(target, probes, tls)
        return ProbeResult(target.url, tls)
    if tls and tls.outcome not in UNREACHABLE_TLS:
        return ProbeResult(target.url, tls)
    return await _fall_back_to_http(target, probes)


async def _confirm_no_https_on_port_80(target: Target, probes: SiteProbes, tls: TlsFacts) -> ProbeResult:
    """Человек сам прислал http://: наша проверка 443 не прошла, но это ещё не «сайт не ответил» — порт 80
    может отвечать, и тогда это ровно ТЗ 5.4, no_https, а не UNKNOWN. Тот же критерий «80 отвечает», что и
    без указанной схемы (_port_80_responds) — не отдельная копия. Адрес для PageSpeed не меняется: человек уже
    прислал http сам."""
    if await _port_80_responds(target, probes):
        return ProbeResult(target.url, TlsFacts(target.host, TlsOutcome.NO_HTTPS))
    return ProbeResult(target.url, tls)


async def check_tls_within_budget(host: str, probes: SiteProbes) -> TlsFacts | None:
    """Не дождались за свой срок — считаем как явный обрыв соединения, а не тратим на него весь бюджет времени,
    в который эта проверка вписана: до замера — на проверку порта 80, после замера — на перепроверку устаревшей
    NO_HTTPS в pipeline.py — обе стороны используют одну и ту же обёртку, не по копии."""
    try:
        return await asyncio.wait_for(probes.check_tls(host), TLS_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return TlsFacts(host, TlsOutcome.CONNECT_FAILED)


async def check_redirect_within_budget(host: str, probes: SiteProbes) -> RedirectState:
    """Не дождались за свой срок — переадресация неизвестна, а не потеряны остальные проверки после замера,
    в которые вписан этот вызов (по образцу check_tls_within_budget выше)."""
    try:
        return await asyncio.wait_for(probes.check_redirect(host), REDIRECT_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return RedirectState.UNKNOWN


async def _fall_back_to_http(target: Target, probes: SiteProbes) -> ProbeResult:
    """https нам не ответил, схема не указана. Порт 80 отвечает — сам или переадресацией — меряем http; иначе
    решит PageSpeed.

    Голый домен с закрытым 443, у которого 80 переадресует на www, — тоже http, не https: иначе PageSpeed
    стучался бы в закрытый 443 и отдал бы «сайт не открывается».
    """
    if await _port_80_responds(target, probes):
        return ProbeResult(target.url_with(HTTP), TlsFacts(target.host, TlsOutcome.NO_HTTPS))
    return ProbeResult(target.url_with(HTTPS), None)


async def _port_80_responds(target: Target, probes: SiteProbes) -> bool:
    """Сайт отвечает на 80 — сам или переадресацией на https — общий критерий и для голого домена без схемы,
    и для явно присланного http:// (ТЗ, 5.4)."""
    return await probes.check_redirect(target.host) in HTTP_RESPONDING_STATES
