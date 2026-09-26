"""Журнал: loguru в stdout — его хранит Docker, 3 файла по 10 МБ (ТЗ, С13), — с маскировкой секретов (ТЗ, Сек7, Сек12).

- Маскируется итоговая строка вместе с трейсбеком: aiohttp и aiogram кладут в тексты ошибок адрес запроса с токеном.
- Шаблоны секретов работают с первой строки журнала, значения — сразу после загрузки настроек.
- diagnose=False: в трейсбеке нет значений переменных, а в них бывают секреты.
- Журналы стандартной библиотеки (aiogram, aiohttp, asyncio) идут через тот же путь.
- Необработанные исключения в потоках, в финализаторах (__del__) и предупреждения (warnings) — туда же,
  с той же маской, а не мимо неё в сырой stderr.
"""
import contextlib
import logging
import sys
import threading
from collections.abc import Iterable

from loguru import logger

from bot.core.secret_patterns import SECRET_PATTERNS

MASK = "***"
MIN_SECRET_LENGTH = 6  # короче — не секрет, а маскировка испортила бы журнал
LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} {level} {name}: {message}"
LOGURU_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
QUIET_LOGGERS = ("aiohttp.access", "aiohttp.client", "aiohttp.internal")


class SecretMasker:
    def __init__(self) -> None:
        self._values: tuple[str, ...] = ()

    def add(self, values: Iterable[str]) -> None:
        known = set(self._values) | {value for value in values if len(value) >= MIN_SECRET_LENGTH}
        self._values = tuple(sorted(known, key=len, reverse=True))

    def mask(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, MASK)
        for pattern in SECRET_PATTERNS.values():
            text = pattern.sub(MASK, text)
        return text


MASKER = SecretMasker()


def mask(text: str) -> str:
    return MASKER.mask(text)


def add_secret_values(values: Iterable[str]) -> None:
    MASKER.add(values)


def _write_masked(message: str) -> None:
    sys.stdout.write(MASKER.mask(str(message)))
    sys.stdout.flush()


class _StdlibToLoguru(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname if record.levelname in LOGURU_LEVELS else record.levelno
            logger.opt(exception=record.exc_info).log(level, "{}: {}", record.name, record.getMessage())
        except Exception:  # noqa: BLE001 — плохое форматирование записи не должно ронять программу
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        """logging.Handler.handleError по умолчанию печатает record.msg и record.args в сыром stderr, в обход
        маски — а там бывает секрет (аргумент испорченной записи). Вместо него — тот же журнал с маской: только
        имя логгера и трейсбек, без исходного сообщения.

        Вторичный сбой самого журнала (сток loguru тоже упал) — не повод ронять программу, ровно как и
        первичный сбой форматирования записи, который сюда привёл."""
        with contextlib.suppress(Exception):  # noqa: BLE001 — вторичный сбой самого журнала не должен ронять программу
            logger.opt(exception=True).error("запись журнала {} не оформилась", record.name)


def _log_uncaught(exc_type, exc_value, exc_traceback) -> None:
    logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical("необработанное исключение")


def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
    thread_name = args.thread.name if args.thread else "?"
    logger.opt(exception=(args.exc_type, args.exc_value, args.exc_traceback)).critical(
        "необработанное исключение в потоке {}", thread_name)


def _log_unraisable(args) -> None:
    logger.opt(exception=(args.exc_type, args.exc_value, args.exc_traceback)).critical(
        "необработанное исключение в финализаторе: {}", args.err_msg or "")


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(_write_masked, level=level, format=LOG_FORMAT, colorize=False, diagnose=False, backtrace=False)
    logging.basicConfig(handlers=[_StdlibToLoguru()], level=logging.INFO, force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    sys.unraisablehook = _log_unraisable
    logging.captureWarnings(True)
