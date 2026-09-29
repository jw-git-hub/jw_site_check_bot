"""Разбор страницы в отдельном процессе со своим пределом памяти (задача 33) — страховка поверх html_guard.

Точка входа: `python -m bot.site_check.parse_worker`. Сторож `html_guard.guard_html` (C1) закрывает все известные
нагрузки на html.parser, но полное совпадение с его внутренностями на всех входах (правила комментариев/CDATA
разных выпусков 3.12) подтвердить нельзя — поэтому здесь, до всего остального, процесс сам ограничивает себе
память (RLIMIT_AS): упасть от OOM может только этот процесс, не весь бот.

Модуль и всё, что он импортирует, не тянут aiogram/aiohttp/sqlalchemy (ради этого декодирование вынесено в
page_text.py) — быстрый импорт, узкая память самого процесса.

Вход и выход — только через stdin/stdout (parse_ipc.py): страница не попадает ни в аргументы командной строки,
ни в журнал.
"""
import resource
import sys

from bot.site_check.head_tags import parse_head
from bot.site_check.page_contacts import parse_contacts
from bot.site_check.page_text import decode_page
from bot.site_check.parse_ipc import decode_request, encode_response

# Старт (задача 33): если на Linux обычная страница не проходит с этим пределом — поднять и записать в отчёт.
PARSE_MEMORY_LIMIT_BYTES = 160 * 1024 * 1024


def limit_memory() -> None:
    """На Маке setrlimit(RLIMIT_AS, …) не даёт поставить предел ниже текущего виртуального адресного
    пространства процесса — на голом интерпретаторе оно уже сотни ГБ (гигантские зоны malloc, замер 29.09.2026):
    любое значение до полутриллиона байт получает ValueError, не только 160 МБ. На Linux ожидается другое, но
    неудача всё равно не должна ронять разбор — html_guard.guard_html остаётся первым и обязательным рубежом."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (PARSE_MEMORY_LIMIT_BYTES, PARSE_MEMORY_LIMIT_BYTES))
    except (ValueError, OSError):
        pass


def parse(charset: str | None, complete: bool, body: bytes) -> bytes:
    html = decode_page(body, charset)
    head = parse_head(html)
    contacts = parse_contacts(html, complete)
    return encode_response(head, contacts)


def main() -> None:
    limit_memory()  # первым делом — до чтения и разбора страницы
    charset, complete, body = decode_request(sys.stdin.buffer.read())
    sys.stdout.buffer.write(parse(charset, complete, body))


if __name__ == "__main__":
    main()
