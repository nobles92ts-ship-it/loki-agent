"""Event dedup — each event id handled exactly once."""
from loki.core import dedup


def test_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "SEEN_FILE", tmp_path / "seen.json")
    assert dedup.already_seen("ev1") is False
    assert dedup.already_seen("ev1") is True
    assert dedup.already_seen("ev2") is False
    assert dedup.already_seen("") is False        # missing id → never dedup
