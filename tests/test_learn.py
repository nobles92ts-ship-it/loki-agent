"""!learn inbox — capture, header, multiline notes, item count."""
from loki.core import learn


def test_capture(tmp_path, monkeypatch):
    f = tmp_path / "learnings.md"
    monkeypatch.setattr(learn, "LEARN_FILE", f)
    assert learn.capture("first note") == 1
    assert learn.capture("multi\nline note") == 2
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# Loki learnings inbox")
    assert "first note" in text
    assert "\n  line note" in text     # continuation lines stay in the bullet
