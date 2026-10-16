"""Знакомые сервисы по адресам и коду (ТЗ, 5.10): счётчики посещений, статистика конструкторов, чаты, онлайн-запись.
Один список — и для своей загрузки страницы (HTML), и для запросов страницы в ответе PageSpeed (lighthouse.py).
Новый сервис — строка в PATTERNS и название в локалях (service_<имя>)."""
import re

METRIKA = "metrika"
GOOGLE_ANALYTICS = "google_analytics"
LIVEINTERNET = "liveinternet"
TOP_MAIL = "top_mail"
RAMBLER_TOP100 = "rambler_top100"
MATOMO = "matomo"
CLARITY = "clarity"
HOTJAR = "hotjar"
PLAUSIBLE = "plausible"
ROISTAT = "roistat"
CALLTOUCH = "calltouch"
COMAGIC = "comagic"
TILDA_STATS = "tilda_stats"
WIX_STATS = "wix_stats"
JIVO = "jivo"
BITRIX24 = "bitrix24"
CALLIBRI = "callibri"
ENVYBOX = "envybox"
WAZZUP = "wazzup"
YCLIENTS = "yclients"
DIKIDI = "dikidi"
PATTERNS = {
    METRIKA: r"mc\.yandex\.(?:ru|com)/(?:metrika|watch)|\bym\(\s*\d{5,}",
    GOOGLE_ANALYTICS: r"google-analytics\.com|googletagmanager\.com",
    LIVEINTERNET: r"counter\.yadro\.ru|liveinternet\.ru/click",
    TOP_MAIL: r"top-fwz1\.mail\.ru|top\.mail\.ru/counter",
    # Новые счётчики ищем по адресам самого счётчика, а не сайта сервиса: на странице бывает просто ссылка на сервис
    RAMBLER_TOP100: r"counter\.rambler\.ru|\bst\d*\.top100\.ru|kraken\.rambler\.ru",
    MATOMO: r"(?<![\w.-])(?:matomo|piwik)\.(?:js|php)\b|\.matomo\.cloud|\b_paq\.push",
    CLARITY: r"\bclarity\.ms\b",
    HOTJAR: r"\b(?:static|script|vars|in|insights)\.hotjar\.(?:com|io)",
    PLAUSIBLE: r"plausible\.io/(?:js|api/event)",
    ROISTAT: r"cloud(?:-eu)?\.roistat\.com/(?:api/site|dist)|\broistat(?:ProjectId|Host)\b",
    CALLTOUCH: r"\bmod\.calltouch\.ru|\bCalltouchDataObject\b",
    COMAGIC: r"\b(?:app|cs)\.(?:comagic|uiscom)\.ru|\bsetCsAccount\b",
    TILDA_STATS: r"tildacdn\.|\.tilda\.ws|tildastat",
    WIX_STATS: r"wixstatic\.com|parastorage\.com|\.wixsite\.com",
    JIVO: r"jivosite\.com|code\.jivo\.ru|jivo\.chat",
    BITRIX24: r"bitrix24\.(?:ru|com|by|kz)/b\d|cdn-ru\.bitrix24",
    CALLIBRI: r"callibri\.ru",
    ENVYBOX: r"envybox\.io",
    WAZZUP: r"wazzup24\.(?:com|ru)",
    YCLIENTS: r"yclients\.com",
    DIKIDI: r"dikidi\.(?:net|ru)",
}
MARKERS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in PATTERNS.items()}
# Порядок названий — в строке отчёта и в перечне «не нашёл знакомых счётчиков» (ТЗ, 5.10)
COUNTER_ORDER = (METRIKA, GOOGLE_ANALYTICS, LIVEINTERNET, TOP_MAIL, RAMBLER_TOP100, MATOMO, CLARITY, HOTJAR, PLAUSIBLE,
                 ROISTAT, CALLTOUCH, COMAGIC)
COUNTERS = frozenset(COUNTER_ORDER)
PLATFORM_STATS = frozenset({TILDA_STATS, WIX_STATS})  # своя статистика конструктора — считается счётчиком (Р19)
CHATS = frozenset({JIVO, BITRIX24, CALLIBRI, ENVYBOX, WAZZUP})
BOOKINGS = frozenset({YCLIENTS, DIKIDI})


def find_markers(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in MARKERS.items() if pattern.search(text))
