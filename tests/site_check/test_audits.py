from bot.site_check import audits, findings, lighthouse, verdict
from bot.site_check.audits import as_number, audit_entry, audit_items, is_number, numeric_value


def test_audit_items_keeps_only_dict_items():
    raw = {"good": {"details": {"items": [{"url": "a"}, "junk", 3]}}, "flat": {"details": {"items": "nope"}},
           "broken": "junk"}
    assert audit_items(raw, "good") == [{"url": "a"}]
    assert audit_items(raw, "flat") == audit_items(raw, "broken") == audit_items(raw, "missing") == []


def test_numbers_are_read_strictly():
    raw = {"lcp": {"numericValue": 1400}, "text": {"numericValue": "1400"}, "flag": {"numericValue": True}}
    assert numeric_value(raw, "lcp") == 1400.0
    assert numeric_value(raw, "text") is None and numeric_value(raw, "flag") is None
    assert audit_entry(raw, "missing") == {}
    assert (is_number(True), as_number("7"), as_number(2)) == (False, 0.0, 2.0)


def test_moved_names_stay_where_old_imports_expect_them():
    assert verdict.Grade is findings.Grade and verdict.FindingItem is findings.FindingItem
    assert lighthouse.AuditState is audits.AuditState and lighthouse.audit_state is audits.audit_state
    assert lighthouse.strip_params is audits.strip_params and lighthouse.file_name is audits.file_name
