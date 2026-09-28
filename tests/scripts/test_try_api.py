"""Скрипт сервера (только стандартная библиотека) повторяет постоянные бота — списки не должны разойтись."""
import importlib.util
from pathlib import Path

from bot.site_check.pagespeed import AUDIT_IDS, CATEGORIES, FIELDS

SCRIPT = Path(__file__).parents[2] / "scripts" / "try_api.py"


def load_try_api():
    spec = importlib.util.spec_from_file_location("try_api", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_try_api_asks_the_same_as_the_bot():
    try_api = load_try_api()
    assert (try_api.AUDIT_IDS, try_api.CATEGORIES, try_api.FIELDS) == (AUDIT_IDS, CATEGORIES, FIELDS)
