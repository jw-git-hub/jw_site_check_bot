import pytest

from bot.brand import BRAND
from bot.locales import TEXTS
from bot.site_check import replies
from tests.fakes import rich_text


def test_checking_and_queued():
    assert rich_text(replies.checking(TEXTS, "ru", BRAND, "пример.рф")) == ">jw_ ~/site-check\nПроверяю пример.рф…"
    assert "Передо мной ещё 2 сайта — ждать примерно 1 мин." in rich_text(replies.queued(TEXTS, "ru", BRAND, 2, 1))
    assert "There are 5 sites ahead of yours — about 3 min." in rich_text(replies.queued(TEXTS, "en", BRAND, 5, 3))


def test_queued_with_one_site_ahead_uses_singular_verb():
    assert "There is 1 site ahead of yours — about 2 min." in rich_text(replies.queued(TEXTS, "en", BRAND, 1, 2))
    assert "Передо мной ещё 1 сайт — ждать примерно 2 мин." in rich_text(replies.queued(TEXTS, "ru", BRAND, 1, 2))


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("code", replies.FAILURE_CODES)
def test_every_failure_has_text_in_both_languages(lang, code):
    text = rich_text(replies.failure(TEXTS, lang, BRAND, code, status=500, site="example.com"))
    assert text.startswith(">jw_ ~/site-check\n")
    assert "{" not in text


def test_unknown_failure_code_falls_back_to_measure_failed():
    assert "Не получилось измерить" in rich_text(replies.failure(TEXTS, "ru", BRAND, "что-то новое"))


def test_limit_user_counts_checks_and_hours():
    assert ("Лимит — 10 проверок в сутки. Следующая будет доступна через 3 ч."
            in rich_text(replies.limit_user(TEXTS, "ru", BRAND, 10, 3)))
    assert "The limit is 10 checks a day." in rich_text(replies.limit_user(TEXTS, "en", BRAND, 10, 3))


def test_social_page_offers_to_talk_about_own_site():
    message = replies.social(TEXTS, "ru", BRAND, "Instagram")
    assert "Это страница на Instagram" in rich_text(message)
    assert message["blocks"][-1]["text"][0]["button"]["url"].startswith("https://t.me/jw_dev_pro?text=")


def test_busy_names_the_site():
    assert "Сначала закончу с example.com" in rich_text(replies.busy(TEXTS, "ru", BRAND, "example.com"))
