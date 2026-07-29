"""Liveness — heartbeat freshness, pid liveness, and the two together."""
import os
import time

from loki.core import health


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_FILE", tmp_path / "health.json")
    monkeypatch.setattr(health, "_state", {})


def _stamp(monkeypatch, tmp_path, pid=None, age=0.0, jobs=0):
    """Write a heartbeat as if the worker had beaten `age` seconds ago."""
    _setup(tmp_path, monkeypatch)
    health._state.update(pid=pid if pid is not None else os.getpid(),
                         platform="slack", started=time.time() - 600, jobs=jobs)
    health.beat()
    health._state["last_beat"] = time.time() - age
    health._FILE.write_text(
        __import__("json").dumps(health._state), encoding="utf-8")


# ── no stamp at all ─────────────────────────────────────────────────────────
def test_never_started(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    s = health.snapshot()
    assert s == {"known": False, "alive": False, "reason": "never_started"}


def test_unreadable_stamp_is_not_alive(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "health.json").write_text("{not json", encoding="utf-8")
    assert health.snapshot()["alive"] is False


# ── the healthy case ────────────────────────────────────────────────────────
def test_fresh_stamp_from_a_live_pid_is_alive(tmp_path, monkeypatch):
    _stamp(monkeypatch, tmp_path, age=5)
    s = health.snapshot()
    assert s["alive"] is True and s["reason"] == "ok"
    assert s["platform"] == "slack"


def test_reports_jobs_and_uptime(tmp_path, monkeypatch):
    _stamp(monkeypatch, tmp_path, age=1, jobs=7)
    s = health.snapshot()
    assert s["jobs"] == 7
    assert s["uptime_sec"] >= 500


# ── the two failure modes ───────────────────────────────────────────────────
def test_stale_heartbeat_is_dead(tmp_path, monkeypatch):
    """The process is alive but the loop stopped beating — hung, not healthy."""
    _stamp(monkeypatch, tmp_path, age=health.STALE_AFTER + 10)
    s = health.snapshot()
    assert s["alive"] is False and s["reason"] == "stale_heartbeat"


def test_dead_process_is_dead_even_with_a_fresh_stamp(tmp_path, monkeypatch):
    """A stamp can outlive the process that wrote it by a second."""
    _stamp(monkeypatch, tmp_path, pid=0x7FFFFFFF, age=1)
    s = health.snapshot()
    assert s["alive"] is False and s["reason"] == "process_gone"


def test_freshness_boundary(tmp_path, monkeypatch):
    _stamp(monkeypatch, tmp_path, age=health.STALE_AFTER - 5)
    assert health.snapshot()["alive"] is True


# ── beat / clear ────────────────────────────────────────────────────────────
def test_beat_counts_finished_jobs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    health.start("discord")
    health.beat(job_done=True)
    health.beat(job_done=True)
    assert health.read()["jobs"] == 2


def test_beat_before_start_is_a_noop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    health.beat()
    assert health.read() is None


def test_clear_makes_it_unknown(tmp_path, monkeypatch):
    _stamp(monkeypatch, tmp_path, age=1)
    health.clear()
    assert health.snapshot()["known"] is False


# ── pid check ───────────────────────────────────────────────────────────────
def test_pid_running_on_self():
    assert health.pid_running(os.getpid()) is True


def test_pid_running_on_nonsense():
    assert health.pid_running(None) is False
    assert health.pid_running(0x7FFFFFFF) is False
