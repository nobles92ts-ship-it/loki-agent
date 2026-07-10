"""Job queue — concurrency, per-conversation ordering, cancel semantics."""
import queue
import threading
import time

import pytest

from loki.core import jobs


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(jobs, "JOBS", queue.Queue())
    jobs._registry.clear()
    jobs._active_convs.clear()
    yield


def _wait(cond, timeout=4.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_two_conversations_run_in_parallel(fresh):
    started, release = [], threading.Event()

    def handler(job):
        started.append(job["id"])
        release.wait(5)

    jobs.start(handler, lambda j, e: None, concurrency=2)
    jobs.submit({"thread": "t1", "text": "a"})
    jobs.submit({"thread": "t2", "text": "b"})
    assert _wait(lambda: len(started) == 2)
    release.set()


def test_same_conversation_is_serial(fresh):
    events = []

    def handler(job):
        events.append(("start", job["id"]))
        time.sleep(0.25)
        events.append(("end", job["id"]))

    jobs.start(handler, lambda j, e: None, concurrency=2)
    j1, _ = jobs.submit({"thread": "same", "text": "a"})
    j2, _ = jobs.submit({"thread": "same", "text": "b"})
    assert _wait(lambda: len(events) == 4, 6)
    first, second = (j1, j2) if events[0] == ("start", j1) else (j2, j1)
    assert events.index(("end", first)) < events.index(("start", second))


def test_cancel_queued_job(fresh):
    ran, block = [], threading.Event()

    def handler(job):
        ran.append(job["id"])
        block.wait(5)

    jobs.start(handler, lambda j, e: None, concurrency=1)
    j1, _ = jobs.submit({"thread": "t1", "text": "a"})
    assert _wait(lambda: ran == [j1])
    j2, _ = jobs.submit({"thread": "t2", "text": "b"})
    assert jobs.cancel(j2) == "dequeued"
    assert jobs.cancel("j9999") == "not_found"
    block.set()
    time.sleep(0.4)
    assert j2 not in ran


def test_snapshot_shows_running_and_queued(fresh):
    block = threading.Event()
    jobs.start(lambda job: block.wait(5), lambda j, e: None, concurrency=1)
    j1, _ = jobs.submit({"thread": "t1", "text": "a", "kind": "owner",
                         "user": "U1"})
    j2, _ = jobs.submit({"thread": "t2", "text": "b", "kind": "guest",
                         "user": "U2"})
    assert _wait(lambda: any(x["status"] == "running" for x in jobs.snapshot()))
    snap = jobs.snapshot()
    assert {x["id"] for x in snap} == {j1, j2}
    statuses = {x["id"]: x["status"] for x in snap}
    assert statuses[j1] == "running" and statuses[j2] == "queued"
    block.set()
