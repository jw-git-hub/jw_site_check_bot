"""Утверждённые владельцем после вычитки тексты (16.10.2026, ТЗ 7.4) — слово в слово с таблицей ТЗ."""
import pytest

from bot.locales import TEXTS

WELCOME_RU = ("Пришлите ссылку на сайт — проверю, как быстро он открывается с телефона, есть ли мобильная версия, "
              "работает ли защита, сколько весят картинки, виден ли сайт в Google, как ссылка выглядит в мессенджерах, "
              "удобно ли читать текст и легко ли с вами связаться. Отчёт — словами, обычно меньше чем за минуту.")
WELCOME_EN = ("Send me a link to a website — I'll check how fast it opens on a phone, whether it has a mobile version, "
              "whether the connection is secure, how heavy the images are, whether it shows up in Google, how the "
              "link looks when shared in messengers, whether the text is easy to read, and how easily people can "
              "contact you. You'll get a report in plain words, usually in under a minute.")
BLOCKED_EN = ("The site blocks automated checks — this happens when a site is behind bot protection. Nothing's "
              "broken, but I can't check it.")


def test_welcome_and_blocked_are_the_approved_texts():
    assert TEXTS.get("ru", "welcome") == WELCOME_RU
    assert TEXTS.get("en", "welcome") == WELCOME_EN
    assert TEXTS.get("en", "blocked") == BLOCKED_EN


@pytest.mark.parametrize("key", ["preview_image_broken", "preview_image_broken_plain", "preview_image_svg",
                                 "preview_image_relative", "preview_no_image", "fix_add_preview_image",
                                 "fix_raster_preview_image", "trouble_preview_image_svg",
                                 "trouble_preview_image_relative"])
@pytest.mark.parametrize("lang", ["ru", "en"])
def test_preview_texts_say_messengers_not_telegram(lang, key):
    assert "Telegram" not in TEXTS.get(lang, key, status=404)


@pytest.mark.parametrize("key", ["fix_unblock_meta", "fix_unblock_header", "search_closed_meta", "search_closed_header"])
def test_russian_noindex_is_explained_in_words(key):
    assert "запрет на показ в поиске (noindex)" in TEXTS.get("ru", key)


def test_english_noindex_is_a_tag_or_a_header_not_a_ban():
    keys = ("fix_unblock_meta", "fix_unblock_header", "search_closed_meta", "search_closed_header")
    assert not any("ban" in TEXTS.get("en", key) for key in keys)
    assert "noindex header" in TEXTS.get("en", "search_closed_header")
    assert "noindex tag" in TEXTS.get("en", "search_closed_meta")
