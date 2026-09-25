"""База: SQLite через SQLAlchemy и aiosqlite (ТЗ, раздел 11).

- WAL, busy_timeout и внешние ключи — на каждом соединении, как в загрузчике.
- Схема по версиям (PRAGMA user_version). Миграции только добавляющие, каждая — одной транзакцией.
- База новее, чем знает код, — отказ запуска. Перед миграцией — копия VACUUM INTO.
"""
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

DB_FILE_NAME = "bot.db"
BACKUP_DIR_NAME = "backups"
DAILY_BACKUP_PREFIX = "bot-"
DAILY_BACKUPS_KEPT = 7
BUSY_TIMEOUT_MS = 10_000
PRAGMAS = ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON", f"busy_timeout={BUSY_TIMEOUT_MS}")

Migration = Sequence[str]


class DatabaseTooNew(Exception):
    """База новее, чем знает этот выпуск: код откатили после миграции вперёд."""


def create_engine(data_dir: Path) -> AsyncEngine:
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{data_dir / DB_FILE_NAME}")
    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine


def _apply_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    for pragma in PRAGMAS:
        cursor.execute(f"PRAGMA {pragma}")
    cursor.close()


async def current_version(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return (await connection.execute(text("PRAGMA user_version"))).scalar_one()


async def migrate(engine: AsyncEngine, migrations: Sequence[Migration], backup_dir: Path, stamp: str) -> int:
    version = await current_version(engine)
    if version > len(migrations):
        raise DatabaseTooNew(f"база версии {version}, этот выпуск знает до {len(migrations)}")
    if 0 < version < len(migrations):
        await backup(engine, backup_dir / f"pre-v{len(migrations)}-{stamp}.db")
    for number, statements in enumerate(migrations[version:], start=version + 1):
        await _apply(engine, number, statements)
    return len(migrations)


async def _apply(engine: AsyncEngine, number: int, statements: Migration) -> None:
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text("BEGIN IMMEDIATE"))
        try:
            await _run_all(autocommit, statements, number)
        except BaseException:
            await autocommit.execute(text("ROLLBACK"))
            raise
        await autocommit.execute(text("COMMIT"))


async def _run_all(connection: AsyncConnection, statements: Migration, number: int) -> None:
    for statement in statements:
        await connection.execute(text(statement))
    await connection.execute(text(f"PRAGMA user_version = {number}"))


async def backup(engine: AsyncEngine, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text("VACUUM INTO :path"), {"path": str(target)})


def daily_backup_path(backup_dir: Path, day: date) -> Path:
    return backup_dir / f"{DAILY_BACKUP_PREFIX}{day.isoformat()}.db"


async def backup_daily(engine: AsyncEngine, backup_dir: Path, day: date) -> Path:
    target = daily_backup_path(backup_dir, day)
    await backup(engine, target)
    for old in sorted(backup_dir.glob(f"{DAILY_BACKUP_PREFIX}*.db"))[:-DAILY_BACKUPS_KEPT]:
        old.unlink()
    return target
