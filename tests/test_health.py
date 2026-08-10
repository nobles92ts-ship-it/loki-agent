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


# ── the beat only counts while we can still hear the platform ───────────────
def test_no_probe_means_beat(tmp_path, monkeypatch):
    """Adapters that never pass one (Discord) keep the old behaviour."""
    _setup(tmp_path, monkeypatch)
    assert health.reachable(None) is True


def test_connected_probe_allows_the_beat(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert health.reachable(lambda: True) is True


def test_detached_socket_withholds_the_beat(tmp_path, monkeypatch):
    """Regression (2026-08): a worker whose Socket Mode connection had dropped
    kept beating from its timer thread, so `alive` stayed True and the watchdog
    never restarted it — three days of silence that looked like health."""
    _setup(tmp_path, monkeypatch)
    assert health.reachable(lambda: False) is False


def test_a_broken_probe_does_not_retire_a_healthy_worker(tmp_path, monkeypatch):
    """Fail open: a probe that raises is our bug, not the worker's."""
    _setup(tmp_path, monkeypatch)

    def boom() -> bool:
        raise RuntimeError("probe exploded")

    assert health.reachable(boom) is True


def test_a_tick_stamps_while_attached_and_freezes_once_deaf(tmp_path,
                                                            monkeypatch):
    """The wiring, not just the helper: the timer's turn is `tick`, and it must
    consult the probe every time. Left unchecked this is the failure being
    fixed — the thread beats on regardless and the stamp never goes stale."""
    _stamp(monkeypatch, tmp_path, age=1)
    before = health.read()["last_beat"]

    health.tick(lambda: True)
    attached = health.read()["last_beat"]
    assert attached > before, "should stamp while the socket is up"

    health.tick(lambda: False)               # socket drops
    assert health.read()["last_beat"] == attached, "stamp must freeze once deaf"


def test_withheld_beats_go_stale_and_read_as_dead(tmp_path, monkeypatch):
    """The whole point: withholding the stamp routes a deaf worker into the
    existing stale_heartbeat path instead of inventing a new failure mode."""
    _stamp(monkeypatch, tmp_path, age=health.STALE_AFTER + 1)
    s = health.snapshot()
    assert s["alive"] is False and s["reason"] == "stale_heartbeat"


def test_a_read_that_lands_mid_write_retries(tmp_path, monkeypatch):
    """A reader arriving while the stamp is being rewritten gets a sharing
    violation on Windows. Answering None there reads as *never_started*, and
    that is the one answer that makes the watchdog spawn a second worker — so
    read retries before it gives up. (Measured: under a writer looping without
    pause, retrying cut failed reads from 646/1500 to 93/1500; at the real
    one-per-minute cadence the window is far smaller still.)"""
    _stamp(monkeypatch, tmp_path, age=1)
    real = type(health._FILE).read_text
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("WinError 5 — writer holds the file")
        return real(self, *a, **kw)

    monkeypatch.setattr(type(health._FILE), "read_text", flaky)
    assert health.read() is not None, "gave up while a writer held the file"
    assert calls["n"] == 2, "should have retried exactly once"


def test_a_missing_stamp_still_answers_immediately(tmp_path, monkeypatch):
    """No file means never started — that is an answer, not a transient."""
    _setup(tmp_path, monkeypatch)
    assert health.read() is None


# ── pid check ───────────────────────────────────────────────────────────────
def test_pid_running_on_self():
    assert health.pid_running(os.getpid()) is True


def test_pid_running_on_nonsense():
    assert health.pid_running(None) is False
    assert health.pid_running(0x7FFFFFFF) is False
    assert health.pid_running("not-a-pid") is False


def test_probing_a_process_does_not_kill_it():
    """Regression: the first cut used `os.kill(pid, 0)`, which on Windows is
    OpenProcess + TerminateProcess — asking whether the worker was alive would
    have killed it. Asking must stay read-only."""
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(30)"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        assert health.pid_running(child.pid) is True
        for _ in range(3):                       # probe repeatedly
            health.pid_running(child.pid)
        assert child.poll() is None, "the liveness probe killed the process"
    finally:
        child.kill()
        child.wait(timeout=5)
    assert health.pid_running(child.pid) is False   # and it notices real death
