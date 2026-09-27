"""Полоса-картинка шапки: файл на первую отправку, дальше — по запомненному file_id (задача 23b, ТЗ 7.1).

`rich.header(lang)` кладёт в первый блок только метку языка (`rich.BANNER_MEDIA_PREFIX`) — ничего не знает про
файлы. Подстановку делает `AiogramMessenger` этим кэшем: метка есть — на её место встаёт файл (первый раз) или
запомненный `file_id` (дальше); блока с меткой нет — сообщение не трогается. Кэш — в памяти процесса: после
перезапуска бота первая отправка на каждом языке загрузит полосу заново, в базу это не пишется.
"""
from pathlib import Path
from typing import Any

from aiogram.types import FSInputFile, Message

from bot.core.i18n import Lang
from bot.core.rich import BANNER_MEDIA_PREFIX

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
BANNER_FILES: dict[Lang, Path] = {"ru": ASSETS_DIR / "banner-ru.png", "en": ASSETS_DIR / "banner-en.png"}


class BannerCache:
    def __init__(self) -> None:
        self._file_ids: dict[Lang, str] = {}

    def resolve(self, rich_message: dict[str, Any]) -> dict[str, Any]:
        """Копия сообщения с файлом или file_id на месте метки; без метки — то же сообщение, без копии."""
        lang = _banner_lang(rich_message)
        if lang is None:
            return rich_message
        media = self._file_ids.get(lang) or FSInputFile(BANNER_FILES[lang])
        return _replace_first_block_media(rich_message, media)

    def remember(self, sent_as: dict[str, Any], response: Message | bool) -> None:
        """response — ответ Telegram на сообщение, ушедшее с меткой sent_as (не с уже подставленным файлом)."""
        lang = _banner_lang(sent_as)
        file_id = _largest_photo_file_id(response) if lang and isinstance(response, Message) else None
        if lang and file_id:
            self._file_ids[lang] = file_id


def _banner_lang(rich_message: dict[str, Any]) -> Lang | None:
    blocks = rich_message.get("blocks") or []
    if not blocks or blocks[0].get("type") != "photo":
        return None
    media = blocks[0]["photo"].get("media", "")
    lang = media.removeprefix(BANNER_MEDIA_PREFIX) if isinstance(media, str) else ""
    return lang if lang in BANNER_FILES else None


def _replace_first_block_media(rich_message: dict[str, Any], media: Any) -> dict[str, Any]:
    first = rich_message["blocks"][0]
    new_first = {**first, "photo": {**first["photo"], "media": media}}
    return {**rich_message, "blocks": [new_first, *rich_message["blocks"][1:]]}


def _largest_photo_file_id(message: Message) -> str | None:
    blocks = message.rich_message.blocks if message.rich_message else []
    sizes = blocks[0].photo if blocks and getattr(blocks[0], "photo", None) else []
    largest = max(sizes, key=lambda size: size.width * size.height, default=None)
    return largest.file_id if largest else None
