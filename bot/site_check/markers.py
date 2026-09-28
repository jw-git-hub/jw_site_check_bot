"""Знакомые сервисы по адресам и коду (ТЗ, 5.10): счётчики посещений, статистика конструкторов, чаты, онлайн-запись.
Один список — и для своей загрузки страницы (HTML), и для запросов страницы в ответе PageSpeed (lighthouse.py).
Новый сервис — строка в PATTERNS и название в локалях (service_<имя>)."""
import re

METRIKA = "metrika"
GOOGLE_ANALYTICS = "google_analytics"
LIVEINTERNET = "liveinternet"
TOP_MAIL = "top_mail"
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
COUNTER_ORDER = (METRIKA, GOOGLE_ANALYTICS, LIVEINTERNET, TOP_MAIL)  # порядок названий в строке отчёта
COUNTERS = frozenset(COUNTER_ORDER)
PLATFORM_STATS = frozenset({TILDA_STATS, WIX_STATS})  # своя статистика конструктора — считается счётчиком (Р19)
CHATS = frozenset({JIVO, BITRIX24, CALLIBRI, ENVYBOX, WAZZUP})
BOOKINGS = frozenset({YCLIENTS, DIKIDI})


def find_markers(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in MARKERS.items() if pattern.search(text))
