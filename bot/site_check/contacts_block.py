"""«Заявки и контакты» (ТЗ, 5.10): как клиенту связаться со страницы и видит ли владелец посещения.

«Плохо» не бывает: контакт может быть на другой странице. Страница прочитана не целиком — находок «нет …» не
выносим (ТЗ, 5.10): то, чего не нашли в обрезанном, могло стоять в подвале.
"""
import re

from bot.site_check.findings import Block, BlockVerdict, Finding, FindingItem, Grade, graded, not_checked
from bot.site_check.head_tags import HeadTags
from bot.site_check.markers import BOOKINGS, CHATS, COUNTERS, PLATFORM_STATS
from bot.site_check.page_contacts import ContactFacts
from bot.site_check.page_fetch import PagePreview

RUSSIAN_LANG_PREFIX = "ru"
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
TAP_EVERYWHERE = "everywhere"  # format-detection telephone=no: номер не нажимается и на iPhone


def judge_contacts(preview: PagePreview | None, network_markers: frozenset[str]) -> BlockVerdict:
    """Главный источник — своя загрузка страницы (ТЗ, 6.1): нет её — «неизвестно», блок не печатается."""
    if preview is None or preview.head is None or preview.contacts is None:
        return not_checked(Block.CONTACTS)
    facts = preview.contacts
    markers = facts.markers | network_markers
    return graded(Block.CONTACTS, [*_reach(facts, markers), *_policy(facts, preview.head), *_counter(facts, markers)])


def has_way_to_reach(facts: ContactFacts, markers: frozenset[str]) -> bool:
    ways = (facts.call_links, facts.short_call_links, facts.text_phones, facts.whatsapp, facts.telegram, facts.viber,
            facts.email, facts.personal_forms, markers & (CHATS | BOOKINGS))
    return any(ways)


def _reach(facts: ContactFacts, markers: frozenset[str]) -> list[FindingItem]:
    if not has_way_to_reach(facts, markers):
        return [FindingItem(Finding.NO_CONTACTS, Grade.FIX)] if facts.complete else []
    found = []
    if facts.text_phones and not facts.call_links and not facts.short_call_links:
        where = TAP_EVERYWHERE if facts.tap_blocked else None
        found.append(FindingItem(Finding.PHONE_NOT_LINK, Grade.FIX, detail=where))
    if facts.short_call_links:
        found.append(FindingItem(Finding.CALL_WITHOUT_CODE, Grade.FIX, examples=facts.short_call_links[:1]))
    return found


def is_russian(head: HeadTags) -> bool:
    """Русская страница (ТЗ, 5.10, Р20): lang с «ru», а без lang — кириллица в заголовке."""
    if head.lang:
        return head.lang.lower().startswith(RUSSIAN_LANG_PREFIX)
    return bool(head.title and CYRILLIC.search(head.title))


def _policy(facts: ContactFacts, head: HeadTags) -> list[FindingItem]:
    missing = facts.complete and facts.personal_forms and not facts.policy_link and is_russian(head)
    return [FindingItem(Finding.NO_PRIVACY_POLICY, Grade.FIX)] if missing else []


def _counter(facts: ContactFacts, markers: frozenset[str]) -> list[FindingItem]:
    counted = markers & (COUNTERS | PLATFORM_STATS)
    return [] if counted or not facts.complete else [FindingItem(Finding.NO_ANALYTICS, Grade.FIX)]
