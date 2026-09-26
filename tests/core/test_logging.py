import gc
import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from loguru import logger

from bot.core.logging import MASK, QUIET_LOGGERS, _log_uncaught, _log_uncaught_in_thread, _log_unraisable
from bot.core.logging import _StdlibToLoguru, add_secret_values, mask, setup_logging
from tests.fakes import fake_google_key, fake_telegram_token

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class _LoggingState:
    """Всё, что setup_logging меняет глобально (раунд 2 обзора задачи 19, находка 2 — открытая часть):
    перехватчики потоков/финализаторов, обработчики и уровень корневого логгера stdlib, уровни «тихих»
    логгеров (QUIET_LOGGERS)."""
    hooks: tuple
    root_handlers: list
    root_level: int
    quiet_levels: dict[str, int]


def _capture_hooks() -> _LoggingState:
    root = logging.getLogger()
    return _LoggingState(hooks=(sys.excepthook, threading.excepthook, sys.unraisablehook),
                         root_handlers=list(root.handlers), root_level=root.level,
                         quiet_levels={name: logging.getLogger(name).level for name in QUIET_LOGGERS})


def _restore_hooks(state: _LoggingState) -> None:
    """Обратная сторона setup_logging (раунд 1 обзора задачи 19, находка 2; раунд 2 — root-логгер и
    QUIET_LOGGERS туда же). Без возврата всё это остаётся подменено на весь процесс pytest, и следующие
    файлы тестов теряют собственный перехват pytest ошибок в потоках/финализаторах, а заодно и корневой
    логгер stdlib — с чужим обработчиком и уровнем."""
    sys.excepthook, threading.excepthook, sys.unraisablehook = state.hooks
    logging.captureWarnings(False)
    root = logging.getLogger()
    root.handlers[:] = state.root_handlers
    root.setLevel(state.root_level)
    for name, level in state.quiet_levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture(autouse=True)
def fresh_logging():
    previous_state = _capture_hooks()
    setup_logging("DEBUG")
    yield
    logger.remove()
    _restore_hooks(previous_state)


def test_restore_hooks_reverts_setup_logging_globals():
    """Раунд 1 обзора задачи 19 (находка 2): setup_logging переставляет sys.excepthook, threading.excepthook,
    sys.unraisablehook и включает logging.captureWarnings — фикстура должна вернуть их в teardown, иначе они
    утекают на весь процесс pytest (наблюдалось: следующие файлы тестов теряли перехват pytest ошибок
    в потоках/финализаторах). Раунд 2: то же для обработчиков и уровня корневого логгера stdlib и уровней
    QUIET_LOGGERS (был найден стойкий root level=INFO и чужой обработчик после test_logging.py)."""
    baseline = _capture_hooks()
    setup_logging("DEBUG")
    root = logging.getLogger()
    assert _capture_hooks().hooks == (_log_uncaught, _log_uncaught_in_thread, _log_unraisable)
    assert [type(handler) for handler in root.handlers] == [_StdlibToLoguru]
    assert root.level == logging.INFO
    assert all(logging.getLogger(name).level == logging.WARNING for name in QUIET_LOGGERS)
    _restore_hooks(baseline)
    restored = _capture_hooks()
    assert restored.hooks == baseline.hooks
    assert restored.root_handlers == baseline.root_handlers
    assert restored.root_level == baseline.root_level
    assert restored.quiet_levels == baseline.quiet_levels


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


def test_handle_error_does_not_raise_when_the_sink_itself_fails():
    """Раунд 2 обзора задачи 19 (правило контролёра к поправке 3): «ошибка оформления записи не должна ронять
    программу» — это верно и когда сам сток loguru, вызванный из handleError, тоже падает. catch=False на
    стоке нужен явно: по умолчанию loguru сама глушит ошибки стока и не даёт им дойти до нашего вызова."""
    logger.remove()

    def failing_sink(message: str) -> None:
        raise RuntimeError("сток тоже упал")

    logger.add(failing_sink, catch=False)
    broken = logging.LogRecord("t", logging.WARNING, __file__, 1, "%s и %s", ("только-один-аргумент",), None)
    _StdlibToLoguru().emit(broken)  # не должно бросить исключение, даже если сам сток сломан
