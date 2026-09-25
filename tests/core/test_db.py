from datetime import date

import pytest
from sqlalchemy import text

from bot.core.db import DatabaseTooNew, backup_daily, create_engine, current_version, migrate
from bot.core.stats import best_effort
from bot.schema import MIGRATIONS


@pytest.fixture
async def engine(tmp_path):
    engine = create_engine(tmp_path / "data")
    yield engine
    await engine.dispose()


async def table_names(engine) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
        return {row[0] for row in rows}


async def test_fresh_database_gets_current_schema_without_copy(engine, tmp_path):
    assert await migrate(engine, MIGRATIONS, tmp_path / "backups", "t") == len(MIGRATIONS)
    assert {"users", "checks"} <= await table_names(engine)
    assert await current_version(engine) == len(MIGRATIONS)
    assert not (tmp_path / "backups").exists()


async def test_second_run_changes_nothing(engine, tmp_path):
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "t")
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "t")
    assert await current_version(engine) == len(MIGRATIONS)


async def test_database_newer_than_code_refuses_start(engine, tmp_path):
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "t")
    with pytest.raises(DatabaseTooNew):
        await migrate(engine, (), tmp_path / "backups", "t")


async def test_upgrade_makes_copy_first(engine, tmp_path):
    first = (("CREATE TABLE a (x INTEGER)",),)
    await migrate(engine, first, tmp_path / "backups", "t")
    await migrate(engine, first + (("CREATE TABLE b (y INTEGER)",),), tmp_path / "backups", "stamp")
    assert (tmp_path / "backups" / "pre-v2-stamp.db").exists()
    assert {"a", "b"} <= await table_names(engine)


async def test_failed_version_rolls_back_entirely(engine, tmp_path):
    broken = (("CREATE TABLE a (x INTEGER)", "CREATE TABLE a (x INTEGER)"),)
    with pytest.raises(Exception):
        await migrate(engine, broken, tmp_path / "backups", "t")
    assert "a" not in await table_names(engine)
    assert await current_version(engine) == 0


async def test_daily_copies_keep_last_seven(engine, tmp_path):
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "t")
    for day in range(1, 10):
        await backup_daily(engine, tmp_path / "backups", date(2026, 9, day))
    kept = sorted(path.name for path in (tmp_path / "backups").glob("bot-*.db"))
    assert kept == [f"bot-2026-09-0{day}.db" for day in range(3, 10)]


async def test_best_effort_returns_fallback_instead_of_error():
    async def broken():
        raise RuntimeError("база недоступна")

    assert await best_effort(broken(), "проба", fallback="запасной") == "запасной"
