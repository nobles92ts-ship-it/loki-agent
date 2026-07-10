"""Scheduler — spec parsing, next-fire math, persistence, boot policy."""
import time

from loki.core import scheduler


def _ts(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_daily():
    s = scheduler.parse("daily 09:00 morning briefing")
    assert s == {"type": "daily", "time": "09:00", "prompt": "morning briefing"}


def test_parse_korean_aliases():
    assert scheduler.parse("매일 9:05 아침 브리핑")["time"] == "09:05"
    assert scheduler.parse("매주 목 10:00 주간보고")["dow"] == 3


def test_parse_weekly():
    s = scheduler.parse("weekly thu 10:00 weekly report")
    assert s["type"] == "weekly" and s["dow"] == 3 and s["time"] == "10:00"


def test_parse_once_and_invalid():
    s = scheduler.parse("once 2026-12-25 08:30 merry xmas")
    assert s["type"] == "once" and s["date"] == "2026-12-25"
    assert scheduler.parse("sometime later do stuff") is None
    assert scheduler.parse("daily 25:00 x") is None
    assert scheduler.parse("weekly noday 10:00 x") is None


# ── next-fire math ───────────────────────────────────────────────────────────
def test_compute_next_daily():
    after = _ts(2026, 7, 10, 12, 0)
    nxt = scheduler.compute_next({"type": "daily", "time": "09:00"}, after)
    assert time.localtime(nxt)[:5] == (2026, 7, 11, 9, 0)     # rolled to tomorrow
    nxt2 = scheduler.compute_next({"type": "daily", "time": "13:00"}, after)
    assert time.localtime(nxt2)[:5] == (2026, 7, 10, 13, 0)   # later today


def test_compute_next_weekly():
    after = _ts(2026, 7, 10, 12, 0)
    nxt = scheduler.compute_next({"type": "weekly", "dow": 3, "time": "10:00"},
                                 after)
    lt = time.localtime(nxt)
    assert lt.tm_wday == 3 and lt.tm_hour == 10
    assert after < nxt <= after + 8 * 86400


def test_compute_next_once_may_be_past():
    spec = {"type": "once", "date": "2020-01-01", "time": "00:00"}
    assert scheduler.compute_next(spec, time.time()) < time.time()


# ── persistence + boot policy ────────────────────────────────────────────────
def test_add_tick_rollforward_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHED_FILE", tmp_path / "sched.json")

    item = scheduler.add({"type": "daily", "time": "09:00", "prompt": "x"}, "C1")
    assert item["id"] == "s1" and item["next_fire"] > time.time()

    # force into the past → tick fires it once and recomputes the next slot
    items = scheduler.list_all()
    items[0]["next_fire"] = time.time() - 60
    scheduler._save(items)
    due = scheduler.tick(time.time())
    assert [d["id"] for d in due] == ["s1"]
    assert scheduler.list_all()[0]["next_fire"] > time.time()

    # boot policy: recurring rolls forward WITHOUT firing; once stays due
    o = scheduler.add({"type": "once", "date": "2020-01-01", "time": "00:00",
                       "prompt": "y"}, "C1")
    items = scheduler.list_all()
    for s in items:
        if s["type"] == "daily":
            s["next_fire"] = time.time() - 60
    scheduler.rollforward(items, time.time())
    for s in items:
        if s["type"] == "daily":
            assert s["next_fire"] > time.time()
        else:
            assert s["next_fire"] < time.time()
    scheduler._save(items)

    # tick fires the missed `once` and drops it from the store
    due = scheduler.tick(time.time())
    assert [d["id"] for d in due] == [o["id"]]
    assert all(s["id"] != o["id"] for s in scheduler.list_all())

    assert scheduler.remove("s1") is True
    assert scheduler.remove("s99") is False
