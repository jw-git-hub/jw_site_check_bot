from bot.core.secret_patterns import SECRET_PATTERNS
from tests.fakes import fake_google_key, fake_telegram_token

TOKEN_KIND = "похоже на токен бота Telegram"
GOOGLE_KIND = "похоже на ключ Google API"
PRIVATE_KIND = "приватный ключ"
TAILSCALE_KIND = "имя устройства в Tailscale"


def found(kind: str, text: str) -> bool:
    return bool(SECRET_PATTERNS[kind].search(text))


def test_token_found_right_after_bot_in_request_url():
    assert found(TOKEN_KIND, f"https://api.telegram.org/bot{fake_telegram_token()}/getMe")


def test_short_number_before_colon_is_not_token():
    assert not found(TOKEN_KIND, "12345:" + "a" * 35)


def test_google_key_found():
    assert found(GOOGLE_KIND, f"key={fake_google_key()}&x=1")


def test_private_key_header_found():
    assert found(PRIVATE_KIND, "-----BEGIN " + "RSA PRIVATE KEY-----")


def test_tailscale_device_name_found_but_zone_mention_is_not():
    assert found(TAILSCALE_KIND, "ssh laptop.tail1a2b3c." + "ts" + ".net")
    assert not found(TAILSCALE_KIND, "имена `*.ts" + ".net` ловит сторож")
