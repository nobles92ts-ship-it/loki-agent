"""One worker per platform — a second start finds the lock and steps aside.

Regression (2026-10-06): at logon the watchdog started a worker at 10:16 and
the Startup folder entry started another at 10:20. Both ran on one Slack app
token until it was noticed two days later, each getting a share of the events.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from loki.core import gateway, health, workerlock

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _release(monkeypatch):
    """claim() keeps its fd for the life of the process. Give each test its
    own table and let go afterwards, so no lock outlives its test."""
    held: dict = {}
    monkeypatch.setattr(workerlock, "_held", held)
    yield
    for fd in held.values():
        os.close(fd)


def _stamp(pid: int, age: float) -> None:
    """A heartbeat from `pid`, last written `age` seconds ago."""
    now = time.time()
    health._FILE.write_text(json.dumps({
        "pid": pid, "platform": "slack", "started": now - 600, "jobs": 0,
        "last_beat": now - age}), encoding="utf-8")


# ── the lock ────────────────────────────────────────────────────────────────
def test_a_second_worker_is_turned_away():
    assert workerlock.claim("slack") is True
    assert workerlock.claim("slack") is False
    assert workerlock.holder("slack") == os.getpid()


def test_the_pid_reads_like_any_file_while_held():
    """Windows locks are mandatory: with the locked byte at 64, `type` and
    `Get-Content` failed on a held lock, because their 4 KB read reached it."""
    workerlock.claim("slack")
    with open(workerlock._DIR / "worker_slack.lock", "rb") as f:
        assert f.read(4096) == str(os.getpid()).encode()


def test_one_worker_per_platform_not_per_machine():
    """A Slack worker and a Discord worker side by side is a supported setup."""
    assert workerlock.claim("slack") is True
    assert workerlock.claim("discord") is True


def test_no_lock_file_means_no_holder():
    assert workerlock.holder("slack") is None


def test_a_pid_left_in_a_free_lock_file_is_not_a_holder():
    """The file outlives its worker. After a reboot the pid in it can belong
    to anything, and here it is a live process (this one), so only the lock
    can say whether a worker is there. Asking must not keep the lock either."""
    (workerlock._DIR / "worker_slack.lock").write_text(str(os.getpid()))
    assert workerlock.holder("slack") is None
    assert workerlock.claim("slack") is True


def test_the_lock_goes_with_the_process_that_held_it():
    """The real case is two processes, and a crash must not leave a lock behind
    that turns every later start away."""
    code = ("import os, sys, time\n"
            "from pathlib import Path\n"
            "from loki.core import workerlock\n"
            "workerlock._DIR = Path(sys.argv[1])\n"
            "print(os.getpid() if workerlock.claim('slack') else 0, flush=True)\n"
            "time.sleep(60)\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(workerlock._DIR)],
                             cwd=ROOT, stdout=subprocess.PIPE, text=True)
    # On Windows a venv's python.exe is a launcher, so child.pid is not the
    # interpreter that holds the lock. The child reports its own pid.
    pid = int(child.stdout.readline() or 0)
    try:
        assert pid, "the child could not take a free lock"
        assert workerlock.claim("slack") is False
        assert workerlock.holder("slack") == pid
    finally:
        if pid:
            os.kill(pid, signal.SIGTERM)
        child.kill()
        child.wait(timeout=10)
        child.stdout.close()
    for _ in range(100):
        if workerlock.holder("slack") is None:
            break
        time.sleep(0.05)
    assert workerlock.claim("slack") is True


# ── the entrypoint ──────────────────────────────────────────────────────────
@pytest.fixture
def entry(slack_adapter, monkeypatch):
    """`_run_worker` with the adapter's run() recorded instead of connecting."""
    from loki import __main__ as main
    ran: list = []
    monkeypatch.setattr(slack_adapter, "run", lambda: ran.append(True))
    monkeypatch.setattr(main.config, "validate_core", lambda: None)
    return SimpleNamespace(run_worker=main._run_worker, ran=ran)


def test_a_second_start_exits_before_connecting(entry, capsys):
    assert workerlock.claim("slack")              # the worker already running
    assert entry.run_worker("slack") == 0
    assert entry.ran == [], "a second worker connected to the same app token"
    assert "already running" in capsys.readouterr().err


def test_the_first_start_takes_the_lock_and_runs(entry):
    """The control: the guard turns away only when a worker is already there."""
    assert entry.run_worker("slack") == 0
    assert entry.ran == [True]
    assert workerlock.holder("slack") == os.getpid()


# ── the watchdog ────────────────────────────────────────────────────────────
@pytest.fixture
def calls(monkeypatch):
    """What `ensure` did, in order, without stopping or starting anything."""
    seen: list = []
    monkeypatch.setattr(gateway, "stop", lambda: seen.append("stop") or 0)
    monkeypatch.setattr(gateway, "spawn", lambda: seen.append("spawn") or 1)
    monkeypatch.setattr(gateway, "time", SimpleNamespace(sleep=lambda s: None))
    return seen


def test_ensure_retires_a_deaf_worker_before_starting_another(calls):
    """A worker whose socket dropped still holds the lock, so a new one started
    beside it would just exit, and the deaf one would stay deaf. Before the
    lock it was worse: the new worker ran next to the old one, and the old
    one's socket could come back."""
    workerlock.claim("slack")                     # this process is the worker
    _stamp(os.getpid(), age=health.STALE_AFTER + 10)
    gateway.ensure()
    assert calls == ["stop", "spawn"]


def test_ensure_never_stops_a_pid_that_does_not_hold_the_lock(calls):
    """After a reboot the stamp's pid can be alive and belong to something
    else. Without the lock to vouch for it, ensure must not touch it."""
    _stamp(os.getpid(), age=health.STALE_AFTER + 10)
    gateway.ensure()
    assert calls == ["spawn"]


def test_ensure_leaves_a_healthy_worker_alone(calls):
    workerlock.claim("slack")
    _stamp(os.getpid(), age=1)
    gateway.ensure()
    assert calls == []
