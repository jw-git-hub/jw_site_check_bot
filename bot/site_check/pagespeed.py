"""Клиент PageSpeed Insights API (ТЗ, 5.1).

- Ключ — в заголовке X-goog-api-key, не в адресе: адрес попадает в тексты ошибок.
- Только нужные поля (fields): без скриншотов ответ в разы меньше и бережёт память контейнера.
- Ошибки Lighthouse PageSpeed отдаёт с HTTP 500 (бывает 400), а код и статус страницы есть только в тексте.
  Поэтому сначала разбираем текст при любом статусе, и лишь потом решаем по HTTP-статусу.
"""
import json
import re
from typing import Any

import aiohttp

from bot.core.clock import Clock

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
KEY_HEADER = "X-goog-api-key"
CATEGORIES = ("performance", "accessibility", "best-practices")
AUDIT_IDS = (
    "largest-contentful-paint", "first-contentful-paint", "total-blocking-time", "cumulative-layout-shift",
    "speed-index", "server-response-time", "image-delivery-insight", "lcp-discovery-insight",
    "render-blocking-insight", "unused-javascript", "legacy-javascript-insight", "duplicated-javascript-insight",
    "document-latency-insight", "third-parties-insight", "viewport-insight", "target-size", "meta-viewport",
    "is-on-https", "total-byte-weight", "resource-summary", "network-requests",
)
RESULT_FIELDS = "lighthouseVersion,requestedUrl,finalDisplayedUrl,runtimeError,runWarnings"
FIELDS = f"lighthouseResult({RESULT_FIELDS},audits({','.join(AUDIT_IDS)}))"
REQUEST_TIMEOUT_SECONDS = 90
MIN_TIMEOUT_SECONDS = 1
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
MAX_ATTEMPTS = 2
RETRY_MIN_REMAINING_SECONDS = 40
HTTP_OK = 200
TOO_MANY_REQUESTS = 429
FIRST_SERVER_ERROR = 500
KEY_PROBLEM_STATUSES = frozenset({400, 401, 403})
KEY_PROBLEM_MARKERS = ("api key", "api_key", "permission_denied", "has not been used", "is disabled")
LIGHTHOUSE_ERROR = re.compile(r"Lighthouse returned error: ([A-Z][A-Z_]+)\b")
PAGE_STATUS = re.compile(r"Status code: (\d{3})")
SOMETHING_WRONG_MARKER = "something went wrong"
NO_ERROR = "NO_ERROR"
TIMEOUT_CODE = "TIMEOUT"
ERRORED_DOCUMENT = "ERRORED_DOCUMENT_REQUEST"
BLOCKED_STATUSES = frozenset({401, 403, 429})
NOT_FOUND_STATUS = 404
MEASURE_FAILED = "measure_failed"
ERROR_CODES = {
    "DNS_FAILURE": "unreachable_dns", "FAILED_DOCUMENT_REQUEST": "unreachable", "NO_FCP": "timeout",
    "PAGE_HUNG": "timeout", "PROTOCOL_TIMEOUT": "timeout", TIMEOUT_CODE: "timeout", "NOT_HTML": "not_html",
    "CHROME_INTERSTITIAL_ERROR": "cert_blocks", "INSECURE_DOCUMENT_REQUEST": "cert_blocks",
}


class LighthouseFailure(Exception):
    """Lighthouse не смог измерить страницу — причина на стороне сайта."""

    def __init__(self, code: str, page_status: int | None):
        super().__init__(code)
        self.code = code
        self.page_status = page_status


class PageSpeedUnavailable(Exception):
    """Сервис замеров ответил не так — причина на нашей стороне: quota, key, too_large, http NNN, network."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Retryable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PageSpeedClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, clock: Clock, *, endpoint: str = ENDPOINT,
                 request_timeout: float = REQUEST_TIMEOUT_SECONDS, max_bytes: int = MAX_RESPONSE_BYTES):
        self._session = session
        self._key = api_key
        self._clock = clock
        self._endpoint = endpoint
        self._request_timeout = request_timeout
        self._max_bytes = max_bytes

    async def run(self, url: str, deadline: float) -> dict[str, Any]:
        """lighthouseResult или исключение. deadline — момент clock.monotonic(), к которому проверка кончается."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._once(url, deadline)
            except _Retryable as error:
                if attempt == MAX_ATTEMPTS or deadline - self._clock.monotonic() <= RETRY_MIN_REMAINING_SECONDS:
                    raise PageSpeedUnavailable(error.reason) from None
        raise AssertionError("недостижимо")

    async def _once(self, url: str, deadline: float) -> dict[str, Any]:
        # Потолок — остаток дедлайна (не меньше MIN_TIMEOUT_SECONDS), запрос — не дольше request_timeout.
        seconds = min(self._request_timeout, max(MIN_TIMEOUT_SECONDS, deadline - self._clock.monotonic()))
        request = self._session.get(self._endpoint, params=_params(url), headers={KEY_HEADER: self._key},
                                    timeout=aiohttp.ClientTimeout(total=seconds))
        try:
            async with request as response:
                return interpret(response.status, await self._read(response))
        except aiohttp.ConnectionTimeoutError as error:
            # Не достучались до самого Google (обрыв/чёрная дыра сети) — это наша беда, не тайм-аут сайта.
            raise _Retryable(f"network: {type(error).__name__}") from None
        except TimeoutError:
            raise LighthouseFailure(TIMEOUT_CODE, None) from None
        except aiohttp.ClientError as error:
            raise _Retryable(f"network: {type(error).__name__}") from None

    async def _read(self, response: aiohttp.ClientResponse) -> bytes:
        body = bytearray()
        async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > self._max_bytes:
                raise PageSpeedUnavailable("too_large")
        return bytes(body)


def _params(url: str) -> list[tuple[str, str]]:
    categories = [("category", name) for name in CATEGORIES]
    return [("url", url), ("strategy", "mobile"), *categories, ("locale", "en"), ("fields", FIELDS)]


def interpret(status: int, body: bytes) -> dict[str, Any]:
    payload = _json_or_none(body)
    message = _error_message(payload)
    if SOMETHING_WRONG_MARKER in message.lower():
        raise _Retryable(f"http {status}")
    _raise_lighthouse_failure(message)
    if status == HTTP_OK and payload and "lighthouseResult" in payload:
        return _checked_result(payload["lighthouseResult"])
    _raise_service_problem(status, message)
    raise _Retryable(f"http {status}")


def _json_or_none(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _error_message(payload: dict[str, Any] | None) -> str:
    error = (payload or {}).get("error")
    return str(error.get("message", "")) if isinstance(error, dict) else ""


def _raise_lighthouse_failure(message: str) -> None:
    code = LIGHTHOUSE_ERROR.search(message)
    if code:
        raise LighthouseFailure(code.group(1), _page_status(message))


def _page_status(message: str) -> int | None:
    found = PAGE_STATUS.search(message)
    return int(found.group(1)) if found else None


def _checked_result(result: dict[str, Any]) -> dict[str, Any]:
    runtime = result.get("runtimeError") or {}
    code = runtime.get("code")
    if code and code != NO_ERROR:
        raise LighthouseFailure(code, _page_status(str(runtime.get("message", ""))))
    return result


def _raise_service_problem(status: int, message: str) -> None:
    if status == TOO_MANY_REQUESTS:
        raise PageSpeedUnavailable("quota")
    if status in KEY_PROBLEM_STATUSES and any(marker in message.lower() for marker in KEY_PROBLEM_MARKERS):
        raise PageSpeedUnavailable("key")
    if status < FIRST_SERVER_ERROR:
        raise PageSpeedUnavailable(f"http {status}")


def classify(failure: LighthouseFailure) -> str:
    """Код Lighthouse → код исхода в базе и сообщение человеку (ТЗ, 5.1)."""
    if failure.code == ERRORED_DOCUMENT:
        return _by_page_status(failure.page_status)
    return ERROR_CODES.get(failure.code, MEASURE_FAILED)


def _by_page_status(status: int | None) -> str:
    if status in BLOCKED_STATUSES:
        return "blocked"
    if status == NOT_FOUND_STATUS:
        return "not_found"
    if status is not None and status >= FIRST_SERVER_ERROR:
        return "server_error"
    return MEASURE_FAILED
