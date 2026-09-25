import logging

import pytest
from loguru import logger

from bot.core.logging import MASK, add_secret_values, mask, setup_logging
from tests.fakes import fake_google_key, fake_telegram_token


@pytest.fixture(autouse=True)
def fresh_logging():
    setup_logging("DEBUG")
    yield
    logger.remove()


def test_token_in_message_is_masked(capsys):
    logger.info("запрос https://api.telegram.org/bot{}/getMe", fake_telegram_token())
    out = capsys.readouterr().out
    assert fake_telegram_token() not in out
    assert MASK in out


def test_value_added_after_start_is_masked(capsys):
    add_secret_values(["long-enough-private-value"])
    logger.warning("значение long-enough-private-value попало в текст")
    out = capsys.readouterr().out
    assert "long-enough-private-value" not in out
    assert MASK in out


def test_traceback_is_masked(capsys):
    try:
        raise RuntimeError(f"ключ {fake_google_key()} в тексте ошибки")
    except RuntimeError:
        logger.exception("упало")
    out = capsys.readouterr().out
    assert fake_google_key() not in out
    assert "RuntimeError" in out


def test_stdlib_logger_goes_through_mask(capsys):
    logging.getLogger("aiogram.dispatcher").warning("url %s", fake_telegram_token())
    captured = capsys.readouterr()
    assert fake_telegram_token() not in captured.out
    assert fake_telegram_token() not in captured.err
    assert MASK in captured.out
    assert "aiogram.dispatcher" in captured.out


def test_mask_function_for_other_texts():
    assert mask(f"x {fake_telegram_token()} y") == f"x {MASK} y"
