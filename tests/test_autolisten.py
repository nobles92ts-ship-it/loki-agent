"""Auto-listen zones — channel/thread registration, lookup, persistence."""
from loki.core import autolisten


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(autolisten, "_FILE", tmp_path / "al.json")
    monkeypatch.setattr(autolisten, "_state",
                        {"channels": set(), "threads": set()})


def test_channel_zone_matches_any_thread(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert autolisten.add("C1", None) == "listen_channel"
    assert autolisten.is_zone("C1", None) is True
    assert autolisten.is_zone("C1", "123.456") is True     # whole channel → any thread
    assert autolisten.is_zone("C2", None) is False


def test_thread_zone_is_specific(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert autolisten.add("C1", "111.222") == "listen_thread"
    assert autolisten.is_zone("C1", "111.222") is True
    assert autolisten.is_zone("C1", "333.444") is False    # other thread not covered
    assert autolisten.is_zone("C1", None) is False         # channel top-level not covered


def test_already_registered(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert autolisten.add("C1", None) == "listen_channel"
    assert autolisten.add("C1", None) == "listen_already"
    assert autolisten.add("C1", "9.9") == "listen_already"  # redundant inside channel zone


def test_remove_thread_then_none(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    autolisten.add("C1", "111.222")
    assert autolisten.remove("C1", "111.222") == "unlisten_ok"
    assert autolisten.is_zone("C1", "111.222") is False
    assert autolisten.remove("C1", "111.222") == "unlisten_none"


def test_remove_falls_back_to_channel(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    autolisten.add("C1", None)
    # !unlisten typed inside a thread of a channel-zone → removes the channel zone
    assert autolisten.remove("C1", "111.222") == "unlisten_ok"
    assert autolisten.is_zone("C1", None) is False


def test_persistence_round_trip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    autolisten.add("C1", None)
    autolisten.add("C2", "5.5")
    reloaded = autolisten._load()
    assert reloaded["channels"] == {"C1"}
    assert reloaded["threads"] == {"C2:5.5"}


def test_snapshot_sorted(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    autolisten.add("Cb", None)
    autolisten.add("Ca", None)
    autolisten.add("C1", "9.9")
    chans, threads = autolisten.snapshot()
    assert chans == ["Ca", "Cb"]
    assert threads == ["C1:9.9"]
