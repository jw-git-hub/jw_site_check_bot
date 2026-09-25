"""Схема базы сайт-чекера по версиям (ТЗ, раздел 11). Миграции только добавляющие: откат кода не ломает базу."""

MIGRATIONS = (
    (
        """CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            lang TEXT NOT NULL,
            lang_manual INTEGER NOT NULL DEFAULT 0,
            first_source TEXT NOT NULL,
            last_source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL)""",
        """CREATE TABLE checks (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            source TEXT NOT NULL,
            domain TEXT,
            url TEXT,
            final_url TEXT,
            status TEXT NOT NULL,
            error_code TEXT,
            grade_speed TEXT,
            grade_mobile TEXT,
            grade_security TEXT,
            grade_images TEXT,
            summary TEXT,
            metrics_json TEXT,
            charged INTEGER NOT NULL DEFAULT 0,
            chat_id INTEGER,
            message_id INTEGER,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT)""",
        "CREATE INDEX checks_by_user_time ON checks(user_id, created_at)",
        "CREATE INDEX checks_by_domain ON checks(domain, created_at)",
        "CREATE INDEX checks_by_status ON checks(status)",
    ),
)
