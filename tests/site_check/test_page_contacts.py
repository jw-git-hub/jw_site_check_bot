import pytest

from bot.site_check.markers import JIVO, METRIKA, TILDA_STATS, TOP_MAIL, YCLIENTS, find_markers
from bot.site_check.page_contacts import parse_contacts

TILDA_LIKE = """<html><head><meta name="format-detection" content="telephone=no"></head><body>
<div class="t-text">Звоните: +38 050 040 21 60</div><script src="https://static.tildacdn.com/js/tilda.js"></script>"""
JOOMLA_LIKE = """<body><a href="tel:+79127127004">+7 912 712-70-04</a> <a href="tel:43-43-48">43-43-48</a>
<a href="/use-personal-info.html">Политикой обработки персональных данных</a>
<script src="https://mc.yandex.ru/metrika/tag.js"></script><img src="https://top-fwz1.mail.ru/counter?id=1"></body>"""
WIX_FORM = """<html lang="ru"><body><form><input aria-label="имя-*"><input type="email" name="email">
<textarea name="message"></textarea></form><p>+7 343 542-18-57</p></body>"""


def test_tilda_phone_as_text_without_link_and_tap_blocked():
    facts = parse_contacts(TILDA_LIKE, complete=True)
    assert (facts.text_phones, facts.call_links, facts.tap_blocked) == (1, 0, True)
    assert TILDA_STATS in facts.markers


def test_call_links_full_and_without_code():
    facts = parse_contacts(JOOMLA_LIKE, complete=True)
    assert (facts.call_links, facts.short_call_links, facts.policy_link) == (1, ("43-43-48",), True)
    assert {METRIKA, TOP_MAIL} <= facts.markers


def test_contact_form_with_personal_fields_and_no_policy():
    facts = parse_contacts(WIX_FORM, complete=True)
    assert (facts.personal_forms, facts.policy_link, facts.text_phones) == (1, False, 1)


def test_search_form_is_not_personal():
    html = '<form action="/search"><input type="search" name="q"><button>Найти</button></form>'
    assert parse_contacts(html, complete=True).personal_forms == 0


@pytest.mark.parametrize(("href", "field"), [
    ("https://wa.me/79127127004", "whatsapp"), ("https://api.whatsapp.com/send?phone=7912", "whatsapp"),
    ("https://t.me/jw_dev_pro", "telegram"), ("tg://resolve?domain=x", "telegram"),
    ("viber://chat?number=7912", "viber"), ("mailto:hello@shop.example", "email"),
])
def test_messengers_and_mail(href, field):
    assert getattr(parse_contacts(f'<a href="{href}">написать</a>', complete=True), field) is True


def test_inn_prices_and_dates_are_not_phones():
    html = "<p>ИНН 7707083893, ОГРН 1027700132195. Цена 1 500 000 руб. Акция до 2026-09-28, код 43-43-48.</p>"
    assert parse_contacts(html, complete=True).text_phones == 0


def test_phones_in_scripts_and_styles_are_not_text():
    html = '<script>var phone = "+7 912 712-70-04";</script><style>.a:after{content:"8 800 555-35-35"}</style>'
    assert parse_contacts(html, complete=True).text_phones == 0


def test_same_phone_twice_counts_once_and_short_service_numbers_are_not_calls():
    html = '<p>+7 912 712-70-04</p><p>8 (912) 712-70-04</p><a href="tel:112">112</a>'
    facts = parse_contacts(html, complete=True)
    assert (facts.text_phones, facts.call_links, facts.short_call_links) == (1, 0, ())


def test_chat_and_booking_are_found_by_their_code():
    assert {JIVO, YCLIENTS} <= find_markers('<script src="//code.jivo.ru/widget/x"></script> n123.yclients.com/')


def test_completeness_is_passed_through():
    assert parse_contacts("<p>кусок</p>", complete=False).complete is False
