"""Кодировка страницы: тело → текст (ТЗ, 16; задача 33, C2).

Отдельный модуль — специально не тянет aiohttp/aiogram/sqlalchemy (задача 33): decode_page нужен и своей
загрузке (page_fetch.py), и parse_worker.py, который разбирает страницу в отдельном процессе под своим
пределом памяти и не должен грузить с собой бота целиком.
"""
import codecs
import re

CHARSET_SNIFF_BYTES = 4096
FALLBACK_CHARSET = "utf-8"
META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
# Белый список текстовых кодеков по их каноническому имени codecs.lookup(name).name (задача 33, C2) — не любой
# codecs.lookup: punycode/idna/rot13/base64/zlib и подобные существуют в реестре, но текстом сайта не являются.
ISO8859_CHARSETS = frozenset(f"iso8859-{n}" for n in range(1, 17) if n != 12)  # 8859-12 не стандартизирован
TEXT_CODECS = frozenset({
    "utf-8", "utf-16", "utf-32",
    "cp1250", "cp1251", "cp1252", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257", "cp1258",
    "koi8-r", "koi8-u", "cp866", "mac-cyrillic",
}) | ISO8859_CHARSETS


def decode_page(body: bytes, charset: str | None) -> str:
    """Кодировка: из Content-Type, иначе из <meta charset> в начале страницы, иначе UTF-8 (ТЗ, 16).

    Кодек — только из белого списка текстовых (TEXT_CODECS, задача 33, C2): codecs.lookup принимает и
    punycode (квадратичный декодер: 2 МБ — около 15 минут синхронно), и idna/rot13/base64/zlib — не текст,
    а сериализация другого рода. Всё вне списка и любая ошибка декодирования — как UTF-8 с заменой.
    """
    for name in (charset, _meta_charset(body)):
        decoded = _try_decode(body, name)
        if decoded is not None:
            return decoded
    return body.decode(FALLBACK_CHARSET, errors="replace")


def _try_decode(body: bytes, name: str | None) -> str | None:
    if not name or not _known_codec(name):
        return None
    try:
        return body.decode(name, errors="replace")
    except (LookupError, UnicodeError):
        return None


def _meta_charset(body: bytes) -> str | None:
    found = META_CHARSET.search(body[:CHARSET_SNIFF_BYTES])
    return found.group(1).decode("ascii") if found else None


def _known_codec(name: str) -> bool:
    try:
        return codecs.lookup(name).name in TEXT_CODECS
    except LookupError:
        return False
