"""Подделки для тестов. Секреты собираются в коде, чтобы файлы тестов проходили хук pre-commit."""

TELEGRAM_TOKEN_TAIL = "Ab1_-" * 7  # 35 знаков после двоеточия
GOOGLE_KEY_TAIL = "x1Y2z3" * 5 + "abcde"  # 35 знаков после префикса


def fake_telegram_token() -> str:
    return "1234567890:" + TELEGRAM_TOKEN_TAIL


def fake_google_key() -> str:
    return "AI" + "za" + GOOGLE_KEY_TAIL
