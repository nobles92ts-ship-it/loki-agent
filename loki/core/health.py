"""Liveness — is the worker actually up, and when did it last do anything.

The worker stamps ``state/health.json`` on a timer and whenever it finishes a
job. That file is all ``doctor``, ``status`` and the watchdog need: a stale
stamp means the process died without telling anyone, which is exactly the
failure that used to go unnoticed until someone messaged the bot and got
silence back.

Nothing here restarts anything — that belongs to the OS. See
``loki.core.gateway``.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import config
from .config import log

_FILE = config.STATE / "health.json"
BEAT_SEC = 60
# Three missed beats before we call it dead — one slow write or a paused VM
# shouldn't get the process killed and restarted underneath a running job.
STALE_AFTER = BEAT_SEC * 3

_lock = threading.Lock()
_state: dict = {}


def start(platform: str) -> None:
    """Begin stamping. Called once by an adapter's run()."""
    with _lock:
        _state.update(pid=os.getpid(), platform=platform,
                      started=time.time(), jobs=0)
    beat()

    def _loop() -> None:
        while True:
            time.sleep(BEAT_SEC)
            beat()

    threading.Thread(target=_loop, daemon=True).start()


def beat(job_done: bool = False) -> None:
    """Write the stamp. Cheap enough to call after every job."""
    with _lock:
        if not _state:
            return
        if job_done:
            _state["jobs"] = _state.get("jobs", 0) + 1
        _state["last_beat"] = time.time()
        payload = dict(_state)
    try:
        _FILE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        log.exception("health.json write failed")


def read() -> dict | None:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def pid_running(pid: int | None) -> bool:
    """Is that process id still alive? Portable enough for our purpose."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, just not ours to signal
    except Exception:
        return False
    return True


def snapshot() -> dict:
    """What doctor/status/watchdog all report from.

    ``alive`` needs both halves: a fresh stamp proves the loop is running, and
    a live pid proves the process is still there. A stamp alone can be left
    behind by a process that was killed a second after writing it.
    """
    data = read()
    if not data:
        return {"known": False, "alive": False, "reason": "never_started"}
    age = time.time() - data.get("last_beat", 0)
    running = pid_running(data.get("pid"))
    fresh = age <= STALE_AFTER
    return {
        "known": True,
        "alive": running and fresh,
        "reason": ("ok" if running and fresh
                   else "process_gone" if not running else "stale_heartbeat"),
        "pid": data.get("pid"),
        "platform": data.get("platform"),
        "uptime_sec": max(0.0, time.time() - data.get("started", 0)),
        "idle_sec": max(0.0, age),
        "jobs": data.get("jobs", 0),
    }


def clear() -> None:
    """Forget the stamp — used by `gateway stop` so a stopped worker doesn't
    look merely stale to the watchdog."""
    try:
        _FILE.unlink(missing_ok=True)
    except Exception:
        log.exception("health.json clear failed")
