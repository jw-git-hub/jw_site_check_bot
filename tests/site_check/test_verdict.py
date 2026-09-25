import pytest

from bot.site_check.lighthouse import AuditState
from bot.site_check.tls_check import RedirectState, TlsFacts, TlsOutcome
from bot.site_check.verdict import (Block, Cause, Finding, FixKey, Grade, SecurityFacts, SummaryKind, UnknownReason,
                                    judge, main_cause)
from tests.builders import MB, TODAY, images, mobile, page, security, speed

FIXED_WIDTH_SNIPPET = '<meta name="viewport" content="width=1024">'


def grades(verdict) -> dict:
    return {block: verdict.blocks[block].grade for block in Block}


def example_com():
    slow_page = page(speed(lcp=7000, image=3000), mobile(target=AuditState.FAILED),
                     images(page_bytes=12 * MB, image_bytes=10 * MB, heaviest=(("slider-1.jpg", 3_355_443),), ratio=6))
    return judge(slow_page, security(days_left=170, redirects=(RedirectState.NO_REDIRECT,)), TODAY)


def test_jw_dev_pro_is_all_good():
    verdict = judge(page(), security(), TODAY)
    assert set(grades(verdict).values()) == {Grade.GOOD}
    assert verdict.summary is SummaryKind.ALL_GOOD
    assert (verdict.fixes, verdict.troubles) == ((), ())


def test_example_com_from_spec():
    verdict = example_com()
    assert grades(verdict) == {Block.SPEED: Grade.BAD, Block.MOBILE: Grade.FIX, Block.SECURITY: Grade.FIX,
                               Block.IMAGES: Grade.BAD}
    assert verdict.summary is SummaryKind.HAS_BAD
    assert [item.finding for item in verdict.troubles] == [Finding.SLOW, Finding.HEAVY_PAGE]
    assert [fix.key for fix in verdict.fixes] == [FixKey.COMPRESS_IMAGES, FixKey.ENABLE_REDIRECT, FixKey.SPACE_BUTTONS]
    assert verdict.blocks[Block.SPEED].findings[0].cause is Cause.IMAGES


@pytest.mark.parametrize(("lcp", "grade"), [(2500, Grade.GOOD), (2501, Grade.FIX), (4000, Grade.FIX), (4001, Grade.BAD)])
def test_speed_thresholds(lcp, grade):
    assert judge(page(speed(lcp=lcp)), security(), TODAY).blocks[Block.SPEED].grade is grade


@pytest.mark.parametrize(("facts", "cause"), [
    (speed(lcp=5000, image=900), Cause.IMAGES),
    (speed(lcp=5000, script=100, tbt=1000), Cause.SCRIPTS),   # 100 + (1000 − 200)
    (speed(lcp=5000, server_ms=1800), Cause.SERVER),           # 1800 − 600
    (speed(lcp=5000, image=300), Cause.UNKNOWN),               # меньше 500 мс — причину не называем
])
def test_main_cause(facts, cause):
    assert main_cause(facts) is cause


def test_slow_server_fix_carries_its_seconds():
    fix = judge(page(speed(lcp=5000, server_ms=1800)), security(), TODAY).fixes[0]
    assert (fix.key, fix.source.server_ms) == (FixKey.FIX_SERVER, 1800)


def test_no_mobile_version_and_fixed_width():
    no_meta = judge(page(mobile_facts=mobile(viewport=AuditState.FAILED, snippet=None)), security(), TODAY)
    fixed = judge(page(mobile_facts=mobile(viewport=AuditState.FAILED, snippet=FIXED_WIDTH_SNIPPET)), security(), TODAY)
    assert no_meta.blocks[Block.MOBILE].findings[0].finding is Finding.NO_MOBILE
    assert fixed.blocks[Block.MOBILE].findings[0].finding is Finding.FIXED_WIDTH
    assert fixed.fixes[0].key is FixKey.FIT_WIDTH


def test_tap_targets_and_zoom_are_worth_fixing():
    verdict = judge(page(mobile_facts=mobile(target=AuditState.FAILED, zoom=AuditState.FAILED)), security(), TODAY)
    block = verdict.blocks[Block.MOBILE]
    assert block.grade is Grade.FIX
    assert [item.finding for item in block.findings] == [Finding.TAP_TARGETS, Finding.NO_ZOOM]
    assert verdict.summary is SummaryKind.ONLY_FIX


def test_missing_main_source_makes_block_unknown():
    facts = mobile(viewport=AuditState.UNKNOWN, target=AuditState.FAILED)
    verdict = judge(page(mobile_facts=facts), security(), TODAY)
    assert verdict.blocks[Block.MOBILE].grade is Grade.UNKNOWN
    assert verdict.blocks[Block.MOBILE].unknown_reason is UnknownReason.NO_DATA
    assert verdict.summary is SummaryKind.GOOD_WITH_UNKNOWN


def test_not_applicable_is_no_finding():
    verdict = judge(page(mobile_facts=mobile(zoom=AuditState.NOT_APPLICABLE)), security(), TODAY)
    assert verdict.blocks[Block.MOBILE].grade is Grade.GOOD


@pytest.mark.parametrize(("days_left", "grade"), [(10, Grade.FIX), (13, Grade.FIX), (14, Grade.GOOD), (20, Grade.GOOD)])
def test_expiring_certificate(days_left, grade):
    assert judge(page(), security(days_left=days_left), TODAY).blocks[Block.SECURITY].grade is grade


@pytest.mark.parametrize(("days_left", "grade"), [(3, Grade.GOOD), (1, Grade.FIX)])
def test_short_certificate_warns_only_in_last_quarter(days_left, grade):
    assert judge(page(), security(days_left=days_left, lifetime=6), TODAY).blocks[Block.SECURITY].grade is grade


def test_expired_certificate_is_bad_with_date():
    verdict = judge(page(), security(outcome=TlsOutcome.EXPIRED, days_left=-3), TODAY)
    item = verdict.blocks[Block.SECURITY].findings[0]
    assert (item.finding, item.cert_problem, item.grade) == (Finding.CERT_INVALID, TlsOutcome.EXPIRED, Grade.BAD)
    assert item.until.isoformat() == "2026-09-22"
    assert verdict.fixes[0].key is FixKey.REPLACE_CERT


def test_no_https_ignores_redirect_and_mixed_content():
    facts = security(outcome=TlsOutcome.NO_HTTPS, redirects=(RedirectState.NO_REDIRECT,), insecure=("http://x.test",))
    assert [item.finding for item in judge(page(), facts, TODAY).blocks[Block.SECURITY].findings] == [Finding.NO_HTTPS]


def test_mixed_content_and_incomplete_chain_are_worth_fixing():
    facts = security(outcome=TlsOutcome.INCOMPLETE_CHAIN, insecure=("http://cdn.test/a.jpg",))
    block = judge(page(), facts, TODAY).blocks[Block.SECURITY]
    assert block.grade is Grade.FIX
    assert [item.finding for item in block.findings] == [Finding.MIXED_CONTENT, Finding.INCOMPLETE_CHAIN]


def test_own_checks_failed_leave_security_unknown():
    failed = SecurityFacts((TlsFacts("site.test", TlsOutcome.CONNECT_FAILED),), (RedirectState.CLOSED,), ())
    assert judge(page(), failed, TODAY).blocks[Block.SECURITY].unknown_reason is UnknownReason.OWN_CHECKS_FAILED


def test_certificate_blocking_the_page_is_untrusted_even_if_our_check_passed():
    verdict = judge(None, security(cert_blocks=True), TODAY)
    assert verdict.summary is SummaryKind.CERT_BLOCKS
    assert verdict.blocks[Block.SECURITY].findings[0].finding is Finding.CERT_UNTRUSTED
    reasons = {verdict.blocks[block].unknown_reason for block in (Block.SPEED, Block.MOBILE, Block.IMAGES)}
    assert reasons == {UnknownReason.CERT_BLOCKS}
    assert [fix.key for fix in verdict.fixes] == [FixKey.REPLACE_CERT]


def test_certificate_blocking_keeps_harsher_own_reason():
    verdict = judge(None, security(outcome=TlsOutcome.EXPIRED, days_left=-3, cert_blocks=True), TODAY)
    assert verdict.troubles[0].cert_problem is TlsOutcome.EXPIRED


@pytest.mark.parametrize(("size", "grade"), [(int(2.5 * MB), Grade.GOOD), (3 * MB, Grade.FIX), (5 * MB, Grade.FIX),
                                            (6 * MB, Grade.BAD)])
def test_page_weight(size, grade):
    assert judge(page(image_facts=images(page_bytes=size)), security(), TODAY).blocks[Block.IMAGES].grade is grade


def test_fixes_are_at_most_three_most_important_first():
    busy = page(speed(lcp=6000, script=2000), mobile(viewport=AuditState.FAILED, snippet=None, zoom=AuditState.FAILED),
                images(page_bytes=8 * MB))
    verdict = judge(busy, security(outcome=TlsOutcome.EXPIRED, days_left=-1), TODAY)
    assert [fix.key for fix in verdict.fixes] == [FixKey.REPLACE_CERT, FixKey.MAKE_MOBILE, FixKey.TRIM_SCRIPTS]
