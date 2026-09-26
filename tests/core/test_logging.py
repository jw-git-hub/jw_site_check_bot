import gc
import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from loguru import logger

from bot.core.logging import MASK, _log_uncaught, _log_uncaught_in_thread, _log_unraisable, _StdlibToLoguru
from bot.core.logging import add_secret_values, mask, setup_logging
from tests.fakes import fake_google_key, fake_telegram_token

ROOT = Path(__file__).resolve().parents[2]


def _capture_hooks() -> tuple:
    return sys.excepthook, threading.excepthook, sys.unraisablehook


def _restore_hooks(hooks: tuple) -> None:
    """Обратная сторона setup_logging (раунд 1 обзора задачи 19, находка 2): без возврата эти перехватчики
    остаются подменены на весь процесс pytest, и следующие файлы тестов теряют собственный перехват pytest
    ошибок в потоках и финализаторах."""
    sys.excepthook, threading.excepthook, sys.unraisablehook = hooks
    logging.captureWarnings(False)


@pytest.fixture(autouse=True)
def fresh_logging():
    previous_hooks = _capture_hooks()
    setup_logging("DEBUG")
    yield
    logger.remove()
    _restore_hooks(previous_hooks)


def test_restore_hooks_reverts_setup_logging_globals():
    """Раунд 1 обзора задачи 19 (находка 2): setup_logging переставляет sys.excepthook, threading.excepthook,
    sys.unraisablehook и включает logging.captureWarnings — фикстура должна вернуть их в teardown, иначе они
    утекают на весь процесс pytest (наблюдалось: следующие файлы тестов теряли перехват pytest ошибок
    в потоках/финализаторах)."""
    baseline = _capture_hooks()
    setup_logging("DEBUG")
    assert _capture_hooks() == (_log_uncaught, _log_uncaught_in_thread, _log_unraisable)
    _restore_hooks(baseline)
    assert _capture_hooks() == baseline


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


def test_warnings_are_masked_only_when_capture_is_enabled():
    """Раунд 1 обзора задачи 19: без logging.captureWarnings(True) предупреждение уходит в сырой stderr —
    первый прогон подтверждает это (иначе тест не ловил бы регресс), второй — что setup_logging чинит это
    на обоих потоках. warnings.warn проверяется в отдельном процессе: внутри самого теста pytest подменяет
    warnings.showwarning на своё на время тела теста (см. документацию pytest о перехвате предупреждений)."""
    key = fake_google_key()
    warn = f"import warnings; warnings.simplefilter('always'); warnings.warn('осторожно: {key}')\n"

    without_capture = subprocess.run([sys.executable, "-c", warn], cwd=ROOT, capture_output=True, text=True,
                                     timeout=30)
    assert key in without_capture.stderr  # подтверждает, что сценарий вообще что-то проверяет

    with_capture = subprocess.run([sys.executable, "-c",
                                   f"from bot.core.logging import setup_logging; setup_logging('DEBUG')\n{warn}"],
                                  cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert key not in with_capture.stdout
    assert key not in with_capture.stderr
    assert MASK in with_capture.stdout


def test_broken_log_record_does_not_leak_raw_arguments(capsys):
    """Раунд 1 обзора задачи 19 (Сек7): logging.Handler.handleError печатает record.msg/record.args в сыром
    stderr, в обход маски — секрет из аргументов записи не должен утечь ни в один поток."""
    key = fake_google_key()
    broken = logging.LogRecord("t", logging.WARNING, __file__, 1, "%s и %s", (key,), None)
    _StdlibToLoguru().emit(broken)  # не должно бросить исключение
    captured = capsys.readouterr()
    assert key not in captured.out
    assert key not in captured.err


def test_broken_log_record_is_masked_end_to_end_in_a_real_process():
    """Раунд 1 обзора задачи 19 (Сек7): тот же случай, что нашёл ревьюер — getLogger(...).warning с
    несовпадающим числом %s и секретом в аргументах, настоящим процессом (Logger.callHandlers, не только
    emit напрямую)."""
    key = fake_google_key()
    script = ("from bot.core.logging import add_secret_values, setup_logging; setup_logging('DEBUG')\n"
             f"add_secret_values(['{key}'])\n"
             "import logging\n"
             f"logging.getLogger('aiohttp.client').warning('url %s and %s', '{key}')\n")
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert key not in result.stdout
    assert key not in result.stderr
