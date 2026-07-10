"""Job queue — bounded concurrency, per-conversation ordering, cancel by id.

N worker threads (JOB_CONCURRENCY) drain one queue. Jobs that belong to the
same conversation (thread) never overlap — a later message in the same thread
waits for the earlier one, so --resume session continuity stays intact.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from typing import Callable

from . import config
from .config import log

JOBS: "queue.Queue[dict]" = queue.Queue()

# conversation key (e.g. thread ts) -> claude session_id, for --resume continuity
sessions: dict[str, str] = {}
sess_lock = threading.Lock()

_reg_lock = threading.Lock()
_registry: dict[str, dict] = {}      # job id -> job dict (queued/running only)
_active_convs: set[str] = set()      # conversations with a job currently running
_ids = itertools.count(1)
_kill: Callable[[int], None] | None = None   # injected by start() (brain.tree_kill)


def submit(job: dict) -> tuple[str, int]:
    """Register + enqueue a job. Returns (job_id, queue size before enqueue)."""
    jid = f"j{next(_ids)}"
    job["id"] = jid
    job["status"] = "queued"
    job["enqueued"] = time.time()
    with _reg_lock:
        _registry[jid] = job
    pos = JOBS.qsize()
    JOBS.put(job)
    return jid, pos


def snapshot() -> list[dict]:
    """Queued + running jobs (metadata only), oldest first — for !jobs."""
    with _reg_lock:
        items = [j for j in _registry.values() if j.get("status") != "cancelled"]
        items.sort(key=lambda j: j.get("enqueued", 0))
        return [{k: j.get(k) for k in
                 ("id", "status", "kind", "user", "started", "text")}
                for j in items]


def cancel(job_id: str) -> str:
    """Cancel one job: 'dequeued' | 'killed' | 'starting' | 'not_found'."""
    with _reg_lock:
        job = _registry.get(job_id)
        if not job or job.get("status") == "cancelled":
            return "not_found"
        if job["status"] == "queued":
            job["status"] = "cancelled"      # worker will skip + drop it
            return "dequeued"
        proc = job.get("proc")               # running
        if proc:
            job["cancelled"] = True          # suppress retry/reply in the handler
    if proc and _kill:
        _kill(proc.pid)
        return "killed"
    return "starting"    # running but claude not spawned yet — retry in a moment


def cancel_all() -> int:
    """!stop — drop everything queued and kill everything running."""
    n = 0
    with _reg_lock:
        current = list(_registry.values())
    for job in current:
        with _reg_lock:
            if job.get("status") == "queued":
                job["status"] = "cancelled"
                n += 1
                continue
            proc = job.get("proc") if job.get("status") == "running" else None
            if proc:
                job["cancelled"] = True      # suppress retry/reply in the handler
        if proc and _kill:
            _kill(proc.pid)
            n += 1
    return n


def start(handler: Callable[[dict], None],
          on_error: Callable[[dict, Exception], None],
          kill: Callable[[int], None] | None = None,
          concurrency: int | None = None) -> None:
    """Start the worker threads."""
    global _kill
    _kill = kill
    workers = concurrency or config.JOB_CONCURRENCY
    q = JOBS   # bind now — workers stay on this queue for their lifetime

    def _loop() -> None:
        while True:
            job = q.get()
            try:
                conv = job.get("thread") or ""
                with _reg_lock:
                    if job.get("status") == "cancelled":
                        _registry.pop(job.get("id", ""), None)
                        continue
                    if conv and conv in _active_convs:
                        requeue = True           # same conversation still running
                    else:
                        requeue = False
                        if conv:
                            _active_convs.add(conv)
                        job["status"] = "running"
                        job["started"] = time.time()
                if requeue:
                    time.sleep(0.5)
                    q.put(job)
                    continue
                try:
                    handler(job)
                except Exception as e:            # noqa: BLE001 — report, keep serving
                    log.exception("job crashed")
                    try:
                        on_error(job, e)
                    except Exception:
                        log.exception("on_error failed")
                finally:
                    with _reg_lock:
                        _registry.pop(job.get("id", ""), None)
                        _active_convs.discard(conv)
            finally:
                q.task_done()

    for _ in range(max(1, workers)):
        threading.Thread(target=_loop, daemon=True).start()
