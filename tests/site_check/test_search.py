import pytest

from bot.site_check.audits import AuditState
from bot.site_check.findings import Block, Finding, Grade
from bot.site_check.lighthouse import parse_lighthouse
from bot.site_check.search_block import BlockSource, block_source, judge_search, parse_search
from bot.site_check.verdict import judge
from tests.builders import (NOINDEX_META, TODAY, audit, head, lighthouse, page, preview, recorded_lighthouse, search,
                            security)


@pytest.mark.parametrize(("name", "findings"), [
    ("jw_dev_pro", [Finding.CLOSED_META]),
    ("example", [Finding.NO_DESCRIPTION]),
    ("wix_coffee", [Finding.NO_DESCRIPTION]),
    ("tilda_salon", []),
    ("wp_autoservice", []),
    ("joomla_tires", []),
])
def test_recorded_sites_search(name, findings):
    facts = parse_lighthouse(recorded_lighthouse(name)).search
    assert [item.finding for item in judge_search(facts).findings] == findings


@pytest.mark.parametrize(("source", "kind"), [
    ({"type": "node", "snippet": NOINDEX_META}, BlockSource.META),
    ("x-robots-tag: noindex", BlockSource.HEADER),
    ({"type": "code", "value": "X-Robots-Tag: none"}, BlockSource.HEADER),
    ({"type": "source-location", "url": "https://site.test/robots.txt?x=1", "line": 3}, BlockSource.ROBOTS_TXT),
    ({"type": "text", "value": "?"}, None),
    ("something else", None),
])
def test_block_source_forms(source, kind):
    assert block_source([{"source": source}])[0] is kind


def test_robots_place_loses_parameters():
    place = {"type": "source-location", "url": "https://site.test/robots.txt?x=1", "line": 3}
    assert block_source([{"source": place}]) == (BlockSource.ROBOTS_TXT, "https://site.test/robots.txt:3")


@pytest.mark.parametrize(("source", "finding"), [
    (BlockSource.META, Finding.CLOSED_META), (BlockSource.HEADER, Finding.CLOSED_HEADER),
    (BlockSource.ROBOTS_TXT, Finding.CLOSED_ROBOTS), (None, Finding.CLOSED_META),
])
def test_closed_page_is_bad_with_its_source(source, finding):
    verdict = judge_search(search(crawlable=AuditState.FAILED, source=source))
    assert (verdict.grade, [item.finding for item in verdict.findings]) == (Grade.BAD, [finding])


def test_robots_txt_findings():
    errors = judge_search(search(robots=AuditState.FAILED, robots_errors=("4", "7")))
    server = judge_search(search(robots=AuditState.FAILED, robots_status=503))
    lighthouse_trouble = judge_search(search(robots=AuditState.FAILED))
    no_file = judge_search(search(robots=AuditState.NOT_APPLICABLE))
    assert [(item.finding, item.count) for item in errors.findings] == [(Finding.ROBOTS_ERRORS, 2)]
    assert [(item.finding, item.detail) for item in server.findings] == [(Finding.ROBOTS_UNREACHABLE, "503")]
    assert lighthouse_trouble.findings == no_file.findings == ()


def test_robots_status_is_read_from_the_display_value():
    failed = audit(score=0, mode="binary") | {"displayValue": "Request for robots.txt returned HTTP status: 503"}
    assert parse_search(lighthouse(robots_txt=failed)["audits"]).robots_txt_status == 503


def test_title_and_description_findings():
    verdict = judge_search(search(title=AuditState.FAILED, description=AuditState.FAILED))
    assert [item.finding for item in verdict.findings] == [Finding.NO_TITLE, Finding.NO_DESCRIPTION]
    assert verdict.grade is Grade.FIX


def test_without_is_crawlable_the_block_is_unknown():
    assert judge_search(search(crawlable=AuditState.UNKNOWN)).grade is Grade.UNKNOWN
    assert judge_search(None).grade is Grade.UNKNOWN


def test_canonical_on_another_site_is_worth_fixing():
    item = judge_search(search(), preview(head(canonical="https://template-shop.example/"))).findings[0]
    assert (item.finding, item.grade, item.detail) == (Finding.CANONICAL_FOREIGN, Grade.FIX, "template-shop.example")


def test_foreign_canonical_is_named_as_it_reads():
    item = judge_search(search(), preview(head(canonical="https://xn--e1afmkfd.xn--p1ai/"))).findings[0]
    assert item.detail == "пример.рф"


@pytest.mark.parametrize(("canonical", "page_url"), [
    ("https://www.пример.рф/", "https://xn--e1afmkfd.xn--p1ai/"),
    ("HTTPS://WWW.Site.Test/page", "https://site.test/page"),
    ("/page", "https://site.test/"),
])
def test_canonical_same_site_with_www_or_unicode_is_not_foreign(canonical, page_url):
    assert judge_search(search(), preview(head(canonical=canonical), url=page_url)).findings == ()


def test_verdict_takes_search_from_the_page_and_canonical_from_the_preview():
    facts = page(search_facts=search(crawlable=AuditState.FAILED, source=BlockSource.META))
    foreign = preview(head(canonical="https://other.example/"))
    verdict = judge(facts, security(), TODAY, foreign)
    assert [item.finding for item in verdict.blocks[Block.SEARCH].findings] == \
        [Finding.CLOSED_META, Finding.CANONICAL_FOREIGN]
    assert verdict.troubles[0].finding is Finding.CLOSED_META
