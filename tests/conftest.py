import pytest

from bot.core.config import load_settings
from bot.core.db import create_engine, migrate
from bot.schema import MIGRATIONS
from bot.settings import Settings
from tests.fakes import ADMIN_ID, fake_google_key, fake_telegram_token


@pytest.fixture
async def db(tmp_path):
    engine = create_engine(tmp_path / "data")
    await migrate(engine, MIGRATIONS, tmp_path / "backups", "test")
    yield engine
    await engine.dispose()


@pytest.fixture
def settings(tmp_path) -> Settings:
    environ = {"BOT_TOKEN": fake_telegram_token(), "PAGESPEED_API_KEY": fake_google_key(),
               "ADMIN_ID": str(ADMIN_ID), "DATA_DIR": str(tmp_path / "data")}
    return load_settings(Settings, environ)
