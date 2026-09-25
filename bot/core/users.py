"""Люди: язык и метка источника (ТЗ, раздел 11). Хранится только user_id — ни имён, ни юзернеймов."""
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.core.clock import Clock, to_iso
from bot.core.i18n import Lang, detect_lang
from bot.core.stats import best_effort

LABEL_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
DIRECT_LABEL = "direct"
INVALID_LABEL = "invalid"

UPSERT_USER = """
INSERT INTO users (user_id, lang, lang_manual, first_source, last_source, created_at, last_seen_at)
VALUES (:user_id, :lang, 0, :source, :source, :now, :now)
ON CONFLICT(user_id) DO UPDATE SET
  last_seen_at = excluded.last_seen_at,
  lang = CASE WHEN users.lang_manual THEN users.lang ELSE excluded.lang END,
  last_source = CASE WHEN :has_label THEN excluded.last_source ELSE users.last_source END
RETURNING lang, lang_manual, last_source
"""
SET_LANG = "UPDATE users SET lang = :lang, lang_manual = 1 WHERE user_id = :user_id"


def parse_label(payload: str | None) -> str:
    """Метка из /start <метка>: латиница, цифры, _ и -, до 64 знаков (ТЗ, раздел 11)."""
    if not payload:
        return DIRECT_LABEL
    return payload if LABEL_PATTERN.fullmatch(payload) else INVALID_LABEL


@dataclass(frozen=True)
class User:
    user_id: int
    lang: Lang
    lang_manual: bool
    last_source: str


class Users:
    def __init__(self, engine: AsyncEngine, clock: Clock):
        self._engine = engine
        self._clock = clock

    async def touch(self, user_id: int, language_code: str | None, label: str | None = None) -> User:
        """Отметить визит; label — только из /start. Сбой базы — человек по language_code (ТЗ, Р13)."""
        fallback = User(user_id, detect_lang(language_code), False, label or DIRECT_LABEL)
        return await best_effort(self._upsert(user_id, language_code, label), "визит человека", fallback)

    async def set_lang(self, user_id: int, lang: Lang) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(text(SET_LANG), {"lang": lang, "user_id": user_id})

    async def _upsert(self, user_id: int, language_code: str | None, label: str | None) -> User:
        params = {"user_id": user_id, "lang": detect_lang(language_code), "source": label or DIRECT_LABEL,
                  "has_label": label is not None, "now": to_iso(self._clock.now())}
        async with self._engine.begin() as connection:
            row = (await connection.execute(text(UPSERT_USER), params)).one()
        return User(user_id, row.lang, bool(row.lang_manual), row.last_source)
