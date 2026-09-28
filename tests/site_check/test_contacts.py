import pytest

from bot.site_check.contacts_block import TAP_EVERYWHERE, judge_contacts
from bot.site_check.findings import Block, Finding, Grade
from bot.site_check.verdict import BAD_PRIORITY, FIX_PRIORITY, judge
from tests.builders import TODAY, contacts, head, page, preview, security

NONE = frozenset()


def findings(facts, page_head=None, network=NONE):
    shown = preview(page_head or head(), contact_facts=facts)
    return [(item.finding, item.detail, item.examples) for item in judge_contacts(shown, network).findings]


def test_phone_link_and_counter_is_good():
    assert judge_contacts(preview(contact_facts=contacts()), NONE).grade is Grade.GOOD


def test_phone_as_text_without_any_call_link():
    assert findings(contacts(call_links=0, text_phones=1)) == [(Finding.PHONE_NOT_LINK, None, ())]
    assert findings(contacts(call_links=0, text_phones=1, tap_blocked=True)) == \
        [(Finding.PHONE_NOT_LINK, TAP_EVERYWHERE, ())]


def test_call_link_without_code_names_the_number():
    assert findings(contacts(short=("43-43-48",))) == [(Finding.CALL_WITHOUT_CODE, None, ("43-43-48",))]


def test_no_way_to_reach_on_a_whole_page():
    assert findings(contacts(call_links=0)) == [(Finding.NO_CONTACTS, None, ())]


def test_chat_or_booking_is_a_way_to_reach():
    assert findings(contacts(call_links=0, markers={"metrika", "jivo"})) == []
    assert findings(contacts(call_links=0), network=frozenset({"yclients"})) == []


def test_form_without_policy_only_on_russian_pages():
    russian = contacts(forms=1, policy=False)
    assert findings(russian, head(lang="ru-ru")) == [(Finding.NO_PRIVACY_POLICY, None, ())]
    assert findings(russian, head(lang=None, title="Кофейня")) == [(Finding.NO_PRIVACY_POLICY, None, ())]
    assert findings(russian, head(lang="en", title="Coffee")) == []


@pytest.mark.parametrize("markers", [frozenset(), frozenset({"jivo"})])
def test_no_counter_unless_a_platform_has_its_own(markers):
    assert findings(contacts(markers=markers)) == [(Finding.NO_ANALYTICS, None, ())]
    assert findings(contacts(markers=markers | {"tilda_stats"})) == []
    assert findings(contacts(markers=markers), network=frozenset({"metrika"})) == []


def test_cut_page_gives_no_missing_findings():
    cut = contacts(call_links=0, forms=1, policy=False, markers=frozenset(), complete=False)
    assert findings(cut, head(lang="ru")) == []


def test_without_own_fetch_the_block_is_hidden():
    assert judge_contacts(None, NONE).grade is Grade.UNKNOWN
    assert judge_contacts(preview(contact_facts=None), NONE).grade is Grade.UNKNOWN


def test_contacts_never_bad_and_go_after_images():
    assert Block.CONTACTS not in BAD_PRIORITY
    assert FIX_PRIORITY.index(Block.CONTACTS) == FIX_PRIORITY.index(Block.IMAGES) + 1


def test_missing_counter_is_the_very_last_trouble_and_fix():
    # Без правила «последним» контакты (раньше ссылки в порядке 6.3) поставили бы счётчик первым.
    shown = preview(head(og_image=None), contact_facts=contacts(markers=frozenset()))
    verdict = judge(page(), security(), TODAY, shown)
    assert [item.finding for item in verdict.troubles] == [Finding.NO_PREVIEW_IMAGE, Finding.NO_ANALYTICS]
    assert verdict.fixes[-1].source.finding is Finding.NO_ANALYTICS
