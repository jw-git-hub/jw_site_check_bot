import pytest

from bot.locales import TEXTS
from bot.site_check.lighthouse import parse_lighthouse
from bot.site_check.markers import COUNTER_ORDER, COUNTERS, find_markers
from bot.site_check.thresholds import RULES_VERSION
from tests.builders import audit, lighthouse

NEW_COUNTERS = ("rambler_top100", "matomo", "clarity", "hotjar", "plausible", "roistat", "calltouch", "comagic")

# Как счётчик выглядит в коде страницы и среди запросов страницы (проверено по документации и живым сайтам).
HTML_SAMPLES = {
    "rambler_top100": '<script src="//counter.rambler.ru/top100.jcn?4512345"></script>',
    "matomo": '<script>g.src=u+"matomo.js"; _paq.push(["trackPageView"]);</script>',
    "clarity": '<script>y.src="https://www.clarity.ms/tag/"+i;</script>',
    "hotjar": '<script>r.src=t+h._hjSettings.hjid; "https://static.hotjar.com/c/hotjar-"</script>',
    "plausible": '<script defer data-domain="site.ru" src="https://plausible.io/js/script.js"></script>',
    "roistat": "<script>(function(w,d,s,h,id){w.roistatProjectId=id;w.roistatHost=h;})(window,document,'script',"
               "'cloud.roistat.com','a1b2');</script>",
    "calltouch": '<script>s.src="https://mod.calltouch.ru/"+n+"?id="+cId;</script>',
    "comagic": '<script>__cs.push(["setCsAccount","xx"])</script><script src="https://app.comagic.ru/static/cs.min.js">'
               "</script>",
}
REQUEST_SAMPLES = {
    "rambler_top100": "https://st.top100.ru/top100/top100.js",
    "matomo": "https://stat.site.ru/matomo.js",
    "clarity": "https://www.clarity.ms/tag/abc123",
    "hotjar": "https://static.hotjar.com/c/hotjar-3456789.js?sv=6",
    "plausible": "https://plausible.io/js/script.js",
    "roistat": "https://cloud.roistat.com/api/site/1.0/a1b2/init?referrer=https%3A%2F%2Fsite.ru",
    "calltouch": "https://mod.calltouch.ru/init.js?id=abcd",
    "comagic": "https://app.uiscom.ru/static/cs.min.js",
}
# Ссылки и слова о сервисах — не счётчик на странице.
LINKS_NOT_COUNTERS = (
    '<a href="https://hotjar.com/blog/heatmaps">Hotjar</a>',
    '<a href="https://www.hotjar.com/">hotjar.com</a> и matomo.org, clarity.microsoft.com',
    '<a href="https://roistat.com/">Roistat</a> <a href="https://cloud.roistat.com/user/login">войти</a>',
    '<a href="https://www.calltouch.ru/product/">Calltouch</a>',
    '<a href="https://www.comagic.ru/">CoMagic</a> <a href="https://www.uiscom.ru/">UIS</a>',
    '<a href="https://plausible.io/blog">Plausible</a>',
    '<a href="https://top100.rambler.ru/navi/">Топ-100</a> <a href="https://www.top100.ru/">рейтинг</a>',
)


@pytest.mark.parametrize("name", NEW_COUNTERS)
def test_new_counter_is_found_in_page_code(name):
    assert name in find_markers(HTML_SAMPLES[name])


@pytest.mark.parametrize("name", NEW_COUNTERS)
def test_new_counter_is_found_in_page_requests(name):
    requests = [{"url": "https://site.test/app.js"}, {"url": REQUEST_SAMPLES[name]}]
    facts = parse_lighthouse(lighthouse(network_requests=audit(mode="informative", items=requests)))
    assert name in facts.service_markers


@pytest.mark.parametrize("name", NEW_COUNTERS)
def test_new_counter_counts_as_a_counter(name):
    assert name in COUNTERS


@pytest.mark.parametrize("html", LINKS_NOT_COUNTERS)
def test_a_link_or_a_word_about_a_service_is_not_its_counter(html):
    assert find_markers(html) == frozenset()


def test_counter_order_starts_with_the_familiar_four_then_the_new_ones():
    assert COUNTER_ORDER == ("metrika", "google_analytics", "liveinternet", "top_mail") + NEW_COUNTERS


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_counter_has_a_name_in_both_languages(lang):
    assert all(TEXTS.get(lang, f"service_{name}") for name in COUNTER_ORDER)


def test_rules_version_marks_the_wider_counter_list():
    assert RULES_VERSION == "1.2.1"
