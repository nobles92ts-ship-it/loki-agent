"""Guest rate limiting — rolling-hour cap, disable switch, per-user isolation."""
import time

from loki.core import config, ratelimit


def _setup(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(ratelimit, "RATE_FILE", tmp_path / "rl.json")
    monkeypatch.setattr(config, "GUEST_RATE_PER_HOUR", limit)


def test_disabled_when_zero(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 0)
    for _ in range(50):
        assert ratelimit.check("U1") == (True, 0)


def test_limit_enforced(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 3)
    assert [ratelimit.check("U1")[0] for _ in range(4)] == [True, True, True, False]
    blocked, retry = ratelimit.check("U1")
    assert blocked is False and 1 <= retry <= 60


def test_per_user_isolation(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 2)
    assert ratelimit.check("U1")[0] and ratelimit.check("U1")[0]
    assert ratelimit.check("U1")[0] is False
    assert ratelimit.check("U2")[0] is True          # independent budget


def test_window_slides(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 1)
    assert ratelimit.check("U1")[0] is True
    assert ratelimit.check("U1")[0] is False
    # age the recorded hit past the window → next call allowed again
    data = ratelimit._load()
    data["U1"] = [time.time() - ratelimit.WINDOW_SEC - 1]
    ratelimit._save(data)
    assert ratelimit.check("U1")[0] is True


def test_empty_user_allowed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 1)
    assert ratelimit.check("")[0] is True
