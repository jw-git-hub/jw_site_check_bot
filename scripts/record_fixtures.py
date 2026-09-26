#!/usr/bin/env python3
"""Запись ответов PageSpeed для тестов (ТЗ, Сек13). Запуск на сервере, из папки бота:

  python3 scripts/record_fixtures.py <папка вне рабочей копии> <имя>=<адрес> [<имя>=<адрес> …]

Из всех адресов вырезает параметры и фрагменты — там бывают чужие ключи и подписанные ссылки.
Всё, похожее на секрет, заменяет на ***; скриншоты удаляет. Из audits оставляет только проверки бота:
остальные Google всё равно присылает (fields их не отсекает), а в тестах они не нужны.
На Мак файлы переносятся через scp.
"""
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from try_api import AUDIT_IDS, pagespeed, read_env  # noqa: E402 — путь к соседнему скрипту задан строкой выше

PATTERNS_FILE = SCRIPTS.parent / "bot" / "core" / "secret_patterns.py"
DROPPED_KEYS = {"screenshot", "final-screenshot", "screenshot-thumbnails", "fullPageScreenshot", "data"}
URL_PREFIXES = ("http://", "https://")
MASK = "***"


def load_patterns() -> list:
    spec = importlib.util.spec_from_file_location("secret_patterns", PATTERNS_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.SECRET_PATTERNS.values())


PATTERNS = load_patterns()


def clean_text(text: str) -> str:
    if text.startswith(URL_PREFIXES):
        parts = urllib.parse.urlsplit(text)
        text = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    for pattern in PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if key not in DROPPED_KEYS}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return clean_text(value) if isinstance(value, str) else value


def keep_needed_audits(payload: dict) -> dict:
    audits = (payload.get("lighthouseResult") or {}).get("audits")
    if isinstance(audits, dict):
        payload["lighthouseResult"]["audits"] = {name: audits[name] for name in AUDIT_IDS if name in audits}
    return payload


def record(out_dir: Path, name: str, url: str, key: str) -> None:
    status, size, payload = pagespeed(url, key, with_fields=True)
    target = out_dir / f"{name}.json"
    recorded = {"http_status": status, "response": sanitize(keep_needed_audits(payload))}
    target.write_text(json.dumps(recorded, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: HTTP {status}, {size} байт → {target}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out_dir = Path(sys.argv[1]).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    key = read_env()["PAGESPEED_API_KEY"]
    for pair in sys.argv[2:]:
        name, _, url = pair.partition("=")
        record(out_dir, name, url, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
