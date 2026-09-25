import pytest

from bot.core.config import ConfigError, find_key_typos, load_settings, within_one_edit
from bot.settings import Settings
from tests.fakes import fake_google_key, fake_telegram_token

VALID = {"BOT_TOKEN": fake_telegram_token(), "PAGESPEED_API_KEY": fake_google_key(), "ADMIN_ID": "4242"}


def test_valid_environment_loads_with_defaults():
    settings = load_settings(Settings, VALID)
    assert settings.admin_id == 4242
    assert (settings.user_daily_limit, settings.global_daily_limit) == (10, 500)
    assert (settings.check_workers, settings.queue_max, settings.open_to_all) == (2, 30, False)
    assert set(settings.secret_values()) == {VALID["BOT_TOKEN"], VALID["PAGESPEED_API_KEY"]}


def test_names_are_case_insensitive():
    lower = {key.lower(): value for key, value in VALID.items()}
    assert load_settings(Settings, lower).admin_id == 4242


def test_missing_value_names_field_without_showing_others():
    environ = {key: value for key, value in VALID.items() if key != "BOT_TOKEN"}
    with pytest.raises(ConfigError) as error:
        load_settings(Settings, environ)
    assert "bot_token" in str(error.value)
    assert VALID["PAGESPEED_API_KEY"] not in str(error.value)
    assert "4242" not in str(error.value)


def test_empty_secret_is_refused():
    with pytest.raises(ConfigError, match="pagespeed_api_key"):
        load_settings(Settings, {**VALID, "PAGESPEED_API_KEY": ""})


def test_wrong_type_is_refused_without_value():
    with pytest.raises(ConfigError) as error:
        load_settings(Settings, {**VALID, "ADMIN_ID": "не-число"})
    assert "admin_id" in str(error.value)
    assert "не-число" not in str(error.value)


def test_typo_in_key_name_is_refused():
    with pytest.raises(ConfigError, match="USER_DAILY_LIMT → похоже на USER_DAILY_LIMIT"):
        load_settings(Settings, {**VALID, "USER_DAILY_LIMT": "5"})


def test_log_level_is_case_insensitive():
    settings = load_settings(Settings, {**VALID, "LOG_LEVEL": "info"})
    assert settings.log_level == "INFO"


def test_invalid_log_level_is_refused():
    with pytest.raises(ConfigError, match="log_level"):
        load_settings(Settings, {**VALID, "LOG_LEVEL": "LOUD"})


def test_unrelated_environment_is_not_a_typo():
    assert find_key_typos(["PATH", "HOME", "HOSTNAME", "LANG"], set(Settings.model_fields)) == []


@pytest.mark.parametrize(("first", "second", "expected"), [
    ("LIMIT", "LIMIT", True), ("LIMIT", "LIMT", True), ("LIMIT", "LIMITS", True),
    ("LIMIT", "LIMOT", True), ("LIMIT", "LIMTI", True), ("LIMIT", "LMTI", False), ("ADMIN_ID", "DATA_DIR", False),
])
def test_within_one_edit(first, second, expected):
    assert within_one_edit(first, second) is expected
