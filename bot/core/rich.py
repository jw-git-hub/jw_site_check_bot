"""Сборка rich-сообщений Telegram (Bot API 10.3) — словарями, как в боте канала (ТЗ, 7.1).

Формат экосистемы: первая строка — моноширинная «>jw ~/раздел_»; в конце — разделитель и моноширинный подвал
«jw-dev.pro · @jw_dev_pro», сайт и юзернейм — ссылками. Кнопки — inline-клавиатура под сообщением, одна в строке
(решение владельца после живой приёмки, задача 23a).
"""
from typing import Any
from urllib.parse import quote

Block = dict[str, Any]
Inline = str | dict[str, Any]

HEADER_PREFIX = ">jw ~/"
HEADER_SUFFIX = "_"
SITE_TEXT = "jw-dev.pro"
SITE_URL = "https://jw-dev.pro"
USERNAME_TEXT = "@jw_dev_pro"
USERNAME_URL = "https://t.me/jw_dev_pro"
FOOTER_SEPARATOR = " · "
TELEGRAM_LINK = "https://t.me/"
CELL_ALIGN = "left"
CELL_VALIGN = "top"
STYLE_PRIMARY = "primary"


def code(text: str) -> dict[str, Any]:
    return {"type": "code", "text": text}


def link(text: Inline, url: str) -> dict[str, Any]:
    return {"type": "url", "url": url, "text": text}


def paragraph(*parts: Inline) -> Block:
    return {"type": "paragraph", "text": list(parts)}


def heading(text: str, size: int) -> Block:
    return {"type": "heading", "size": size, "text": text}


def divider() -> Block:
    return {"type": "divider"}


def header(section: str) -> Block:
    return paragraph(code(HEADER_PREFIX + section + HEADER_SUFFIX))


def footer() -> Block:
    parts = [link(code(SITE_TEXT), SITE_URL), code(FOOTER_SEPARATOR), link(code(USERNAME_TEXT), USERNAME_URL)]
    return {"type": "footer", "text": parts}


def button_url(text: str, url: str, style: str | None = None) -> dict[str, Any]:
    return _button(text, style, url=url)


def button_callback(text: str, data: str, style: str | None = None) -> dict[str, Any]:
    return _button(text, style, callback_data=data)


def _button(text: str, style: str | None, **action: str) -> dict[str, Any]:
    button: dict[str, Any] = {"text": text, **action}
    if style:
        button["style"] = style
    return button


def keyboard(*buttons: dict[str, Any]) -> dict[str, Any]:
    """Inline-клавиатура под сообщением — одна кнопка в строке (решение владельца, задача 23a)."""
    return {"inline_keyboard": [[button] for button in buttons]}


def details(summary: str, blocks: list[Block]) -> Block:
    return {"type": "details", "summary": summary, "blocks": blocks}


def table(rows: list[list[str]]) -> Block:
    """Первая строка — заголовок таблицы."""
    cells = [[_cell(value, is_header=index == 0) for value in row] for index, row in enumerate(rows)]
    return {"type": "table", "cells": cells}


def _cell(value: str, is_header: bool) -> dict[str, Any]:
    cell: dict[str, Any] = {"text": value, "align": CELL_ALIGN, "valign": CELL_VALIGN}
    if is_header:
        cell["is_header"] = True
    return cell


def message(blocks: list[Block]) -> dict[str, Any]:
    return {"blocks": blocks}


def dm_link(username: str, text: str) -> str:
    """Ссылка в личку с уже набранным текстом (ТЗ, 7.2)."""
    return f"{TELEGRAM_LINK}{username}?text={quote(text)}"
