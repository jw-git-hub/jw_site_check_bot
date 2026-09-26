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
from bot.site_check.probe import (DNS_REASON, SERVICE_DOWN, ProbeRejected, ProbeResult, SiteProbes,
                                  check_redirect_within_budget, check_tls_within_budget, measure, resolve_target)
from bot.site_check.tls_check import HTTPS_PREFIX, RedirectState, TlsFacts, TlsOutcome
from bot.site_check.url_input import Target, to_ascii_host
from bot.site_check.verdict import TLS_INVALID, SecurityFacts, Verdict, judge

CHECK_DEADLINE_SECONDS = 120  # ТЗ, Л4: одна проверка целиком
PROBE_TIMEOUT_SECONDS = 10    # ТЗ, Л4: до замера
AFTER_TIMEOUT_SECONDS = 15    # ТЗ, Л4: после замера
CERT_BLOCKS = "cert_blocks"
UNREACHABLE = "unreachable"
# Разбор ответа Lighthouse — узкий набор исключений на неожиданную структуру, не Exception целиком.
PARSE_ERRORS = (AttributeError, TypeError, ValueError)
# PageSpeed не называет плохой сертификат отдельным кодом (отвечает FAILED_DOCUMENT_REQUEST, classify →
# "unreachable") — своя проверка до замера уже знает, что браузер сайту не доверяет.
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
            # Срок для PageSpeed оставляет запас на проверки после замера — иначе они добавились бы поверх
            # общего срока проверки, до AFTER_TIMEOUT_SECONDS сверху.
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
        # Contract: Clock.now() уже гарантированно в UTC (bot/core/clock.py) — своей конвертации не нужно.
        # Даты сертификатов — тоже UTC, поэтому это важно для точности до дня.
        return self._clock.now().date()

    async def _before(self, target: Target) -> ProbeResult:
        """DNS проходит целиком в пределах срока «до замера»: срок вышел во время него — сбой на нашей стороне,
        PageSpeed не вызывается. Вышел позже (TLS, переадресация) — мягкий отказ от своих проверок: сайт
        разбирается PageSpeed'ом даже без них.
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
        """Неожиданная форма ответа не роняет проверку — узкий набор исключений, в журнал и отказ.

        Нечитаемый ответ Google — наша сторона, не сайта, поэтому reached_measurement=False — попытка не
        списывается (в отличие от пустого замера в _require_measurement, где сайт действительно ничего не
        показал)."""
        try:
            return parse_lighthouse(result)
        except PARSE_ERRORS as error:
            logger.warning("разбор ответа PageSpeed не удался: {}", type(error).__name__)
            raise CheckFailed(MEASURE_FAILED, reached_measurement=False) from None

    def _require_measurement(self, page: PageFacts) -> None:
        """Ни LCP, ни мобильной версии, ни веса страницы — измерить нечего, отчёта не будет."""
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
        """Браузер не открыл страницу («unreachable»), а своя проверка до замера уже сказала, что сертификату
        не доверяют, — тот же путь, что и явный CHROME_INTERSTITIAL_ERROR."""
        return code == UNREACHABLE and before.tls is not None and before.tls.outcome in UNTRUSTED_TLS_OUTCOMES

    async def _after(self, target: Target, before: ProbeResult, page: PageFacts) -> SecurityFacts:
        final_host = to_ascii_host(urlsplit(page.final_url).hostname or target.host)
        checks = self._after_checks(target.host, final_host, before.tls, page)
        try:
            tls, redirects = await asyncio.wait_for(checks, AFTER_TIMEOUT_SECONDS)
        except TimeoutError:
            tls, redirects = self._after_timeout_tls(before.tls, final_host, target.host, page), ()
        return SecurityFacts(tls=tls, redirects=redirects, insecure_urls=page.insecure_urls)

    def _after_timeout_tls(self, first_tls: TlsFacts | None, final_host: str | None, submitted_host: str,
                           page: PageFacts) -> tuple[TlsFacts, ...]:
        """Срок после замера вышел раньше своих проверок — используем то, что знали до замера, но не возвращаем
        NO_HTTPS, которую PageSpeed уже опроверг (тот же признак, что и в `_recheck_if_pagespeed_disagrees`
        ниже)."""
        if _pagespeed_contradicts_no_https(first_tls, final_host, submitted_host, page):
            return ()
        return _known(first_tls)

    async def _after_checks(self, submitted_host: str, final_host: str | None, first_tls: TlsFacts | None,
                            page: PageFacts) -> tuple[tuple[TlsFacts, ...], tuple[RedirectState, ...]]:
        """final_host is None — юникодный итоговый хост не перевёлся в ASCII: проверки для него не идут, только
        отметка «неизвестно», без падения. Каждая проверка переадресации — на своём коротком сроке
        (check_redirect_within_budget): зависший на чтении сервер съедает только свой срок, а не весь бюджет
        «после замера» вместе с уже готовыми результатами других проверок.
        """
        first_tls = await self._recheck_if_pagespeed_disagrees(submitted_host, final_host, first_tls, page)
        tls = list(_known(first_tls))
        redirects = [await check_redirect_within_budget(submitted_host, self._probes)]
        if final_host is None:
            redirects.append(RedirectState.UNKNOWN)
        elif final_host != submitted_host:
            tls += _known(await self._probes.check_tls(final_host))
            redirects.append(await check_redirect_within_budget(final_host, self._probes))
        return tuple(tls), tuple(redirects)

    async def _recheck_if_pagespeed_disagrees(self, submitted_host: str, final_host: str | None,
                                              first_tls: TlsFacts | None, page: PageFacts) -> TlsFacts | None:
        """Своя проверка до замера сказала NO_HTTPS для этого хоста (443 не ответил нам), а PageSpeed (настоящий
        браузер) на самом деле открыл https на том же хосте — свежая проверка важнее устаревшей. Другой хост
        (переадресация) сюда не попадает — там уже есть Finding.HTTPS_AFTER_REDIRECT.

        Свой срок — тот же, что у TLS-проверки до замера (probe.check_tls_within_budget), не второй wait_for —
        иначе зависшая перепроверка сама съела бы весь бюджет «после замера». Не дождались — свежий исход
        «обрыв соединения», как и до замера.
        """
        if not _pagespeed_contradicts_no_https(first_tls, final_host, submitted_host, page):
            return first_tls
        return await check_tls_within_budget(submitted_host, self._probes)


def _pagespeed_contradicts_no_https(first_tls: TlsFacts | None, final_host: str | None, submitted_host: str,
                                    page: PageFacts) -> bool:
    """PageSpeed (настоящий браузер) открыл https на том же хосте, для которого своя проверка сказала NO_HTTPS —
    эту находку нельзя ни использовать как исход перепроверки, ни вернуть обратно при таймауте после замера —
    один признак для обоих мест."""
    return (first_tls is not None and first_tls.outcome is TlsOutcome.NO_HTTPS
           and final_host == submitted_host and page.final_url.startswith(HTTPS_PREFIX))


def _known(tls: TlsFacts | None) -> tuple[TlsFacts, ...]:
    return (tls,) if tls else ()
