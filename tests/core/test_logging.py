import gc
import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from loguru import logger

from bot.core.logging import MASK, _StdlibToLoguru, add_secret_values, mask, setup_logging
from tests.fakes import fake_google_key, fake_telegram_token

ROOT = Path(__file__).resolve().parents[2]


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


def test_thread_exception_is_masked_and_logged(capsys):
    """Поправка 3 к задаче 19: threading.excepthook — тот же журнал с маской, не падение молча в stderr."""
    def boom():
        raise RuntimeError(f"ключ {fake_google_key()} в потоке")

    thread = threading.Thread(target=boom, name="фоновый")
    thread.start()
    thread.join(timeout=5)
    out = capsys.readouterr().out
    assert fake_google_key() not in out
    assert "фоновый" in out
    assert MASK in out


def test_unraisable_exception_is_masked_and_logged(capsys):
    """Поправка 3 к задаче 19: sys.unraisablehook — ошибка в __del__ тоже уходит в журнал с маской."""
    class Boom:
        def __del__(self):
            raise RuntimeError(f"ключ {fake_google_key()} в деструкторе")

    Boom()
    gc.collect()
    out = capsys.readouterr().out
    assert fake_google_key() not in out
    assert MASK in out


def test_warnings_logger_goes_through_masked_log(capsys):
    """Поправка 3 к задаче 19: тот же путь, каким captureWarnings передаёт предупреждение журналу."""
    logging.getLogger("py.warnings").warning("осторожно: %s", fake_google_key())
    out = capsys.readouterr().out
    assert fake_google_key() not in out
    assert MASK in out


def test_warnings_warn_is_masked_in_a_real_process():
    """Поправка 3 к задаче 19: logging.captureWarnings(True) — сквозная проверка без обвязки pytest вокруг
    warnings.showwarning, которая внутри теста подменяла бы её на свою (см. документацию pytest о перехвате
    предупреждений)."""
    key = fake_google_key()
    script = ("from bot.core.logging import setup_logging; setup_logging('DEBUG')\n"
             "import warnings; warnings.simplefilter('always')\n"
             f"warnings.warn('осторожно: {key}')\n")
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert key not in result.stdout
    assert MASK in result.stdout


def test_broken_log_record_does_not_crash_the_program():
    """Поправка 3 к задаче 19: _StdlibToLoguru.emit не должен ронять программу на плохом форматировании —
    ошибка форматирования уходит в self.handleError, а не наружу исключением."""
    broken = logging.LogRecord("t", logging.WARNING, __file__, 1, "%s и %s", ("только-один-аргумент",), None)
    _StdlibToLoguru().emit(broken)  # не должно бросить исключение
