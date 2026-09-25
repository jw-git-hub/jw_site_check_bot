import pytest

from bot.core.db import create_engine, migrate
from bot.schema import MIGRATIONS


@pytest.fixture
async def db(tmp_path):
    engine = create_engine(tmp_path / "data")
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "test")
    yield engine
    await engine.dispose()
