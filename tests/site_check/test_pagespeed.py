import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.site_check.pagespeed import (FIELDS, KEY_HEADER, RETRY_MIN_REMAINING_SECONDS, LighthouseFailure,
                                      PageSpeedClient, PageSpeedUnavailable, classify, interpret)
from tests.fakes import FakeClock, fake_google_key

RESULT = {"lighthouseResult": {"lighthouseVersion": "13.5.0", "finalDisplayedUrl": "https://site.test/", "audits": {}}}
FIXTURES = sorted((Path(__file__).parents[1] / "fixtures" / "pagespeed").glob("*.json"))
EXPECTED_FAILURES = {"not_found": "not_found", "cert": "cert_blocks", "no_domain": "unreachable_dns"}


def lighthouse_error(code: str, page_status: int | None = None) -> dict:
    tail = f" (Status code: {page_status})" if page_status else ""
    message = f"Lighthouse returned error: {code}. Lighthouse was unable to reliably load the page you requested.{tail}"
    return {"error": {"code": 500, "message": message, "errors": [{"reason": "lighthouseError"}]}}


class FakePageSpeed:
    """Отвечает заготовками по очереди и запоминает запросы."""

    def __init__(self, *answers: tuple[int, dict | str], delay: float = 0.0):
        self.answers = list(answers)
        self.requests: list[web.Request] = []
        self.delay = delay

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        status, body = self.answers.pop(0)
        text = body if isinstance(body, str) else json.dumps(body)
        return web.Response(status=status, text=text, content_type="application/json")


@pytest.fixture
async def run_client():
    opened = []

    async def run(fake: FakePageSpeed, seconds_left: float = 120, **options):
        app = web.Application()
        app.router.add_get("/psi", fake.handle)
        server, session = TestServer(app), aiohttp.ClientSession()
        opened.append((server, session))
        await server.start_server()
        clock = FakeClock()
        client = PageSpeedClient(session, fake_google_key(), clock, endpoint=str(server.make_url("/psi")), **options)
        return await client.run("https://site.test/", deadline=clock.monotonic() + seconds_left)

    yield run
    for server, session in opened:
        await session.close()
        await server.close()


async def test_success_sends_key_in_header_and_asks_only_needed_fields(run_client):
    fake = FakePageSpeed((200, RESULT))
    assert (await run_client(fake))["lighthouseVersion"] == "13.5.0"
    request = fake.requests[0]
    assert request.headers[KEY_HEADER] == fake_google_key()
    assert "key" not in request.query
    assert request.query["fields"] == FIELDS
    assert request.query["strategy"] == "mobile"
    assert request.query.getall("category") == ["performance", "accessibility", "best-practices"]


async def test_lighthouse_error_in_500_is_site_failure_without_retry(run_client):
    fake = FakePageSpeed((500, lighthouse_error("ERRORED_DOCUMENT_REQUEST", 403)))
    with pytest.raises(LighthouseFailure) as failure:
        await run_client(fake)
    assert (failure.value.code, failure.value.page_status) == ("ERRORED_DOCUMENT_REQUEST", 403)
    assert len(fake.requests) == 1


async def test_runtime_error_inside_200_is_site_failure(run_client):
    body = {"lighthouseResult": {"runtimeError": {"code": "NO_FCP", "message": "No content"}}}
    with pytest.raises(LighthouseFailure, match="NO_FCP"):
        await run_client(FakePageSpeed((200, body)))


async def test_quota_is_our_problem_without_retry(run_client):
    fake = FakePageSpeed((429, {"error": {"code": 429, "message": "Quota exceeded"}}))
    with pytest.raises(PageSpeedUnavailable, match="quota"):
        await run_client(fake)
    assert len(fake.requests) == 1


async def test_rejected_key_is_reported(run_client):
    body = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}
    with pytest.raises(PageSpeedUnavailable, match="key"):
        await run_client(FakePageSpeed((400, body)))


async def test_server_error_is_retried_once(run_client):
    fake = FakePageSpeed((503, "Service Unavailable"), (200, RESULT))
    assert (await run_client(fake))["lighthouseVersion"] == "13.5.0"
    assert len(fake.requests) == 2


async def test_two_server_errors_give_up(run_client):
    with pytest.raises(PageSpeedUnavailable):
        await run_client(FakePageSpeed((503, "x"), (503, "x")))


async def test_something_went_wrong_without_lighthouse_prefix_is_retried(run_client):
    """Статус ниже 500 (не quota, не отказ ключа) с «Something went wrong» — тоже повтор, не наша ошибка сразу."""
    body = {"error": {"code": 400, "message": "Oops, Something went wrong on our end. Please try again."}}
    fake = FakePageSpeed((400, body), (200, RESULT))
    assert (await run_client(fake))["lighthouseVersion"] == "13.5.0"
    assert len(fake.requests) == 2


async def test_something_went_wrong_with_lighthouse_prefix_is_retried(run_client):
    body = {"error": {"code": 500, "message": "Lighthouse returned error: Something went wrong."}}
    fake = FakePageSpeed((500, body), (200, RESULT))
    assert (await run_client(fake))["lighthouseVersion"] == "13.5.0"
    assert len(fake.requests) == 2


async def test_no_retry_when_little_time_left(run_client):
    fake = FakePageSpeed((503, "x"), (200, RESULT))
    with pytest.raises(PageSpeedUnavailable):
        await run_client(fake, seconds_left=30)
    assert len(fake.requests) == 1


async def test_no_retry_when_exactly_forty_seconds_left(run_client):
    """ТЗ 5.1: повтор только если до срока осталось *больше* 40 секунд — ровно 40 повтора не даёт."""
    fake = FakePageSpeed((503, "x"), (200, RESULT))
    with pytest.raises(PageSpeedUnavailable):
        await run_client(fake, seconds_left=RETRY_MIN_REMAINING_SECONDS)
    assert len(fake.requests) == 1


async def test_slow_answer_is_site_timeout(run_client):
    with pytest.raises(LighthouseFailure, match="TIMEOUT"):
        await run_client(FakePageSpeed((200, RESULT), delay=0.5), request_timeout=0.1)


async def test_huge_answer_is_refused(run_client):
    with pytest.raises(PageSpeedUnavailable, match="too_large"):
        await run_client(FakePageSpeed((200, RESULT)), max_bytes=10)


class _ConnectTimeoutRequest:
    """Контекст-менеджер запроса, который никогда не достучится до Google — рвётся уже на соединении."""

    async def __aenter__(self):
        raise aiohttp.ConnectionTimeoutError("connect timeout")

    async def __aexit__(self, *exc_info):
        return False


class ConnectTimeoutSession:
    """Подделка сессии aiohttp: каждый запрос падает на соединении с Google, а не с сайтом."""

    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return _ConnectTimeoutRequest()


async def test_connection_timeout_to_google_is_our_problem_not_the_site():
    """Обрыв/чёрная дыра сети до самого Google — не тайм-аут сайта: повтор, затем service_down (ТЗ 5.1)."""
    session = ConnectTimeoutSession()
    clock = FakeClock()
    client = PageSpeedClient(session, fake_google_key(), clock, endpoint="https://example.invalid/psi")
    with pytest.raises(PageSpeedUnavailable):
        await client.run("https://site.test/", deadline=clock.monotonic() + 120)
    assert session.calls == 2


@pytest.mark.parametrize(("code", "status", "expected"), [
    ("DNS_FAILURE", None, "unreachable_dns"), ("FAILED_DOCUMENT_REQUEST", None, "unreachable"),
    ("ERRORED_DOCUMENT_REQUEST", 403, "blocked"), ("ERRORED_DOCUMENT_REQUEST", 401, "blocked"),
    ("ERRORED_DOCUMENT_REQUEST", 429, "blocked"), ("ERRORED_DOCUMENT_REQUEST", 404, "not_found"),
    ("ERRORED_DOCUMENT_REQUEST", 502, "server_error"), ("ERRORED_DOCUMENT_REQUEST", 410, "measure_failed"),
    ("NO_FCP", None, "timeout"), ("PAGE_HUNG", None, "timeout"), ("TIMEOUT", None, "timeout"),
    ("NOT_HTML", None, "not_html"), ("CHROME_INTERSTITIAL_ERROR", None, "cert_blocks"),
    ("INSECURE_DOCUMENT_REQUEST", None, "cert_blocks"), ("NO_LCP", None, "measure_failed"),
])
def test_classify(code, status, expected):
    assert classify(LighthouseFailure(code, status)) == expected


@pytest.mark.skipif(not FIXTURES, reason="нет записанных ответов PageSpeed (задача 2)")
@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_recorded_answers_are_understood(path):
    recorded = json.loads(path.read_text(encoding="utf-8"))
    body = json.dumps(recorded["response"]).encode()
    expected = EXPECTED_FAILURES.get(path.stem)
    if expected is None:
        assert "audits" in interpret(recorded["http_status"], body)
        return
    with pytest.raises(LighthouseFailure) as failure:
        interpret(recorded["http_status"], body)
    assert classify(failure.value) == expected
