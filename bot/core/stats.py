"""Учёт «по возможности» (ТЗ, Р13): сбой базы уходит в журнал, работа бота продолжается.

Сюда же потом встанет отправка событий в общую базу экосистемы (jw_core).
"""
from collections.abc import Awaitable
from typing import TypeVar

from loguru import logger

Result = TypeVar("Result")


async def best_effort(action: Awaitable[Result], what: str, fallback: Result) -> Result:
    try:
        return await action
    except Exception:  # noqa: BLE001 — сбой базы не должен останавливать проверку
        logger.exception("сбой базы, работа продолжается: {}", what)
        return fallback
