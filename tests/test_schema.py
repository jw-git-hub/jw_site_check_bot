from sqlalchemy import text

from bot.core.db import create_engine, migrate
from bot.schema import MIGRATIONS

NEW_GRADES = {"grade_search", "grade_preview", "grade_readability"}
ADDITIVE_PREFIXES = ("CREATE TABLE", "CREATE INDEX", "ALTER TABLE")


async def test_version_one_database_gets_new_grade_columns_and_keeps_rows(tmp_path):
    engine = create_engine(tmp_path / "data")
    await migrate(engine, MIGRATIONS[:1], tmp_path / "backups", "t")
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO users VALUES (77, 'ru', 0, 'channel', 'channel', 'x', 'x')"))
        await connection.execute(text("INSERT INTO checks (user_id, source, status, created_at) "
                                      "VALUES (77, 'channel', 'done', 'x')"))
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "t")
    async with engine.connect() as connection:
        columns = {row[1] for row in await connection.execute(text("PRAGMA table_info(checks)"))}
        count = (await connection.execute(text("SELECT COUNT(*) FROM checks"))).scalar_one()
    await engine.dispose()
    assert NEW_GRADES <= columns and count == 1
    assert list((tmp_path / "backups").glob("pre-v3-*.db"))


async def test_version_two_database_gets_the_contacts_grade(tmp_path):
    engine = create_engine(tmp_path / "data")
    await migrate(engine, MIGRATIONS[:2], tmp_path / "backups", "t")
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "t")
    async with engine.connect() as connection:
        columns = {row[1] for row in await connection.execute(text("PRAGMA table_info(checks)"))}
    await engine.dispose()
    assert "grade_contacts" in columns and list((tmp_path / "backups").glob("pre-v3-*.db"))


def test_migrations_only_add():
    for statement in (statement for migration in MIGRATIONS for statement in migration):
        assert statement.lstrip().upper().startswith(ADDITIVE_PREFIXES), statement
        assert "DROP" not in statement.upper()
