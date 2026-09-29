"""Защита перед разбором HTML стандартным html.parser (задача 33, C1).

На одном теге с сотнями тысяч атрибутов внутренняя регулярка html.parser (locatetagend) держит 170–290 байт
памяти на байт входа: страница 2 МБ даёт сотни МБ и роняет контейнер. Чинить сам html.parser нельзя — его
внутренности приватные, поэтому перед разбором (head_tags.parse_head, page_contacts.parse_contacts) страница
проходит через этот линейный проход: только str.find, без регулярок с обратным ходом. Тег (от `<` до `>`)
длиннее MAX_TAG_CHARS или `<` без `>` до самого конца строки — обрезаем html перед этим `<` и считаем
страницу неполной: html.parser такого тега никогда не увидит.
"""

MAX_TAG_CHARS = 16 * 1024  # с большим запасом для настоящих тегов (задача 33, C1)


def guard_html(html: str) -> tuple[str, bool]:
    """(возможно обрезанный html, застали ли страницу целиком до места обрезки)."""
    cut = _first_unsafe_tag(html)
    return (html, True) if cut is None else (html[:cut], False)


def _first_unsafe_tag(html: str) -> int | None:
    """Первый `<`, чей тег длиннее MAX_TAG_CHARS или не закрыт вовсе — позиция для обрезки, иначе None."""
    position = 0
    while (start := html.find("<", position)) != -1:
        end = html.find(">", start)
        if end == -1 or end - start > MAX_TAG_CHARS:
            return start
        position = end + 1
    return None
