import pytest
from sqlalchemy import text

from bot.core.users import DIRECT_LABEL, INVALID_LABEL, Users, parse_label
from tests.fakes import FakeClock


@pytest.mark.parametrize(("payload", "label"), [
    (None, DIRECT_LABEL), ("", DIRECT_LABEL), ("channel", "channel"), ("grp_danang", "grp_danang"),
    ("post-2026-10-01", "post-2026-10-01"), ("метка", INVALID_LABEL), ("x" * 65, INVALID_LABEL),
    ("a b", INVALID_LABEL),
])
def test_parse_label(payload, label):
    assert parse_label(payload) == label


async def test_new_user_gets_language_and_label(db):
    user = await Users(db, FakeClock()).touch(10, "uk", "channel")
    assert (user.lang, user.lang_manual, user.last_source) == ("ru", False, "channel")


async def test_visit_without_label_keeps_last_label(db):
    users = Users(db, FakeClock())
    await users.touch(10, "en", "site")
    assert (await users.touch(10, "en")).last_source == "site"


async def test_new_label_replaces_last_but_not_first(db):
    users = Users(db, FakeClock())
    await users.touch(10, "en", "site")
    assert (await users.touch(10, "en", "channel")).last_source == "channel"
    async with db.connect() as connection:
        first = (await connection.execute(text("SELECT first_source FROM users WHERE user_id = 10"))).scalar_one()
    assert first == "site"


async def test_manual_language_is_not_overwritten(db):
    users = Users(db, FakeClock())
    await users.touch(10, "en")
    await users.set_lang(10, "ru")
    assert (await users.touch(10, "en")).lang == "ru"


class BrokenEngine:
    """Движок, у которого база недоступна: любая транзакция падает."""

    def begin(self):
        raise RuntimeError("база недоступна")


async def test_broken_database_gives_user_from_language_code():
    user = await Users(BrokenEngine(), FakeClock()).touch(10, "ru", "channel")
    assert (user.lang, user.lang_manual, user.last_source) == ("ru", False, "channel")
