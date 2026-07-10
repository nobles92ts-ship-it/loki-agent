"""Usage ledger — record + windowed aggregation."""
import json
import time

from loki.core import usage


def test_record_and_summarize(tmp_path, monkeypatch):
    f = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage, "USAGE_FILE", f)
    usage.record("owner", "U1", True, 10)
    usage.record("guest", "U2", False, 5, "timeout")
    old = {"ts": time.time() - 10 * 86400, "kind": "owner", "user": "U1",
           "ok": True, "dur": 3}
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(old) + "\n")

    d = usage.summarize(7)
    assert d["total"] == 2 and d["ok"] == 1 and d["fail"] == 1
    assert d["today"]["total"] == 2
    assert dict(d["by_kind"]) == {"owner": 1, "guest": 1}
    assert dict(d["by_user"]) == {"U1": 1, "U2": 1}
    assert usage.summarize(30)["total"] == 3


def test_empty_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "none.jsonl")
    d = usage.summarize(7)
    assert d["total"] == 0 and d["by_user"] == [] and d["today"]["total"] == 0
