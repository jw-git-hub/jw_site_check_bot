import pytest

from bot.site_check.audits import AuditState
from bot.site_check.findings import Block, Finding, Grade
from bot.site_check.lighthouse import parse_lighthouse
from bot.site_check.readability_block import judge_readability, parse_readability
from bot.site_check.thresholds import ALT_MIN_SIDE_PX
from bot.site_check.verdict import SummaryKind, judge
from tests.builders import TODAY, audit, lighthouse, page, readability, recorded_lighthouse, security


def node(snippet='<img src="/images/photo.jpg">', label="", width=300, height=200) -> dict:
    return {"node": {"snippet": snippet, "nodeLabel": label, "selector": "div > a",
                     "boundingRect": {"width": width, "height": height}}}


def failed(*items: dict) -> dict:
    return audit(score=0, mode="binary", items=list(items))


def facts(**audits):
    return parse_readability(lighthouse(**audits)["audits"])


@pytest.mark.parametrize(("name", "findings"), [
    ("jw_dev_pro", []),
    ("example", []),
    ("tilda_salon", [Finding.NO_LANG]),  # плашка Tilda и значки 40–100 точек — не в счёт
    ("joomla_tires", [Finding.LOW_CONTRAST, Finding.NO_ALT]),
    ("wix_coffee", [Finding.LOW_CONTRAST]),
    ("wp_autoservice", [Finding.LOW_CONTRAST]),
])
def test_recorded_sites_readability(name, findings):
    verdict = judge_readability(parse_lighthouse(recorded_lighthouse(name)).readability)
    assert [item.finding for item in verdict.findings] == findings


def test_joomla_images_without_captions_are_named():
    item = judge_readability(parse_lighthouse(recorded_lighthouse("joomla_tires")).readability).findings[1]
    assert (item.count, item.examples) == (9, ("20let.png", "avtomoyka-open-340x210.jpg"))


def test_platform_badges_are_not_the_owners_text():
    tilda = node('<a class="t-tildalabel-free__txt-link" href="https://tilda.cc/">', "How to remove this block?")
    wix = node('<a href="https://www.wix.com/">', "Made with Wix")
    assert facts(color_contrast=failed(tilda, wix)).contrast_count == 0


def test_hidden_zero_size_elements_are_skipped():
    hidden = node('<a href="/x">', "Скрытая ссылка", width=0, height=0)
    shown = node('<a class="btn" href="/tseny/">', "НАШИ ЦЕНЫ", width=183, height=47)
    result = facts(color_contrast=failed(hidden, shown))
    assert (result.contrast_count, result.contrast_examples) == (1, ("НАШИ ЦЕНЫ",))


def test_small_icons_are_not_counted_as_images_without_captions():
    icon = node('<img src="https://static.tildacdn.com/lib/tildaicon/icon.svg">', width=100, height=100)
    diploma = node('<img src="/images/diplom-tm-2025-m.jpg">', width=136, height=187)
    edge = node('<img src="/images/edge.jpg">', width=ALT_MIN_SIDE_PX, height=300)
    result = facts(image_alt=failed(icon, diploma, edge))
    assert (result.alt_missing, result.alt_missing_count, result.alt_icons_skipped) == \
        (("diplom-tm-2025-m.jpg", "edge.jpg"), 2, 1)


def test_same_picture_counts_once_and_cut_addresses_have_no_name():
    twice = node('<img src="/images/slide.jpg">')
    cut = node('<img src="https://cdn.example/very/long/address/that/lighthouse/cut…">')
    result = facts(image_alt=failed(twice, twice, cut))
    assert (result.alt_missing, result.alt_missing_count) == (("slide.jpg",), 2)


def test_image_without_size_is_counted():
    assert facts(image_alt=failed({"node": {"snippet": '<img src="/a.jpg">'}})).alt_missing_count == 1


@pytest.mark.parametrize(("has_lang", "lang_valid"), [(failed(), audit()), (audit(), failed())])
def test_missing_or_invalid_language_is_worth_fixing(has_lang, lang_valid):
    result = facts(html_has_lang=has_lang, html_lang_valid=lang_valid)
    assert [item.finding for item in judge_readability(result).findings] == [Finding.NO_LANG]


def test_readability_is_never_bad_and_names_one_example():
    worst = facts(color_contrast=failed(node(label="НАШИ ЦЕНЫ"), node(label="Хорошо")),
                  image_alt=failed(node()), html_has_lang=failed())
    verdict = judge_readability(worst)
    assert verdict.grade is Grade.FIX
    assert (verdict.findings[0].count, verdict.findings[0].examples) == (2, ("НАШИ ЦЕНЫ",))


def test_without_contrast_and_alt_audits_the_block_is_unknown():
    audits = lighthouse()["audits"]
    del audits["color-contrast"], audits["image-alt"]
    assert judge_readability(parse_readability(audits)).grade is Grade.UNKNOWN
    assert judge_readability(None).grade is Grade.UNKNOWN


def test_controls_and_fields_are_counted_for_the_owner_only():
    result = facts(link_name=failed(node('<a href="/x">'), node('<a href="#">', width=0)),
                   button_name=failed(node("<button>")), label=failed(node("<input>")))
    assert (result.unnamed_controls, result.unlabeled_fields) == (2, 1)
    assert judge_readability(result).findings == ()


def test_verdict_takes_readability_from_the_page():
    verdict = judge(page(readability_facts=readability(contrast=AuditState.FAILED, examples=("НАШИ ЦЕНЫ",))),
                    security(), TODAY)
    assert verdict.blocks[Block.READABILITY].grade is Grade.FIX
    assert verdict.summary is SummaryKind.ONLY_FIX
