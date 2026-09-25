"""Из текста человека — адрес сайта (ТЗ, раздел 4). Сеть не трогает: внутренние адреса по DNS ловит net_guard."""
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, quote, urlsplit

import idna

MAX_INPUT_LENGTH = 2000
HTTPS = "https"
HTTP = "http"
ALLOWED_SCHEMES = (HTTP, HTTPS)
ALLOWED_PORTS = (None, 80, 443)
ROOT_PATH = "/"
PATH_SAFE = "/%:@!$&'()*+,;=-._~"
QUERY_SAFE = PATH_SAFE + "?"
TRAILING_PUNCTUATION = ".,;:!?)]}»\"'"
SERVICE_ZONES = frozenset({"local", "localhost", "lan", "home", "internal", "intranet", "corp", "test", "invalid",
                           "onion", "arpa"})
DOMAIN_WORD = re.compile(r"(?:https?://)?(?:[\w-]+\.)+(?:xn--[\w-]+|[^\W\d_]{2,})(?::\d+)?(?:[/?#]\S*)?",
                         re.IGNORECASE)
SCHEME_PREFIX = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)

NOT_A_LINK = "not_a_link"
BAD_ADDRESS = "bad_address"
SOCIAL = "social"

PLATFORM_HOSTS: dict[str, str] = {
    "instagram.com": "Instagram", "facebook.com": "Facebook", "fb.com": "Facebook", "m.me": "Messenger",
    "t.me": "Telegram", "telegram.me": "Telegram", "vk.com": "VK", "ok.ru": "OK",
    "youtube.com": "YouTube", "youtu.be": "YouTube", "tiktok.com": "TikTok",
    "wa.me": "WhatsApp", "whatsapp.com": "WhatsApp", "zalo.me": "Zalo", "line.me": "LINE", "lin.ee": "LINE",
    "x.com": "X", "twitter.com": "X", "threads.net": "Threads", "linkedin.com": "LinkedIn",
    "taplink.cc": "Taplink", "taplink.ws": "Taplink", "linktr.ee": "Linktree",
    "maps.google.com": "Google Maps", "maps.app.goo.gl": "Google Maps", "2gis.ru": "2GIS", "2gis.com": "2GIS",
}
PLATFORM_PATHS: dict[tuple[str, str], str] = {
    ("google.com", "/maps"): "Google Maps", ("goo.gl", "/maps"): "Google Maps", ("yandex.ru", "/maps"): "Yandex Maps",
}


@dataclass(frozen=True)
class Target:
    scheme: str
    scheme_given: bool
    host: str
    display_host: str
    path: str
    query: str

    def url_with(self, scheme: str) -> str:
        query = f"?{quote(self.query, safe=QUERY_SAFE)}" if self.query else ""
        return f"{scheme}://{self.host}{quote(self.path, safe=PATH_SAFE)}{query}"

    @property
    def url(self) -> str:
        return self.url_with(self.scheme)

    @property
    def stored_url(self) -> str:
        return f"{self.scheme}://{self.display_host}{self.path}"

    @property
    def display(self) -> str:
        return self.display_host if self.path == ROOT_PATH else self.display_host + self.path


@dataclass(frozen=True)
class Rejection:
    code: str
    platform: str | None = None


def parse_input(text: str, entity_urls: list[str]) -> Target | Rejection:
    candidate = _candidate(text, entity_urls)
    if candidate is None:
        return Rejection(NOT_A_LINK)
    scheme_given = bool(SCHEME_PREFIX.match(candidate))
    parts = _split(candidate if scheme_given else f"{HTTPS}://{candidate}")
    if parts is None:
        return Rejection(BAD_ADDRESS)
    return _target(parts, scheme_given)


def _candidate(text: str, entity_urls: list[str]) -> str | None:
    if len(text) > MAX_INPUT_LENGTH:
        return None
    if entity_urls:
        return entity_urls[0].strip().rstrip(TRAILING_PUNCTUATION)
    match = DOMAIN_WORD.search(text)
    return match.group(0).rstrip(TRAILING_PUNCTUATION) if match else None


def _split(url: str) -> SplitResult | None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ALLOWED_SCHEMES or port not in ALLOWED_PORTS:
        return None
    return None if parts.username or parts.password else parts


def _target(parts: SplitResult, scheme_given: bool) -> Target | Rejection:
    host = (parts.hostname or "").rstrip(".")
    if _is_ip_literal(host):  # idna отказывает на ::1 и подобных — ловим их до кодирования
        return Rejection(BAD_ADDRESS)
    try:
        ascii_host = idna.encode(host, uts46=True).decode("ascii").rstrip(".")
        display_host = idna.decode(ascii_host)
    except idna.IDNAError:
        return Rejection(NOT_A_LINK)
    # UTS46 приводит похожие на цифры и буквы юникод-символы (полноширинные, кружком, математические,
    # «。») к ASCII — проверяем адрес и зону ещё раз, уже на итоговом хосте, иначе они проходят мимо П3
    if _is_ip_literal(ascii_host) or "." not in ascii_host or ascii_host.rsplit(".", 1)[-1] in SERVICE_ZONES:
        return Rejection(BAD_ADDRESS)
    path = parts.path or ROOT_PATH
    platform = _platform(ascii_host, path)
    if platform:
        return Rejection(SOCIAL, platform)
    return Target(parts.scheme.lower(), scheme_given, ascii_host, display_host, path, parts.query)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(host)  # ловит и старые записи адреса: 127.1, 2130706433, 0x7f.1
        return True
    except OSError:
        return False


def _platform(host: str, path: str) -> str | None:
    for domain, name in PLATFORM_HOSTS.items():
        if _within(host, domain):
            return name
    for (domain, prefix), name in PLATFORM_PATHS.items():
        if _within(host, domain) and path.startswith(prefix):
            return name
    return None


def _within(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)
