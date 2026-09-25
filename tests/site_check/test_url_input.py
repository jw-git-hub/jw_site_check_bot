import pytest

from bot.site_check.url_input import BAD_ADDRESS, NOT_A_LINK, SOCIAL, Rejection, Target, parse_input


def target(text: str, entity_urls: list[str] | None = None) -> Target:
    parsed = parse_input(text, entity_urls or [])
    assert isinstance(parsed, Target), parsed
    return parsed


def rejection(text: str, entity_urls: list[str] | None = None) -> Rejection:
    parsed = parse_input(text, entity_urls or [])
    assert isinstance(parsed, Rejection), parsed
    return parsed


def test_bare_domain_becomes_https_without_given_scheme():
    parsed = target("example.com")
    assert (parsed.scheme, parsed.scheme_given, parsed.host) == ("https", False, "example.com")
    assert (parsed.display, parsed.url) == ("example.com", "https://example.com/")


def test_full_link_keeps_page_and_parameters_for_measuring_only():
    parsed = target("https://www.Example.com/uslugi?utm_source=fb#top")
    assert parsed.host == "www.example.com"
    assert parsed.url == "https://www.example.com/uslugi?utm_source=fb"
    assert parsed.stored_url == "https://www.example.com/uslugi"
    assert parsed.display == "www.example.com/uslugi"


def test_cyrillic_domain_in_sentence():
    parsed = target("Гляньте мой сайт пример.рф/uslugi?utm_source=fb, пожалуйста")
    assert parsed.host == "xn--e1afmkfd.xn--p1ai"
    assert parsed.display == "пример.рф/uslugi"
    assert parsed.stored_url == "https://пример.рф/uslugi"


def test_cyrillic_path_is_percent_encoded_for_measuring():
    assert target("пример.рф/услуги").url == "https://xn--e1afmkfd.xn--p1ai/%D1%83%D1%81%D0%BB%D1%83%D0%B3%D0%B8"


def test_telegram_link_entity_wins_over_text():
    assert target("вот тут", ["https://site.org/page"]).host == "site.org"


def test_trailing_punctuation_is_dropped():
    assert target("(example.com).").host == "example.com"
    assert target("https://example.com/page).").url == "https://example.com/page"


def test_given_http_scheme_is_kept():
    parsed = target("http://example.com")
    assert (parsed.scheme, parsed.scheme_given) == ("http", True)


@pytest.mark.parametrize("text", [
    "http://localhost:8080", "https://example.com:8443/", "http://user:pass@example.com/", "ftp://example.com/",
])
def test_unusual_addresses_are_refused(text):
    assert rejection(text, [text]).code == BAD_ADDRESS


@pytest.mark.parametrize("url", ["https://127.1/", "http://2130706433/", "http://0x7f.1/", "http://[::1]/",
                                 "http://10.0.0.1/", "http://router.lan/", "https://nas.local/",
                                 "http://0x7f000001/", "http://[::ffff:127.0.0.1]/", "http://[fc00::1]/",
                                 "http://[fe80::1]/"])
def test_ip_literals_and_service_zones_are_refused(url):
    assert rejection(url, [url]).code == BAD_ADDRESS


@pytest.mark.parametrize(("url", "platform"), [
    ("https://instagram.com/shop", "Instagram"), ("https://www.instagram.com/shop", "Instagram"),
    ("https://t.me/somechannel", "Telegram"), ("https://www.google.com/maps/place/x", "Google Maps"),
    ("https://maps.app.goo.gl/abc", "Google Maps"), ("https://zalo.me/123", "Zalo"), ("https://taplink.cc/x", "Taplink"),
])
def test_social_pages_are_not_measured(url, platform):
    parsed = rejection(url, [url])
    assert (parsed.code, parsed.platform) == (SOCIAL, platform)


def test_google_search_is_not_a_social_page():
    assert target("https://www.google.com/search").host == "www.google.com"


@pytest.mark.parametrize("text", ["просто текст", "1.45 раза быстрее", "x" * 2001])
def test_no_link_in_text(text):
    assert rejection(text).code == NOT_A_LINK


@pytest.mark.parametrize("url", [
    "http://127.0.0.１/", "http://１２７.０.０.１/", "http://𝟏𝟐𝟕.𝟎.𝟎.𝟏/", "http://127.0.0。1/",
    "http://nas.router。lan/", "http://x.ＬＯＣＡＬ/", "http://foo.ｏｎｉｏｎ/", "http://a.localhost。/",
])
def test_unicode_lookalikes_are_refused_after_normalization(url):
    assert rejection(url, [url]).code == BAD_ADDRESS


def test_unicode_lookalike_zone_in_plain_text_is_refused():
    assert rejection("router.ｌａｎ").code == BAD_ADDRESS


def test_unicode_dot_lookalike_does_not_hide_social_platform():
    parsed = rejection("instagram.com。/x", ["instagram.com。/x"])
    assert (parsed.code, parsed.platform) == (SOCIAL, "Instagram")


@pytest.mark.parametrize("text", [
    "example.com/login?next=https://example.com/",
    "пример.рф/?r=http://x.ru",
    "example.com/#http://x",
])
def test_scheme_in_query_or_fragment_does_not_count_as_given(text):
    parsed = target(text)
    assert (parsed.scheme, parsed.scheme_given) == ("https", False)


def test_punycode_tld_in_plain_text_keeps_full_domain_and_path():
    parsed = target("xn--e1afmkfd.xn--p1ai/uslugi")
    assert parsed.host == "xn--e1afmkfd.xn--p1ai"
    assert parsed.display == "пример.рф/uslugi"
