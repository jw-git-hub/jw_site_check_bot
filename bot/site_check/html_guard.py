"""Защита перед разбором HTML стандартным html.parser (задача 33, C1).

На одном теге с сотнями тысяч атрибутов внутренняя регулярка html.parser (locatetagend) держит 170–290 байт
памяти на байт входа: страница 2 МБ даёт сотни МБ и роняет контейнер. Чинить сам html.parser нельзя — его
внутренности приватные, поэтому перед разбором (head_tags.parse_head, page_contacts.parse_contacts) страница
проходит через линейный сканер тегов ниже: только str.find и простые нежадные регулярки без обратного хода.

Раунд 2 (перепроверка ревьюера): сторож должен видеть тег НЕ КОРОЧЕ, чем видит его html.parser, — иначе
`>` внутри кавычек в атрибуте (`<a b="…>…>…">`, сотни КБ) сторож принимает за конец тега, пропускает
короткий обрубок, а html.parser разбирает тот же тег целиком со всеми кавычками — память снова растёт.
Поэтому сканер: тегом считает только `<`+буква или `</`+буква (как парсер); `<!--` и `<!…`/`<?…` — отдельно,
через find, без кавычек (парсер там тоже просто ищет ближайший конец); конец обычного тега — ближайший из
`>`, `"`, `'`; кавычка (любая, даже не после `=`) — прыжок к такой же закрывающей, тоже в пределах
MAX_TAG_CHARS от начала тега; не нашли конец в этих границах — обрезаем html перед этим `<`, считаем
страницу неполной. Содержимое `<script>`/`<style>` (режим CDATA у парсера) не сканируется вовсе — сразу
прыжок к `</script`/`</style` — большой JS с `<` и кавычками внутри не режется зря.
"""
import re

MAX_TAG_CHARS = 16 * 1024  # с большим запасом для настоящих тегов (задача 33, C1)
CDATA_ELEMENTS = ("script", "style")  # у парсера в них режим CDATA — содержимое не разбирается как теги
TAG_START = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)")
TAG_DELIMITER = re.compile("""[>"']""")
CDATA_CLOSERS = {name: re.compile(rf"</{name}", re.IGNORECASE) for name in CDATA_ELEMENTS}


class _Cut(Exception):
    """Сигнал наверх: тег или конструкция не влезли в границы — обрезать html перед их `<`."""


def guard_html(html: str) -> tuple[str, bool]:
    """(возможно обрезанный html, застали ли страницу целиком до места обрезки)."""
    cut = _first_unsafe_tag(html)
    return (html, True) if cut is None else (html[:cut], False)


def _first_unsafe_tag(html: str) -> int | None:
    pos = 0
    while (start := html.find("<", pos)) != -1:
        try:
            pos = _skip_one(html, start)
        except _Cut:
            return start
    return None


def _skip_one(html: str, start: int) -> int:
    if html.startswith("<!--", start):
        return _skip_comment(html, start)
    tag = TAG_START.match(html, start)
    if tag is not None:
        return _skip_tag(html, start, tag)
    return _skip_markup_or_text(html, start)


def _skip_comment(html: str, start: int) -> int:
    end = html.find("-->", start + 4)
    if end == -1:
        raise _Cut
    return end + 3


def _skip_markup_or_text(html: str, start: int) -> int:
    """`<!…`/`<?…` (декларации, инструкции обработки) — до ближайшего `>`, без учёта кавычек: у парсера там
    тоже простой линейный поиск, а не разбор атрибутов. Прочий одиночный `<` тегом не считается вовсе."""
    if start + 1 >= len(html) or html[start + 1] not in "!?":
        return start + 1
    end = html.find(">", start)
    if end == -1:
        raise _Cut
    return end + 1


def _skip_tag(html: str, start: int, tag: re.Match) -> int:
    end = _scan_tag(html, start)
    if tag.group(1):  # закрывающий тег — CDATA не открывает
        return end
    return _skip_cdata_content(html, tag.group(2).lower(), end)


def _skip_cdata_content(html: str, name: str, after_tag: int) -> int:
    closer = CDATA_CLOSERS.get(name)
    if closer is None:
        return after_tag
    match = closer.search(html, after_tag)
    if match is None:
        raise _Cut
    return match.start()


def _scan_tag(html: str, start: int) -> int:
    """Конец тега — как его видит html.parser: `>`, а кавычка (любая, даже не после `=`) — прыжок к такой же
    закрывающей, в пределах MAX_TAG_CHARS от начала тега (задача 33, C1, раунд 2): иначе тег с гигантским
    кавычным значением проскочит сторожа коротким, а html.parser увидит его целиком."""
    limit = min(len(html), start + MAX_TAG_CHARS)
    pos = start + 1
    while True:
        match = TAG_DELIMITER.search(html, pos, limit)
        if match is None:
            raise _Cut
        if match.group() == ">":
            return match.end()
        closing = html.find(match.group(), match.end(), limit)
        if closing == -1:
            raise _Cut
        pos = closing + 1
