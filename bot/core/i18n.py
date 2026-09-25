"""Язык и числа (ТЗ, Р11): ru для ru, uk, be, kk, остальным en; склонения, секунды, размеры, даты.

Тексты — в модулях локалей бота (bot/locales/ru.py, en.py): TEXTS, WORDS, UNITS, MONTHS, DECIMAL_SEPARATOR.
"""
from collections.abc import Mapping
from datetime import date
from types import ModuleType
from typing import Literal

Lang = Literal["ru", "en"]
RUSSIAN_CODES = frozenset({"ru", "uk", "be", "kk"})
BYTES_IN_KB = 1024
BYTES_IN_MB = 1024 * 1024
KB_DISPLAY_LIMIT = 1000
MS_IN_SECOND = 1000
WHOLE_SECONDS_FROM = 10
DECIMALS_SHOWN = 1
TEENS = range(11, 15)
FEW = range(2, 5)


def detect_lang(language_code: str | None) -> Lang:
    base = (language_code or "").split("-")[0].lower()
    return "ru" if base in RUSSIAN_CODES else "en"


def plural_ru(number: float, forms: tuple[str, str, str]) -> str:
    """1 сайт, 2 сайта, 5 сайтов; дробное — как «1,4 секунды»."""
    if number != int(number):
        return forms[1]
    remainder = abs(int(number)) % 100
    if remainder in TEENS:
        return forms[2]
    last = remainder % 10
    if last == 1:
        return forms[0]
    return forms[1] if last in FEW else forms[2]


class Texts:
    def __init__(self, locales: Mapping[Lang, ModuleType]):
        self._locales = locales

    def get(self, lang: Lang, key: str, **params: object) -> str:
        return self._locales[lang].TEXTS[key].format(**params)

    def count(self, lang: Lang, number: float, word: str) -> str:
        forms = self._locales[lang].WORDS[word]
        form = plural_ru(number, forms) if lang == "ru" else forms[0 if number == 1 else 1]
        return f"{self.number(lang, number)} {form}"

    def number(self, lang: Lang, value: float) -> str:
        if value == int(value):
            return str(int(value))
        return f"{value:.{DECIMALS_SHOWN}f}".replace(".", self._locales[lang].DECIMAL_SEPARATOR)

    def seconds(self, lang: Lang, milliseconds: float) -> str:
        value = milliseconds / MS_IN_SECOND
        shown = round(value) if value >= WHOLE_SECONDS_FROM else round(value, DECIMALS_SHOWN)
        return self.count(lang, shown, "second")

    def size(self, lang: Lang, size_bytes: int) -> str:
        """Меньше 1000 КБ (по округлению) — в целых КБ, дальше — в МБ с одним знаком (ТЗ, 7.1)."""
        units = self._locales[lang].UNITS
        rounded_kb = max(1, round(size_bytes / BYTES_IN_KB))
        if rounded_kb < KB_DISPLAY_LIMIT:
            return f"{rounded_kb} {units['kb']}"
        return f"{self._megabytes(lang, size_bytes)} {units['mb']}"

    def _megabytes(self, lang: Lang, size_bytes: int) -> str:
        value = round(size_bytes / BYTES_IN_MB, DECIMALS_SHOWN)
        separator = self._locales[lang].DECIMAL_SEPARATOR
        return f"{value:.{DECIMALS_SHOWN}f}".replace(".", separator)

    def date(self, lang: Lang, day: date) -> str:
        return f"{day.day} {self._locales[lang].MONTHS[day.month - 1]} {day.year}"
