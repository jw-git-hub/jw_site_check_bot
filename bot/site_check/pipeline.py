"""Одна проверка (ТЗ, Л4): до замера → PageSpeed → после замера → вердикт. Шаги идут по очереди, не параллельно."""
import asyncio
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from loguru import logger

from bot.core.clock import Clock
from bot.site_check.lighthouse import AuditState, PageFacts, parse_lighthouse
from bot.site_check.pagespeed import (MEASURE_FAILED, LighthouseFailure, PageSpeedClient, PageSpeedUnavailable,
                                      classify)
from bot.site_check.probe import (DNS_REASON, SERVICE_DOWN, ProbeRejected, ProbeResult, SiteProbes, measure,
                                  resolve_target)
from bot.site_check.tls_check import HTTPS_PREFIX, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.url_input import Target, to_ascii_host
from bot.site_check.verdict import TLS_INVALID, SecurityFacts, Verdict, judge

CHECK_DEADLINE_SECONDS = 120  # ТЗ, Л4: одна проверка целиком
PROBE_TIMEOUT_SECONDS = 10    # ТЗ, Л4: до замера
AFTER_TIMEOUT_SECONDS = 15    # ТЗ, Л4: после замера
CERT_BLOCKS = "cert_blocks"
UNREACHABLE = "unreachable"
# Разбор ответа Lighthouse — узкий набор исключений на неожиданную структуру, не Exception целиком (поправка 9).
PARSE_ERRORS = (AttributeError, TypeError, ValueError)
# Поправка 4: PageSpeed не называет плохой сертификат отдельным кодом (отвечает FAILED_DOCUMENT_REQUEST,
# classify → "unreachable") — своя проверка до замера уже знает, что браузер сайту не доверяет.
UNTRUSTED_TLS_OUTCOMES = TLS_INVALID | frozenset({TlsOutcome.OTHER})


@dataclass(frozen=True)
class CheckResult:
    final_url: str
    page: PageFacts | None
    security: SecurityFacts
    verdict: Verdict


class CheckFailed(Exception):
    """Отчёта не будет. reached_measurement — дошла ли проверка до замера: от этого зависит списание (ТЗ, Л9)."""

    def __init__(self, code: str, reached_measurement: bool, page_status: int | None = None, reason: str | None = None):
        super().__init__(code)
        self.code = code
        self.reached_measurement = reached_measurement
        self.page_status = page_status
        self.reason = reason

    @property
    def charged(self) -> bool:
        return self.reached_measurement and self.code != SERVICE_DOWN


class Pipeline:
    def __init__(self, probes: SiteProbes, pagespeed: PageSpeedClient, clock: Clock):
        self._probes = probes
        self._pagespeed = pagespeed
        self._clock = clock

    async def run(self, target: Target) -> CheckResult:
        deadline = self._clock.monotonic() + CHECK_DEADLINE_SECONDS
        before = await self._before(target)
        try:
            # Поправка 1: срок для PageSpeed оставляет запас на проверки после замера — иначе их до
            # AFTER_TIMEOUT_SECONDS добавятся сверх общего срока проверки.
            result = await self._pagespeed.run(before.url, deadline - AFTER_TIMEOUT_SECONDS)
        except LighthouseFailure as failure:
            return self._on_failure(before, failure)
        except PageSpeedUnavailable as error:
            raise CheckFailed(SERVICE_DOWN, reached_measurement=True, reason=error.reason) from None
        page = self._parse(result)
        self._require_measurement(page)
        security = await self._after(target, before, page)
        return CheckResult(page.final_url or before.url, page, security, judge(page, security, self._today()))

    def _today(self) -> date:
        # Contract: Clock.now() уже гарантированно в UTC (bot/core/clock.py) — своей конвертации не нужно
        # (поправка 10). Даты сертификатов — тоже UTC, поэтому это важно для точности до дня.
        return self._clock.now().date()

    async def _before(self, target: Target) -> ProbeResult:
        """DNS проходит целиком в пределах срока «до замера»: срок вышел во время него — сбой на нашей стороне,
        PageSpeed не вызывается. Вышел позже (TLS, переадресация) — как и раньше, мягкий отказ от своих проверок
        (поправка 5).
        """
        deadline = self._clock.monotonic() + PROBE_TIMEOUT_SECONDS
        try:
            addresses = await asyncio.wait_for(resolve_target(target, self._probes), PROBE_TIMEOUT_SECONDS)
        except ProbeRejected as rejected:
            raise CheckFailed(rejected.code, reached_measurement=False, reason=rejected.reason) from None
        except TimeoutError:
            raise CheckFailed(SERVICE_DOWN, reached_measurement=False, reason=DNS_REASON) from None
        if addresses is None:
            return ProbeResult(target.url, None)
        remaining = max(0.0, deadline - self._clock.monotonic())
        try:
            return await asyncio.wait_for(measure(target, self._probes), remaining)
        except TimeoutError:
            return ProbeResult(target.url, None)

    def _parse(self, result: dict) -> PageFacts:
        """Поправка 9: неожиданная форма ответа не роняет проверку — узкий набор исключений, в журнал и отказ."""
        try:
            return parse_lighthouse(result)
        except PARSE_ERRORS as error:
            logger.warning("разбор ответа PageSpeed не удался: {}", type(error).__name__)
            raise CheckFailed(MEASURE_FAILED, reached_measurement=True) from None

    def _require_measurement(self, page: PageFacts) -> None:
        """Поправка 8: ни LCP, ни мобильной версии, ни веса страницы — измерить нечего, отчёта не будет."""
        empty = (page.speed.lcp_ms is None and page.mobile.viewport is AuditState.UNKNOWN
                and page.images.page_bytes is None)
        if empty:
            raise CheckFailed(MEASURE_FAILED, reached_measurement=True)

    def _on_failure(self, before: ProbeResult, failure: LighthouseFailure) -> CheckResult:
        code = classify(failure)
        if code != CERT_BLOCKS and not self._blocked_by_known_bad_cert(code, before):
            raise CheckFailed(code, reached_measurement=True, page_status=failure.page_status)
        security = SecurityFacts(tls=_known(before.tls), redirects=(), insecure_urls=(), cert_blocks=True)
        return CheckResult(before.url, None, security, judge(None, security, self._today()))

    def _blocked_by_known_bad_cert(self, code: str, before: ProbeResult) -> bool:
        """Поправка 4: браузер не открыл страницу («unreachable»), а своя проверка до замера уже сказала, что
        сертификату не доверяют, — тот же путь, что и явный CHROME_INTERSTITIAL_ERROR."""
        return code == UNREACHABLE and before.tls is not None and before.tls.outcome in UNTRUSTED_TLS_OUTCOMES

    async def _after(self, target: Target, before: ProbeResult, page: PageFacts) -> SecurityFacts:
        final_host = to_ascii_host(urlsplit(page.final_url).hostname or target.host)
        checks = self._after_checks(target.host, final_host, before.tls, page)
        try:
            tls, redirects = await asyncio.wait_for(checks, AFTER_TIMEOUT_SECONDS)
        except TimeoutError:
            tls, redirects = _known(before.tls), ()
        return SecurityFacts(tls=tls, redirects=redirects, insecure_urls=page.insecure_urls)

    async def _after_checks(self, submitted_host: str, final_host: str | None, first_tls: TlsFacts | None,
                            page: PageFacts) -> tuple[tuple[TlsFacts, ...], tuple[RedirectState, ...]]:
        """final_host is None — юникодный итоговый хост не перевёлся в ASCII: проверки для него не идут, только
        отметка «неизвестно» (поправка 7), без падения.
        """
        first_tls = await self._recheck_if_pagespeed_disagrees(submitted_host, final_host, first_tls, page)
        tls = list(_known(first_tls))
        redirects = [await self._probes.check_redirect(submitted_host)]
        if final_host is None:
            redirects.append(RedirectState.UNKNOWN)
        elif final_host != submitted_host:
            tls += _known(await self._probes.check_tls(final_host))
            redirects.append(await self._probes.check_redirect(final_host))
        return tuple(tls), tuple(redirects)

    async def _recheck_if_pagespeed_disagrees(self, submitted_host: str, final_host: str | None,
                                              first_tls: TlsFacts | None, page: PageFacts) -> TlsFacts | None:
        """Своя проверка до замера сказала NO_HTTPS для этого хоста (443 не ответил нам), а PageSpeed (настоящий
        браузер) на самом деле открыл https на том же хосте — свежая проверка важнее устаревшей (обзор задачи 14,
        находка 3). Другой хост (переадресация) сюда не попадает — там уже есть Finding.HTTPS_AFTER_REDIRECT.
        """
        same_host_https = final_host == submitted_host and page.final_url.startswith(HTTPS_PREFIX)
        if first_tls is None or first_tls.outcome is not TlsOutcome.NO_HTTPS or not same_host_https:
            return first_tls
        return await self._probes.check_tls(submitted_host)


def _known(tls: TlsFacts | None) -> tuple[TlsFacts, ...]:
    return (tls,) if tls else ()
