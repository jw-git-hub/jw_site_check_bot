import time
import tracemalloc

import pytest

from bot.site_check.html_guard import MAX_TAG_CHARS, guard_html
from bot.site_check.page_contacts import parse_contacts
from bot.site_check.page_fetch import decode_page
from bot.site_check.head_tags import parse_head

NORMAL_HTML = "<html><head><title>Кафе</title></head><body><p>Привет</p></body></html>"
HUGE_ATTR_TAG = "<a " + "a=b " * 500_000 + ">"  # задача 33, C1: реальный `>`, но тег длиннее MAX_TAG_CHARS
HUGE_ENDTAG = "</a " + "a=b " * 500_000 + ">"
UNCLOSED_BARE_WORDS = "<html><body><a " + "a " * 1_100_000  # без `>` вовсе — как в e2e_oom.py ревьюера
UNCLOSED_SHORT_TAIL = "<html><head><title>x</title></head><body><a href"  # обычная обрезка PAGE_MAX_BYTES
# Раунд 2 (перепроверка ревьюера): `>` внутри кавычки — html.parser кавычку учитывает и видит один гигантский
# тег, наивный сторож (первый `>` без учёта кавычек) видел бы короткий обрубок и пропускал бы тег целиком.
QUOTE_BYPASS_TAG = '<a b="' + ">" * 200_000 + '">'

PAGE_MAX_BYTES = 2 * 1024 * 1024
PEAK_MEMORY_LIMIT_BYTES = 40 * 1024 * 1024  # решение контроллёра, C1: с запасом ниже прежних сотен МБ
PARSE_TIME_LIMIT_SECONDS = 2.0


def test_normal_html_is_not_touched():
    assert guard_html(NORMAL_HTML) == (NORMAL_HTML, True)


def test_tag_longer_than_the_limit_is_cut_before_it():
    html = "<html><body>" + HUGE_ATTR_TAG
    guarded, complete = guard_html(html)
    assert (guarded, complete) == ("<html><body>", False)


def test_long_end_tag_is_also_cut():
    html = "<html><body>" + HUGE_ENDTAG
    guarded, complete = guard_html(html)
    assert (guarded, complete) == ("<html><body>", False)


def test_unclosed_tag_without_any_further_gt_is_cut():
    guarded, complete = guard_html(UNCLOSED_BARE_WORDS)
    assert (guarded, complete) == ("<html><body>", False)


def test_short_trailing_unclosed_tag_from_an_ordinary_cut_is_dropped_too():
    guarded, complete = guard_html(UNCLOSED_SHORT_TAIL)
    assert (guarded, complete) == ("<html><head><title>x</title></head><body>", False)


def test_a_tag_right_at_the_limit_is_kept():
    filler = "a" * (MAX_TAG_CHARS - len("<a >"))
    html = f"<a {filler}>ok"
    assert guard_html(html) == (html, True)


def test_gt_inside_a_quoted_attribute_does_not_hide_a_giant_tag():
    """Раунд 2: `>` внутри кавычки — не конец тега (как у html.parser), а не конец сканирования сторожа."""
    html = "<html><body>" + QUOTE_BYPASS_TAG
    guarded, complete = guard_html(html)
    assert (guarded, complete) == ("<html><body>", False)


def test_a_stray_quote_not_after_equals_is_tracked_too():
    """Раунд 2: кавычка учитывается, даже если не после `=` — здесь это апостроф внутри «don't»."""
    filler = "x" * (MAX_TAG_CHARS + 100)
    html = "<html><body><a title=don't " + filler + ">ok"
    guarded, complete = guard_html(html)
    assert (guarded, complete) == ("<html><body>", False)


def test_ordinary_quoted_attributes_with_nested_other_quote_type_are_kept():
    html = '<a href="/page?a=1&b=2" title=\'hello "world"\' class=x>text</a>'
    assert guard_html(html) == (html, True)


SCRIPT_JSON_BODY = '{"note": "<b>", "arr": [1,2,3]}' * 20_000  # ~640 КБ: `<` и `"` внутри, без «</script»
FOOTER_WITH_TEL = '<footer><a href="tel:+79127127004">Позвоните</a></footer>'
LARGE_SCRIPT_PAGE = ("<html><head></head><body><script>" + SCRIPT_JSON_BODY + "</script>"
                    + FOOTER_WITH_TEL + "</body></html>")


def test_large_script_content_is_skipped_without_scanning_and_page_is_kept_whole():
    """Раунд 2: содержимое <script> (режим CDATA у парсера) не сканируется — большой JS с `<` и `"` внутри
    не режется зря, страница остаётся целой."""
    assert guard_html(LARGE_SCRIPT_PAGE) == (LARGE_SCRIPT_PAGE, True)


def test_footer_tel_after_a_large_script_is_still_found():
    facts = parse_contacts(LARGE_SCRIPT_PAGE, complete=True)
    assert (facts.call_links, facts.complete) == (1, True)


@pytest.mark.parametrize("payload", [
    ("<html><body><a " + "a " * 1_100_000)[:PAGE_MAX_BYTES],
    ("<html><body>" + "<a " + "a=b " * 500_000 + ">")[:PAGE_MAX_BYTES],
    ("<html><body>" + "</a " + "a=b " * 500_000 + ">")[:PAGE_MAX_BYTES],
    ("<html><body><a " + "a=b " * 500_000)[:PAGE_MAX_BYTES],
    ("<html><body>" + '<a b="' + ">" * 1_500_000 + '">')[:PAGE_MAX_BYTES],
    # Раунд 2, самый опасный обход: `>` в кавычке рано прячет от наивного сторожа сотни тысяч атрибутов дальше
    # в этом же теге — html.parser их всё равно разбирает целиком (измерено: без правки +607 МБ).
    ('<html><body><a href="x>y" ' + "a=b " * 500_000 + ">")[:PAGE_MAX_BYTES],
])
def test_adversarial_two_megabyte_pages_parse_within_bounded_memory_and_time(payload):
    body = payload.encode()
    started = time.perf_counter()
    tracemalloc.start()
    html = decode_page(body, "utf-8")
    parse_head(html)
    parse_contacts(html, complete=False)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - started
    assert peak < PEAK_MEMORY_LIMIT_BYTES
    assert elapsed < PARSE_TIME_LIMIT_SECONDS


def test_an_ordinary_page_still_parses_normally_after_the_guard():
    html = NORMAL_HTML
    tags = parse_head(html)
    facts = parse_contacts(html, complete=True)
    assert (tags.title, tags.complete) == ("Кафе", True)
    assert facts.complete is True
