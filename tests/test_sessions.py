"""Conversation sessions — key policy, round trip, idle expiry, reset, persistence."""
import json
import time

from loki.core import config, sessions


def _setup(tmp_path, monkeypatch, idle_min=120):
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(sessions, "_state", {})
    monkeypatch.setattr(config, "SESSION_IDLE_MIN", idle_min)


def _age(key, seconds):
    """Backdate a stored session so expiry can be tested without sleeping."""
    sessions._state[key]["ts"] = time.time() - seconds


# ── key policy ──────────────────────────────────────────────────────────────
def test_thread_gets_its_own_key():
    assert sessions.key_for("C1", "111.22", is_dm=False) == "thread:C1:111.22"
    # a thread inside a DM is still a thread, not the rolling DM conversation
    assert sessions.key_for("D1", "111.22", is_dm=True) == "thread:D1:111.22"


def test_dm_top_level_is_one_rolling_conversation():
    assert sessions.key_for("D1", None, is_dm=True) == "dm:D1"


def test_channel_top_level_has_no_session():
    """Several people share a channel — no session, so no context bleed."""
    assert sessions.key_for("C1", None, is_dm=False) is None


def test_missing_channel_has_no_session():
    assert sessions.key_for("", None, is_dm=True) is None


# ── round trip ──────────────────────────────────────────────────────────────
def test_unknown_key_is_fresh(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert sessions.get("dm:D1") is None


def test_remember_then_get(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember("dm:D1", "sess-abc")
    assert sessions.get("dm:D1") == "sess-abc"


def test_conversations_are_isolated(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember("dm:D1", "sess-a")
    sessions.remember("thread:C1:9.9", "sess-b")
    assert sessions.get("dm:D1") == "sess-a"
    assert sessions.get("thread:C1:9.9") == "sess-b"


def test_none_key_or_id_is_a_noop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember(None, "sess-abc")
    sessions.remember("dm:D1", None)
    assert sessions.get(None) is None
    assert sessions.get("dm:D1") is None
    assert sessions._state == {}


# ── idle expiry ─────────────────────────────────────────────────────────────
def test_idle_session_expires_and_is_dropped(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, idle_min=120)
    sessions.remember("dm:D1", "sess-abc")
    _age("dm:D1", 121 * 60)
    assert sessions.get("dm:D1") is None
    assert "dm:D1" not in sessions._state      # dropped, not just hidden


def test_active_session_survives(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, idle_min=120)
    sessions.remember("dm:D1", "sess-abc")
    _age("dm:D1", 119 * 60)
    assert sessions.get("dm:D1") == "sess-abc"


def test_each_turn_refreshes_the_clock(tmp_path, monkeypatch):
    """A conversation in continuous use never ages out mid-chat."""
    _setup(tmp_path, monkeypatch, idle_min=120)
    sessions.remember("dm:D1", "sess-abc")
    _age("dm:D1", 119 * 60)
    sessions.remember("dm:D1", "sess-abc")     # next turn
    _age("dm:D1", 60 * 60)
    assert sessions.get("dm:D1") == "sess-abc"


def test_zero_disables_expiry(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, idle_min=0)
    sessions.remember("dm:D1", "sess-abc")
    _age("dm:D1", 365 * 86400)
    assert sessions.get("dm:D1") == "sess-abc"


def test_writes_prune_other_expired_entries(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, idle_min=120)
    sessions.remember("dm:D1", "old")
    _age("dm:D1", 200 * 60)
    sessions.remember("dm:D2", "new")          # unrelated conversation
    assert "dm:D1" not in sessions._state      # swept without ever being read


def test_active_counts_only_live_sessions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, idle_min=120)
    sessions.remember("dm:D1", "a")
    sessions.remember("dm:D2", "b")
    _age("dm:D1", 200 * 60)
    assert sessions.active() == 1


# ── reset (!new) ────────────────────────────────────────────────────────────
def test_reset_forgets_the_conversation(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember("dm:D1", "sess-abc")
    assert sessions.reset("dm:D1") is True
    assert sessions.get("dm:D1") is None


def test_reset_reports_nothing_to_drop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert sessions.reset("dm:D1") is False
    assert sessions.reset(None) is False


def test_reset_leaves_other_conversations_alone(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember("dm:D1", "a")
    sessions.remember("thread:C1:9.9", "b")
    sessions.reset("dm:D1")
    assert sessions.get("thread:C1:9.9") == "b"


# ── persistence ─────────────────────────────────────────────────────────────
def test_survives_a_restart(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember("dm:D1", "sess-abc")
    monkeypatch.setattr(sessions, "_state", sessions._load())   # "restart"
    assert sessions.get("dm:D1") == "sess-abc"


def test_reset_persists(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sessions.remember("dm:D1", "sess-abc")
    sessions.reset("dm:D1")
    monkeypatch.setattr(sessions, "_state", sessions._load())
    assert sessions.get("dm:D1") is None


def test_corrupt_state_file_starts_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "sessions.json").write_text("not json{", encoding="utf-8")
    assert sessions._load() == {}


def test_malformed_entries_are_ignored(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "sessions.json").write_text(
        json.dumps({"ok": {"sid": "s", "ts": time.time()},
                    "no_sid": {"ts": time.time()},
                    "not_a_dict": "sess-x"}), encoding="utf-8")
    assert list(sessions._load()) == ["ok"]
