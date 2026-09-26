"""Проверки в базе (ТЗ, раздел 11): статус, оценки, ключевые цифры, списание. Адреса — без параметров.

Пользователь должен быть в базе до записи проверки: `checks.user_id` ссылается на `users(user_id)`, внешние ключи
включены на каждом соединении (bot/core/db.py). `create` сам его не создаёт — это обязанность вызывающего
(вызвать `Users.touch` раньше), контракт закреплён тестом на ошибку целостности.
"""
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.core.clock import Clock, from_iso, to_iso
from bot.site_check.lighthouse import strip_params
from bot.site_check.pipeline import CheckResult
from bot.site_check.url_input import Target
from bot.site_check.verdict import Block, SummaryKind

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
INTERRUPTED = "interrupted"
RECENT_LIMIT = 5
SUMMARY_CODES = {SummaryKind.ALL_GOOD: "ok", SummaryKind.GOOD_WITH_UNKNOWN: "ok", SummaryKind.ONLY_FIX: "fix",
                 SummaryKind.HAS_BAD: "bad", SummaryKind.CERT_BLOCKS: "bad"}
GRADE_COLUMNS = ("grade_speed", "grade_mobile", "grade_security", "grade_images")

# Статусы — только параметрами запроса, литералов вроде 'queued' в SQL не остаётся.
INSERT_CHECK = """
INSERT INTO checks (user_id, source, domain, url, status, error_code, chat_id, message_id, created_at)
VALUES (:user_id, :source, :domain, :url, :status, :error_code, :chat_id, :message_id, :now) RETURNING id"""
MARK_RUNNING = "UPDATE checks SET status = :status, started_at = :now WHERE id = :id"
FINISH_DONE = """
UPDATE checks SET status = :status, final_url = :final_url, grade_speed = :speed, grade_mobile = :mobile,
  grade_security = :security, grade_images = :images, summary = :summary, metrics_json = :metrics, charged = 1,
  finished_at = :now
WHERE id = :id"""
FINISH_FAILED = """
UPDATE checks SET status = :status, error_code = :code, charged = :charged, finished_at = :now WHERE id = :id"""
COUNT_CHARGED = "SELECT COUNT(*) FROM checks WHERE charged = 1 AND created_at >= :since"
OLDEST_CHARGED = "SELECT MIN(created_at) FROM checks WHERE charged = 1 AND created_at >= :since AND user_id = :user_id"
UNFINISHED = """
SELECT checks.id, checks.chat_id, checks.message_id, users.lang FROM checks JOIN users USING (user_id)
WHERE checks.status IN (:queued, :running) AND checks.message_id IS NOT NULL"""
INTERRUPT = """
UPDATE checks SET status = :interrupted, error_code = :interrupted, charged = 0, finished_at = :now
WHERE status IN (:queued, :running)"""
RECENT_FOR_DOMAIN = """
SELECT created_at, source, status, error_code, grade_speed, grade_mobile, grade_security, grade_images, summary,
  metrics_json
FROM checks WHERE domain = :domain ORDER BY created_at DESC, id DESC LIMIT :limit"""
STATS_BY_SOURCE = """
SELECT source, COUNT(*) AS checks, COUNT(DISTINCT CASE WHEN status = :done THEN user_id END) AS reported_users,
  SUM(CASE WHEN error_code IS NOT NULL THEN 1 ELSE 0 END) AS refusals
FROM checks WHERE created_at >= :since GROUP BY source"""
NEW_USERS_BY_SOURCE = """
SELECT first_source AS source, COUNT(*) AS new_users FROM users WHERE created_at >= :since GROUP BY first_source"""
REFUSALS_BY_CODE = """
SELECT error_code, COUNT(*) AS total FROM checks WHERE created_at >= :since AND error_code IS NOT NULL
GROUP BY error_code ORDER BY total DESC, error_code"""


@dataclass(frozen=True)
class NewCheck:
    user_id: int
    source: str
    target: Target | None
    status: str
    error_code: str | None = None
    chat_id: int | None = None
    message_id: int | None = None


@dataclass(frozen=True)
class Unfinished:
    check_id: int
    chat_id: int
    message_id: int
    lang: str


@dataclass(frozen=True)
class DomainCheck:
    created_at: datetime
    source: str
    status: str
    error_code: str | None
    grades: tuple[str | None, ...]
    summary: str | None
    metrics: dict[str, Any]


@dataclass(frozen=True)
class LabelStats:
    source: str
    new_users: int
    reported_users: int
    checks: int
    refusals: int


def metrics_summary(result: CheckResult) -> dict[str, Any]:
    """Ключевые цифры для /site и «Цифр для поста» — до 4 КБ, адреса без параметров."""
    summary: dict[str, Any] = {"final_url": strip_params(result.final_url)}
    page = result.page
    if page:
        summary |= {"lighthouse": page.lighthouse_version, "lcp_ms": page.speed.lcp_ms,
                    "page_bytes": page.images.page_bytes, "image_bytes": page.images.image_bytes,
                    "heaviest": [[item.name, item.bytes] for item in page.images.heaviest]}
    certs = [facts.cert for facts in reversed(result.security.tls) if facts.cert]
    if certs:
        summary["cert_until"] = certs[0].not_after.date().isoformat()
    return summary


class ChecksRepo:
    def __init__(self, engine: AsyncEngine, clock: Clock):
        self._engine = engine
        self._clock = clock

    def _now(self) -> str:
        return to_iso(self._clock.now())

    async def _write(self, sql: str, params: dict[str, Any]) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(text(sql), params)

    async def create(self, new: NewCheck) -> int:
        params = {"user_id": new.user_id, "source": new.source, "status": new.status, "error_code": new.error_code,
                  "chat_id": new.chat_id, "message_id": new.message_id, "now": self._now(),
                  "domain": new.target.display_host if new.target else None,
                  "url": new.target.stored_url if new.target else None}
        async with self._engine.begin() as connection:
            return (await connection.execute(text(INSERT_CHECK), params)).scalar_one()

    async def mark_running(self, check_id: int) -> None:
        await self._write(MARK_RUNNING, {"id": check_id, "now": self._now(), "status": RUNNING})

    async def finish_done(self, check_id: int, result: CheckResult) -> None:
        grades = {block.value: result.verdict.blocks[block].grade.value for block in Block}
        params = {"id": check_id, "now": self._now(), "status": DONE, "final_url": strip_params(result.final_url),
                  "summary": SUMMARY_CODES[result.verdict.summary],
                  "metrics": json.dumps(metrics_summary(result), ensure_ascii=False), **grades}
        await self._write(FINISH_DONE, params)

    async def finish_failed(self, check_id: int, code: str, charged: bool) -> None:
        await self._write(FINISH_FAILED, {"id": check_id, "code": code, "charged": int(charged), "now": self._now(),
                                          "status": FAILED})

    async def count_charged_since(self, since: datetime, user_id: int | None = None) -> int:
        sql = COUNT_CHARGED + ("" if user_id is None else " AND user_id = :user_id")
        async with self._engine.connect() as connection:
            return (await connection.execute(text(sql), {"since": to_iso(since), "user_id": user_id})).scalar_one()

    async def oldest_charged_since(self, since: datetime, user_id: int) -> datetime | None:
        async with self._engine.connect() as connection:
            value = (await connection.execute(text(OLDEST_CHARGED), {"since": to_iso(since), "user_id": user_id})).scalar()
        return from_iso(value) if value else None

    async def interrupt_unfinished(self) -> list[Unfinished]:
        params = {"queued": QUEUED, "running": RUNNING}
        async with self._engine.begin() as connection:
            rows = (await connection.execute(text(UNFINISHED), params)).all()
            await connection.execute(text(INTERRUPT), {**params, "interrupted": INTERRUPTED, "now": self._now()})
        return [Unfinished(row.id, row.chat_id, row.message_id, row.lang) for row in rows]

    async def recent_for_domain(self, domain: str) -> list[DomainCheck]:
        async with self._engine.connect() as connection:
            rows = (await connection.execute(text(RECENT_FOR_DOMAIN), {"domain": domain, "limit": RECENT_LIMIT})).all()
        return [_domain_check(row) for row in rows]

    async def stats(self, since: datetime) -> tuple[list[LabelStats], list[tuple[str, int]]]:
        params = {"since": to_iso(since)}
        async with self._engine.connect() as connection:
            by_source = (await connection.execute(text(STATS_BY_SOURCE), {**params, "done": DONE})).all()
            new_users = dict((await connection.execute(text(NEW_USERS_BY_SOURCE), params)).all())
            refusals = [(row.error_code, row.total) for row in await connection.execute(text(REFUSALS_BY_CODE), params)]
        return _label_stats(by_source, new_users), refusals


def _domain_check(row) -> DomainCheck:
    grades = tuple(getattr(row, column) for column in GRADE_COLUMNS)
    metrics = json.loads(row.metrics_json) if row.metrics_json else {}
    return DomainCheck(from_iso(row.created_at), row.source, row.status, row.error_code, grades, row.summary, metrics)


def _label_stats(by_source, new_users: dict[str, int]) -> list[LabelStats]:
    rows = {row.source: row for row in by_source}
    labels = sorted(set(rows) | set(new_users))
    return [LabelStats(label, new_users.get(label, 0), rows[label].reported_users if label in rows else 0,
                       rows[label].checks if label in rows else 0, rows[label].refusals if label in rows else 0)
            for label in labels]
