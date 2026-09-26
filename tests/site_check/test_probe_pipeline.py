import asyncio
from datetime import UTC, datetime

import pytest

from bot.site_check import pipeline as pipeline_module
from bot.site_check import probe as probe_module
from bot.site_check.net_guard import NameLookupFailed, NameNotFound, NoIPv4, PrivateAddress
from bot.site_check.pagespeed import MEASURE_FAILED, LighthouseFailure, PageSpeedUnavailable
from bot.site_check.pipeline import AFTER_TIMEOUT_SECONDS, CHECK_DEADLINE_SECONDS, CheckFailed, Pipeline
from bot.site_check.probe import ProbeRejected, probe
from bot.site_check.tls_check import RedirectState, TlsFacts, TlsOutcome
from bot.site_check.url_input import Target
from bot.site_check.verdict import Block, Finding, Grade, SummaryKind, UnknownReason
from tests.builders import audit, cert, lighthouse
from tests.fakes import FakeClock

OK_TLS = TlsFacts("site.test", TlsOutcome.OK, cert())
PAGE_AUDITS = {"largest_contentful_paint": audit(value=1400), "total_byte_weight": audit(value=265_789)}
PAGE = lighthouse(**PAGE_AUDITS)


class FakeProbes:
    def __init__(self, resolve_error=None, tls=None, redirects=None):
        self.resolve_error = resolve_error
        self.tls = tls or {}
        self.redirects = redirects or {}
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, host):
        self.calls.append(("resolve", host))
        if self.resolve_error:
            raise self.resolve_error
        return ["93.184.215.14"]

    async def check_tls(self, host):
        self.calls.append(("tls", host))
        return self.tls.get(host)

    async def check_redirect(self, host):
        self.calls.append(("redirect", host))
        return self.redirects.get(host, RedirectState.UNKNOWN)


class FakePageSpeed:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.urls, self.deadlines = result, error, [], []

    async def run(self, url, deadline):
        self.urls.append(url)
        self.deadlines.append(deadline)
        if self.error:
            raise self.error
        return self.result


def target(scheme="https", scheme_given=False) -> Target:
    """Цель собрана напрямую: зона .test — служебная, разбор ввода её бы отбраковал (задача 7)."""
    return Target(scheme, scheme_given, "site.test", "site.test", "/", "")


async def test_https_answers_so_measure_https():
    result = await probe(target(), FakeProbes(tls={"site.test": OK_TLS}))
    assert (result.url, result.tls) == ("https://site.test/", OK_TLS)


async def test_no_https_but_http_answers_so_measure_http():
    probes = FakeProbes(tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED)},
                        redirects={"site.test": RedirectState.NO_REDIRECT})
    result = await probe(target(), probes)
    assert result.url == "http://site.test/"
    assert result.tls.outcome is TlsOutcome.NO_HTTPS


async def test_site_silent_for_us_is_left_to_pagespeed():
    probes = FakeProbes(tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED)},
                        redirects={"site.test": RedirectState.CLOSED})
    assert (await probe(target(), probes)).tls is None


async def test_given_http_scheme_is_measured_as_given():
    result = await probe(target("http", scheme_given=True), FakeProbes(tls={"site.test": OK_TLS}))
    assert result.url == "http://site.test/"


@pytest.mark.parametrize(("error", "code"), [
    (NameNotFound("x"), "unreachable_dns"), (PrivateAddress("x"), "private_address"),
    (NameLookupFailed("x"), "service_down"),
])
async def test_refusals_before_measuring(error, code):
    with pytest.raises(ProbeRejected) as rejected:
        await probe(target(), FakeProbes(resolve_error=error))
    assert rejected.value.code == code


async def test_ipv6_only_site_goes_to_pagespeed_without_own_checks():
    probes = FakeProbes(resolve_error=NoIPv4("x"))
    assert (await probe(target(), probes)).tls is None
    assert ("tls", "site.test") not in probes.calls


async def test_pipeline_checks_both_hosts_after_redirect_to_www():
    probes = FakeProbes(tls={"site.test": OK_TLS, "www.site.test": TlsFacts("www.site.test", TlsOutcome.OK, cert())},
                        redirects={"site.test": RedirectState.REDIRECTS, "www.site.test": RedirectState.REDIRECTS})
    pagespeed = FakePageSpeed(lighthouse(final_url="https://www.site.test/", **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    assert ("tls", "www.site.test") in probes.calls
    assert result.security.redirects == (RedirectState.REDIRECTS, RedirectState.REDIRECTS)
    assert result.verdict.summary is SummaryKind.ALL_GOOD


async def test_site_unreachable_for_us_still_gets_report():
    probes = FakeProbes(tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED)},
                        redirects={"site.test": RedirectState.CLOSED})
    result = await Pipeline(probes, FakePageSpeed(PAGE), FakeClock()).run(target())
    security = result.verdict.blocks[Block.SECURITY]
    assert (security.grade, security.unknown_reason) == (Grade.UNKNOWN, UnknownReason.OWN_CHECKS_FAILED)
    assert result.verdict.blocks[Block.SPEED].grade is Grade.GOOD


async def test_site_failure_is_charged_and_keeps_page_status():
    failure = LighthouseFailure("ERRORED_DOCUMENT_REQUEST", 404)
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(FakeProbes(tls={"site.test": OK_TLS}), FakePageSpeed(error=failure), FakeClock()).run(target())
    assert (failed.value.code, failed.value.charged, failed.value.page_status) == ("not_found", True, 404)


async def test_our_failure_is_not_charged():
    pagespeed = FakePageSpeed(error=PageSpeedUnavailable("quota"))
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(FakeProbes(tls={"site.test": OK_TLS}), pagespeed, FakeClock()).run(target())
    assert (failed.value.code, failed.value.charged, failed.value.reason) == ("service_down", False, "quota")


async def test_refusal_before_measuring_does_not_call_pagespeed():
    pagespeed = FakePageSpeed(PAGE)
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(FakeProbes(resolve_error=PrivateAddress("x")), pagespeed, FakeClock()).run(target())
    assert (failed.value.code, failed.value.charged, pagespeed.urls) == ("private_address", False, [])


async def test_certificate_blocking_gives_security_only_report():
    expired = TlsFacts("site.test", TlsOutcome.EXPIRED, cert(days_left=-3))
    pagespeed = FakePageSpeed(error=LighthouseFailure("CHROME_INTERSTITIAL_ERROR", None))
    result = await Pipeline(FakeProbes(tls={"site.test": expired}), pagespeed, FakeClock()).run(target())
    assert result.page is None
    assert result.verdict.summary is SummaryKind.CERT_BLOCKS
    assert result.verdict.blocks[Block.MOBILE].grade is Grade.UNKNOWN


# --- Поправка 1: срок PageSpeed оставляет запас для проверок после замера ---

async def test_pagespeed_deadline_reserves_time_for_after_checks():
    clock = FakeClock()
    probes = FakeProbes(tls={"site.test": OK_TLS}, redirects={"site.test": RedirectState.REDIRECTS})
    pagespeed = FakePageSpeed(PAGE)
    await Pipeline(probes, pagespeed, clock).run(target())
    assert pagespeed.deadlines == [clock.monotonic() + CHECK_DEADLINE_SECONDS - AFTER_TIMEOUT_SECONDS]


# --- Поправка 2: наш DNS не ответил — причина "dns" доходит до CheckFailed (задача 17 её отличит) ---

async def test_our_dns_failure_is_flagged_with_reason_for_task_17():
    pagespeed = FakePageSpeed(PAGE)
    probes = FakeProbes(resolve_error=NameLookupFailed("x"))
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(probes, pagespeed, FakeClock()).run(target())
    assert (failed.value.code, failed.value.reached_measurement, failed.value.reason) == ("service_down", False, "dns")
    assert pagespeed.urls == []


# --- Поправка 3: схема не указана, 443 не отвечает — 80 отвечает и переадресацией, не только напрямую ---

async def test_scheme_not_given_443_closed_80_redirects_gives_http():
    probes = FakeProbes(tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED)},
                        redirects={"site.test": RedirectState.REDIRECTS})
    result = await probe(target(), probes)
    assert result.url == "http://site.test/"
    assert result.tls.outcome is TlsOutcome.NO_HTTPS


async def test_bare_domain_443_closed_80_redirects_to_www_is_worth_fixing_not_bad():
    probes = FakeProbes(
        tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),
             "www.site.test": TlsFacts("www.site.test", TlsOutcome.OK, cert())},
        redirects={"site.test": RedirectState.REDIRECTS, "www.site.test": RedirectState.REDIRECTS},
    )
    pagespeed = FakePageSpeed(lighthouse(final_url="https://www.site.test/", **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    assert pagespeed.urls == ["http://site.test/"]
    security = result.verdict.blocks[Block.SECURITY]
    assert [item.finding for item in security.findings] == [Finding.HTTPS_AFTER_REDIRECT]
    assert security.grade is Grade.FIX
    assert result.verdict.summary is SummaryKind.ONLY_FIX


# --- Поправка 4: PageSpeed не называет плохой сертификат отдельным кодом ---

async def test_failed_document_request_with_bad_cert_before_is_cert_blocks():
    expired = TlsFacts("site.test", TlsOutcome.EXPIRED, cert(days_left=-3))
    pagespeed = FakePageSpeed(error=LighthouseFailure("FAILED_DOCUMENT_REQUEST", None))
    result = await Pipeline(FakeProbes(tls={"site.test": expired}), pagespeed, FakeClock()).run(target())
    assert result.verdict.summary is SummaryKind.CERT_BLOCKS


async def test_failed_document_request_with_good_cert_before_stays_unreachable():
    pagespeed = FakePageSpeed(error=LighthouseFailure("FAILED_DOCUMENT_REQUEST", None))
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(FakeProbes(tls={"site.test": OK_TLS}), pagespeed, FakeClock()).run(target())
    assert failed.value.code == "unreachable"


# --- Поправка 5: адрес не уходит в PageSpeed, пока DNS не проверен в пределах срока «до замера» ---

async def test_dns_timeout_within_probe_budget_fails_before_pagespeed(monkeypatch):
    monkeypatch.setattr(pipeline_module, "PROBE_TIMEOUT_SECONDS", 0.01)

    class HangingDnsProbes(FakeProbes):
        async def resolve(self, host):
            await asyncio.sleep(0.05)
            return await super().resolve(host)

    pagespeed = FakePageSpeed(PAGE)
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(HangingDnsProbes(), pagespeed, FakeClock()).run(target())
    assert (failed.value.code, failed.value.reached_measurement, failed.value.reason) == ("service_down", False, "dns")
    assert pagespeed.urls == []


async def test_timeout_after_dns_falls_back_to_measuring_without_own_tls(monkeypatch):
    monkeypatch.setattr(pipeline_module, "PROBE_TIMEOUT_SECONDS", 0.01)

    class HangingTlsProbes(FakeProbes):
        async def check_tls(self, host):
            await asyncio.sleep(0.05)
            return await super().check_tls(host)

    pagespeed = FakePageSpeed(PAGE)
    result = await Pipeline(HangingTlsProbes(), pagespeed, FakeClock()).run(target())
    assert pagespeed.urls == ["https://site.test/"]
    assert result.verdict.blocks[Block.SECURITY].unknown_reason is UnknownReason.OWN_CHECKS_FAILED


# --- Обзор задачи 14, находка 1: 443, который тихо роняет пакеты, не должен блокировать проверку 80 ---

@pytest.mark.parametrize("redirect_state", [RedirectState.NO_REDIRECT, RedirectState.REDIRECTS])
async def test_hanging_tls_check_falls_back_to_http_within_its_own_budget(monkeypatch, redirect_state):
    """AddressGuard.open_stream может копить до 15 с (DNS + подключение) на один check_tls — у самой TLS-проверки
    внутри measure() свой короткий срок (ТЗ С5), чтобы на проверку порта 80 остался запас общего бюджета."""
    monkeypatch.setattr(probe_module, "TLS_CHECK_TIMEOUT_SECONDS", 0.02)

    class HangingTlsProbes(FakeProbes):
        async def check_tls(self, host):
            await asyncio.sleep(0.05)
            raise AssertionError("TLS-проверка не должна была дождаться ответа — сработать должен свой срок")

    probes = HangingTlsProbes(redirects={"site.test": redirect_state})
    result = await probe(target(), probes)
    assert result.url == "http://site.test/"
    assert result.tls.outcome is TlsOutcome.NO_HTTPS


async def test_measure_step_gets_only_the_remaining_probe_budget_after_dns(monkeypatch):
    """FakeClock сам не движется между шагами — если бы remaining пересчитывался как новый полный бюджет
    (а не остаток общего срока «до замера»), этот тест прошёл бы даже при такой ошибке."""
    monkeypatch.setattr(pipeline_module, "PROBE_TIMEOUT_SECONDS", 0.1)
    clock = FakeClock()

    class AdvancingProbes(FakeProbes):
        async def resolve(self, host):
            clock.advance(0.08)  # «DNS отняло» 0,08 с из бюджета в 0,1 с — на TLS-шаг остаётся 0,02 с
            return await super().resolve(host)

        async def check_tls(self, host):
            await asyncio.sleep(0.05)  # дольше остатка (0,02 с), но короче полного бюджета (0,1 с)
            return await super().check_tls(host)

    probes = AdvancingProbes(tls={"site.test": OK_TLS})
    result = await Pipeline(probes, FakePageSpeed(PAGE), clock).run(target())
    assert result.verdict.blocks[Block.SECURITY].unknown_reason is UnknownReason.OWN_CHECKS_FAILED


# --- Поправка 6 (tls_check.py) — тесты в tests/site_check/test_tls_check.py ---


# --- Поправка 7: итоговый хост юникодом не роняет проверки после замера ---

async def test_unicode_final_host_that_fails_idna_does_not_crash_after_checks():
    probes = FakeProbes(tls={"site.test": OK_TLS}, redirects={"site.test": RedirectState.REDIRECTS})
    bad_host_url = "https://exa≠mple.test/"
    pagespeed = FakePageSpeed(lighthouse(final_url=bad_host_url, **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    assert result.security.redirects == (RedirectState.REDIRECTS, RedirectState.UNKNOWN)
    assert ("tls", "exa≠mple.test") not in probes.calls


async def test_unicode_final_host_that_translates_reaches_checks_in_punycode():
    """Обзор задачи 14, находка 2: юникодный, но переводимый хост доходит до check_tls/check_redirect уже
    в punycode — не как есть, юникодом."""
    ascii_final_host = "xn--e1afmkfd.xn--p1ai"
    probes = FakeProbes(
        tls={"site.test": OK_TLS, ascii_final_host: TlsFacts(ascii_final_host, TlsOutcome.OK, cert())},
        redirects={"site.test": RedirectState.REDIRECTS, ascii_final_host: RedirectState.REDIRECTS},
    )
    pagespeed = FakePageSpeed(lighthouse(final_url="https://пример.рф/", **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    assert ("tls", ascii_final_host) in probes.calls
    assert ("redirect", ascii_final_host) in probes.calls
    assert result.security.tls[-1].host == ascii_final_host


# --- Поправка 8: пустой замер не даёт ложное «всё хорошо» ---

async def test_empty_measurement_gives_no_report():
    empty_page = lighthouse(viewport_insight=audit(mode="error"))
    pagespeed = FakePageSpeed(empty_page)
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(FakeProbes(tls={"site.test": OK_TLS}), pagespeed, FakeClock()).run(target())
    assert (failed.value.code, failed.value.reached_measurement) == (MEASURE_FAILED, True)


# --- Поправка 9: разбор ответа не роняет проверку ---

async def test_broken_pagespeed_response_does_not_crash_the_check():
    pagespeed = FakePageSpeed(result=None)  # подложенный битый ответ: parse_lighthouse получит не словарь
    with pytest.raises(CheckFailed) as failed:
        await Pipeline(FakeProbes(tls={"site.test": OK_TLS}), pagespeed, FakeClock()).run(target())
    assert (failed.value.code, failed.value.reached_measurement) == (MEASURE_FAILED, True)


# --- Поправка 10: дата «сегодня» — по UTC, без сдвига на локальные сутки ---

async def test_today_for_cert_expiry_uses_utc_not_a_local_day_shift():
    clock = FakeClock(now=datetime(2026, 9, 25, 23, 30, tzinfo=UTC))
    ok_but_at_the_edge = TlsFacts("site.test", TlsOutcome.OK, cert(days_left=14))  # ровно порог CERT_WARN_DAYS
    probes = FakeProbes(tls={"site.test": ok_but_at_the_edge}, redirects={"site.test": RedirectState.REDIRECTS})
    result = await Pipeline(probes, FakePageSpeed(PAGE), clock).run(target())
    assert result.verdict.blocks[Block.SECURITY].grade is Grade.GOOD


# --- Поправка 11 (verdict.py) — тест в tests/site_check/test_verdict.py ---


# --- Обзор задачи 14, находка 3: своя NO_HTTPS устаревает, если PageSpeed открыл https на том же хосте ---

class ImprovingTlsProbes(FakeProbes):
    """Первая проверка (до замера) — CONNECT_FAILED, все следующие — OK: имитирует TLS, который наш «почерк» не
    проходит, а настоящий браузер (PageSpeed) открывает нормально."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tls_calls = 0

    async def check_tls(self, host):
        self._tls_calls += 1
        self.calls.append(("tls", host))
        if self._tls_calls == 1:
            return TlsFacts(host, TlsOutcome.CONNECT_FAILED)
        return TlsFacts(host, TlsOutcome.OK, cert())


async def test_same_host_https_evidence_from_pagespeed_overrides_stale_no_https():
    probes = ImprovingTlsProbes(redirects={"site.test": RedirectState.REDIRECTS})
    pagespeed = FakePageSpeed(lighthouse(final_url="https://site.test/", **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    findings = [item.finding for item in result.verdict.blocks[Block.SECURITY].findings]
    assert Finding.NO_HTTPS not in findings
    assert result.verdict.blocks[Block.SECURITY].grade is Grade.GOOD


async def test_same_host_recheck_still_unreachable_leaves_security_unknown():
    probes = FakeProbes(tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED)},
                        redirects={"site.test": RedirectState.REDIRECTS})
    pagespeed = FakePageSpeed(lighthouse(final_url="https://site.test/", **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    security = result.verdict.blocks[Block.SECURITY]
    assert (security.grade, security.unknown_reason) == (Grade.UNKNOWN, UnknownReason.OWN_CHECKS_FAILED)


async def test_different_host_redirect_keeps_https_after_redirect_finding():
    """Контроль: переадресация на ДРУГОЙ хост не должна попадать под перепроверку находки 3 — здесь остаётся
    находка HTTPS_AFTER_REDIRECT из поправки 3."""
    probes = FakeProbes(
        tls={"site.test": TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),
             "www.site.test": TlsFacts("www.site.test", TlsOutcome.OK, cert())},
        redirects={"site.test": RedirectState.REDIRECTS, "www.site.test": RedirectState.REDIRECTS},
    )
    pagespeed = FakePageSpeed(lighthouse(final_url="https://www.site.test/", **PAGE_AUDITS))
    result = await Pipeline(probes, pagespeed, FakeClock()).run(target())
    security = result.verdict.blocks[Block.SECURITY]
    assert [item.finding for item in security.findings] == [Finding.HTTPS_AFTER_REDIRECT]
    assert security.grade is Grade.FIX
