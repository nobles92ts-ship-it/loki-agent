"""brain._parse — claude -p JSON output handling."""
import json

from loki.core import brain


def test_parse_ok():
    out = json.dumps({"result": "hi", "session_id": "s1", "is_error": False})
    r = brain._parse(out, "", 0, None)
    assert r == {"text": "hi", "session_id": "s1", "error": False,
                 "reason": "ok"}


def test_parse_quota_detection():
    out = json.dumps({"result": "You have hit your usage limit",
                      "session_id": "s1"})
    assert brain._parse(out, "", 0, None)["reason"] == "quota"


def test_parse_nonzero_exit():
    r = brain._parse("", "boom", 1, "keep")
    assert r["error"] and r["session_id"] == "keep" and "boom" in r["text"]


def test_parse_plain_text():
    r = brain._parse("plain text", "", 0, None)
    assert r["text"] == "plain text" and r["reason"] == "ok"
