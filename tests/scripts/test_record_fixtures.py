"""Запись ответов хранит снимок первого экрана (ТЗ, 5.9) без его base64 и выкидывает миниатюры.

Решение владельца: реальную картинку в записи не держим — длинная base64-строка похожа на секрет
для сторожа (scripts/check_secrets.py) и gitleaks, а вместо неё в details.data — короткая заглушка.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "record_fixtures.py"


def load_record_fixtures():
    spec = importlib.util.spec_from_file_location("record_fixtures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recording_keeps_the_screenshot_audit_without_its_picture_and_drops_thumbnails():
    record = load_record_fixtures()
    payload = {"lighthouseResult": {"audits": {
        "final-screenshot": {"details": {"type": "screenshot", "data": "data:image/jpeg;base64,/9j/2Q=="}},
        "screenshot-thumbnails": {"details": {"items": [{"data": "data:image/jpeg;base64,/9j/2Q=="}]}}}}}
    prepared = record.mask_screenshot_data(record.keep_needed_audits(payload))
    audits = record.sanitize(prepared)["lighthouseResult"]["audits"]
    assert audits["final-screenshot"]["details"]["type"] == "screenshot"
    assert audits["final-screenshot"]["details"]["data"] == record.SCREENSHOT_PLACEHOLDER
    assert "screenshot-thumbnails" not in audits
