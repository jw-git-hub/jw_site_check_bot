from datetime import UTC, datetime

from bot.site_check.checks import DomainCheck
from bot.site_check.comparison import compare
from bot.site_check.findings import Block, Grade
from bot.site_check.pipeline import CheckResult
from bot.site_check.thresholds import RULES_VERSION
from bot.site_check.verdict import judge
from tests.builders import MB, TODAY, images, page, security, speed

WHEN = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
GRADES_BEFORE = ("bad", "good", "good", "bad", "unknown", "unknown", "unknown", "unknown")


def previous(lcp_ms=4200, page_bytes=12 * MB, rules=RULES_VERSION, grades=GRADES_BEFORE) -> DomainCheck:
    return DomainCheck(WHEN, "channel", "done", None, grades, "bad",
                       {"lcp_ms": lcp_ms, "page_bytes": page_bytes, "rules": rules})


def current(lcp=2100, page_bytes=3_250_586) -> CheckResult:
    facts = page(speed(lcp=lcp), image_facts=images(page_bytes=page_bytes))
    return CheckResult("https://site.test/", facts, security(), judge(facts, security(), TODAY))


def test_notable_changes_with_grades():
    found = compare(previous(), current())
    assert (found.day, found.lcp_ms, found.page_bytes) == (WHEN.date(), (4200, 2100), (12 * MB, 3_250_586))
    assert found.grades == ((Block.SPEED, Grade.BAD, Grade.GOOD), (Block.IMAGES, Grade.BAD, Grade.FIX))


def test_small_moves_are_noise():
    same_grades = ("good", "good", "good", "fix") + ("unknown",) * 4
    assert compare(previous(lcp_ms=2400, page_bytes=3_000_000, grades=same_grades),
                   current(lcp=2100, page_bytes=3_250_586)) is None


def test_grades_are_not_compared_across_rules():
    found = compare(previous(rules="1.1"), current())
    assert found.grades == () and found.lcp_ms == (4200, 2100)


def test_missing_numbers_are_not_compared():
    assert compare(previous(lcp_ms=None, page_bytes=None, rules="1.1"), current()) is None
