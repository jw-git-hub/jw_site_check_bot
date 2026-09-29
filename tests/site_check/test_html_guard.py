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


@pytest.mark.parametrize("payload", [
    ("<html><body><a " + "a " * 1_100_000)[:PAGE_MAX_BYTES],
    ("<html><body>" + "<a " + "a=b " * 500_000 + ">")[:PAGE_MAX_BYTES],
    ("<html><body>" + "</a " + "a=b " * 500_000 + ">")[:PAGE_MAX_BYTES],
    ("<html><body><a " + "a=b " * 500_000)[:PAGE_MAX_BYTES],
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
