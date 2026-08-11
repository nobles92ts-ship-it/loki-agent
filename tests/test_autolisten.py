"""Auto-listen zones — channel/thread registration, lookup, persistence."""
from loki.core import autolisten


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(autolisten, "_FILE", tmp_path / "al.json")
    monkeypatch.setattr(autolisten, "_state",
                        {"channels": set(), "threads": set(), "seen": False})


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


# ── the silent-void warning ──────────────────────────────────────────────────
# A zone is inert unless the Slack app subscribes to message.channels /
# message.groups. Nothing in Slack's bot-token API reports that, so the only
# honest signal is whether a channel message has ever arrived.
def test_a_fresh_install_has_not_seen_one(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert autolisten.channel_events_seen() is False


def test_the_first_channel_message_latches_it(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    autolisten.note_channel_event()
    assert autolisten.channel_events_seen() is True


def test_it_survives_a_restart(tmp_path, monkeypatch):
    """A quiet weekend must not undo it — otherwise the warning returns to an
    install where auto-listen demonstrably works."""
    _setup(tmp_path, monkeypatch)
    autolisten.note_channel_event()
    monkeypatch.setattr(autolisten, "_state", autolisten._load())
    assert autolisten.channel_events_seen() is True


def test_listen_warns_only_while_nothing_has_arrived(tmp_path, monkeypatch):
    from loki.core import commands
    _setup(tmp_path, monkeypatch)
    ctx = {"channel": "C1", "thread": None, "is_owner": True,
           "name_of": lambda u: u, "session_key": None, "is_dm": False,
           "user_ids": [], "is_user_id": lambda t: False,
           "is_channel_id": lambda t: False, "chan_ref": lambda c: c}

    warned = commands.handle("!listen", ctx)
    assert "message.channels" in warned          # registered into a void — say so

    autolisten.note_channel_event()
    autolisten.remove("C1", None)
    quiet = commands.handle("!listen", ctx)
    assert "message.channels" not in quiet       # proven to work — no nagging


def test_listening_carries_the_same_warning(tmp_path, monkeypatch):
    """`!listening` is where you go when a zone is not answering — the reason
    belongs there too, not only on the command that created it."""
    from loki.core import commands
    _setup(tmp_path, monkeypatch)
    autolisten.add("C1", None)
    assert "message.channels" in commands._listen_warning(commands.fmt_listening())
