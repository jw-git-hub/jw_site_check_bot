"""Точка входа: python -m bot. Настройки и секреты — из окружения: docker compose подаёт .env."""
import asyncio
import sys

from loguru import logger

from bot.app import run
from bot.core.config import ConfigError, load_settings
from bot.core.db import DatabaseTooNew
from bot.core.logging import add_secret_values, setup_logging
from bot.settings import Settings

EXIT_CONFIG = 78  # EX_CONFIG: с такими настройками перезапуск не поможет


def main() -> int:
    setup_logging()
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        logger.error("запуск отменён: {}", error)
        return EXIT_CONFIG
    add_secret_values(settings.secret_values())
    setup_logging(settings.log_level)
    try:
        asyncio.run(run(settings))
    except DatabaseTooNew as error:
        logger.error("запуск отменён: {}", error)
        return EXIT_CONFIG
    return 0


if __name__ == "__main__":
    sys.exit(main())
