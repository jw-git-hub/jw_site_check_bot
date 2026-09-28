import pytest

from bot.site_check.findings import Block, Finding, Grade
from bot.site_check.page_fetch import FetchFailure, ImageState
from bot.site_check.preview_block import judge_preview
from bot.site_check.verdict import judge
from tests.builders import TODAY, head, page, preview, security


@pytest.mark.parametrize(("state", "finding", "detail"), [
    (ImageState.BROKEN, Finding.PREVIEW_IMAGE_BROKEN, "404"),
    (ImageState.NOT_IMAGE, Finding.PREVIEW_IMAGE_BROKEN, None),
    (ImageState.SVG, Finding.PREVIEW_IMAGE_SVG, None),
    (ImageState.RELATIVE, Finding.PREVIEW_IMAGE_RELATIVE, None),
])
def test_image_problems_are_worth_fixing(state, finding, detail):
    status = 404 if state is ImageState.BROKEN else 200
    verdict = judge_preview(preview(image_state=state, image_status=status))
    assert [(item.finding, item.grade, item.detail) for item in verdict.findings] == [(finding, Grade.FIX, detail)]


def test_image_that_could_not_be_checked_is_not_a_finding():
    assert judge_preview(preview(image_state=ImageState.UNKNOWN)).grade is Grade.GOOD


def test_missing_image_and_title_are_both_named():
    verdict = judge_preview(preview(head(title=None, og_image=None)))
    assert [item.finding for item in verdict.findings] == [Finding.NO_PREVIEW_IMAGE, Finding.NO_PREVIEW_TITLE]


def test_cut_head_gives_no_missing_image_or_title_findings():
    assert judge_preview(preview(head(title=None, og_image=None, complete=False))).findings == ()


@pytest.mark.parametrize("failure", list(FetchFailure))
def test_failed_own_fetch_hides_the_block(failure):
    assert judge_preview(preview(failure=failure)).grade is Grade.UNKNOWN
    assert judge_preview(None).grade is Grade.UNKNOWN


def test_verdict_takes_the_preview():
    verdict = judge(page(), security(), TODAY, preview(head(og_image=None)))
    assert verdict.blocks[Block.PREVIEW].grade is Grade.FIX
    assert verdict.troubles[0].finding is Finding.NO_PREVIEW_IMAGE
