"""Liveness — is the worker actually up, and when did it last do anything.

The worker stamps ``state/health.json`` on a timer and whenever it finishes a
job. That file is all ``doctor``, ``status`` and the watchdog need: a stale
stamp means the process died without telling anyone, which is exactly the
failure that used to go unnoticed until someone messaged the bot and got
silence back.

The stamp is withheld while the platform connection is down (see the
``connected`` probe on :func:`start`), so "alive" means *reachable*, not merely
*running*. A process with a live thread and a dead socket is not healthy — it
is the quietest way this thing fails.

Nothing here restarts anything — that belongs to the OS. See
``loki.core.gateway``.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable

from . import config
from .config import log

_FILE = config.STATE / "health.json"
BEAT_SEC = 60
# Three missed beats before we call it dead — one slow write or a paused VM
# shouldn't get the process killed and restarted underneath a running job.
STALE_AFTER = BEAT_SEC * 3

_lock = threading.Lock()
_state: dict = {}


def reachable(probe: "Callable[[], bool] | None") -> bool:
    """Is the event stream still attached? No probe means "assume yes"."""
    if probe is None:
        return True
    try:
        ok = bool(probe())
    except Exception:
        # A broken probe must not retire a worker that is doing fine.
        log.exception("connectivity probe failed — beating anyway")
        return True
    if not ok:
        log.warning("event stream detached — withholding heartbeat")
    return ok


def start(platform: str,
          connected: "Callable[[], bool] | None" = None) -> None:
    """Begin stamping. Called once by an adapter's run().

    ``connected`` answers *can we still hear the platform?* — the timer skips
    the stamp while it says no, so a detached worker goes stale on its own and
    the watchdog restarts it through the existing ``stale_heartbeat`` path.

    Without it the beat only ever proved this process still had a thread left.
    That is how a dropped Socket Mode connection stayed ``reason=ok`` for three
    days in August 2026: the process was up, the loop was beating, and nothing
    was listening.
    """
    with _lock:
        _state.update(pid=os.getpid(), platform=platform,
                      started=time.time(), jobs=0)
    beat()

    def _loop() -> None:
        while True:
            time.sleep(BEAT_SEC)
            tick(connected)

    threading.Thread(target=_loop, daemon=True).start()


def tick(connected: "Callable[[], bool] | None" = None) -> None:
    """One turn of the heartbeat: stamp only while we can still hear them."""
    if reachable(connected):
        beat()


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
    """The stamp, or None if there isn't one.

    Retries briefly on anything else: on Windows a reader that arrives during
    the writer's ``os.replace`` gets a sharing violation, and answering None
    there would read as *never_started* — the one answer that makes the
    watchdog start a second worker.
    """
    for attempt in range(3):
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            if attempt == 2:
                return None
            time.sleep(0.02)
    return None


def pid_running(pid: int | None) -> bool:
    """Is that process id still alive?

    ⚠ Not ``os.kill(pid, 0)``. That is the POSIX idiom, but on Windows CPython
    implements os.kill as ``OpenProcess`` + ``TerminateProcess`` — signal 0 and
    all — so the liveness probe would *kill the worker it was asking about*.
    Windows gets a read-only query instead.
    """
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False

    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False                     # gone, or not ours to look at
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
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
