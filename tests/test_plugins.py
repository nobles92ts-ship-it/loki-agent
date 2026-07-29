"""Plugins — discovery, the permission gate, and blast-radius containment.

The gate matters most: a file appearing in a folder must never silently hand
guests a new way to reach the machine.
"""
import pytest

from loki.core import commands, config, orgs, plugins, scope


@pytest.fixture
def env(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    monkeypatch.setattr(config, "BASE", tmp_path)
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "STATE", state)
    monkeypatch.setattr(orgs, "_cache",
                        {"stamp": None, "orgs": {}, "member_index": {},
                         "channel_index": {}})
    scope.ensure_manifest()
    plugins.reload()
    yield pdir
    plugins.reload()


def write(pdir, name, body):
    (pdir / name).write_text(body, encoding="utf-8")
    plugins.reload()


OWNER = {"is_owner": True, "org": None}
GUEST = {"is_owner": False, "org": None}

PING = '''
MATCH = r"^!ping$"
HELP = "say pong"
def handle(match, ctx):
    return "pong"
'''


# ── discovery ───────────────────────────────────────────────────────────────
def test_no_plugins_dir_is_fine(env, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE", tmp_path / "nowhere")
    plugins.reload()
    assert plugins.load() == []
    assert plugins.handle("!ping", OWNER) is None


def test_plugin_is_discovered_and_runs(env):
    write(env, "ping.py", PING)
    assert plugins.handle("!ping", OWNER) == "pong"


def test_name_defaults_to_filename(env):
    write(env, "ping.py", PING)
    assert plugins.listing()[0][0] == "ping"


def test_explicit_name_wins(env):
    write(env, "ping.py", PING + '\nNAME = "pong-maker"\n')
    assert plugins.listing()[0][0] == "pong-maker"


def test_underscore_files_are_helpers_not_plugins(env):
    write(env, "_shared.py", PING)
    assert plugins.load() == []


def test_non_matching_text_falls_through(env):
    write(env, "ping.py", PING)
    assert plugins.handle("hello", OWNER) is None


def test_handler_may_decline_by_returning_none(env):
    write(env, "shy.py", 'MATCH = r"^!shy$"\ndef handle(m, c): return None\n')
    assert plugins.handle("!shy", OWNER) is None


# ── permission gate ─────────────────────────────────────────────────────────
def test_owner_only_is_the_default(env):
    write(env, "ping.py", PING)
    assert plugins.listing()[0][2] is True
    assert plugins.handle("!ping", GUEST) is None


def test_opening_a_plugin_still_needs_an_org_grant(env):
    """OWNER_ONLY = False alone must not open it to every guest."""
    write(env, "ping.py", PING + "\nOWNER_ONLY = False\n")
    assert plugins.handle("!ping", GUEST) is None


def test_granted_org_may_run_an_open_plugin(env, monkeypatch):
    write(env, "ping.py", PING + "\nOWNER_ONLY = False\n")
    monkeypatch.setattr(orgs, "allows_command",
                        lambda org, cmd: org == "acme" and cmd == "ping")
    assert plugins.handle("!ping", {"is_owner": False, "org": "acme"}) == "pong"
    assert plugins.handle("!ping", {"is_owner": False, "org": "other"}) is None


# ── containment ─────────────────────────────────────────────────────────────
def test_broken_plugin_is_skipped_not_fatal(env):
    write(env, "broken.py", "this is not python(((")
    write(env, "ping.py", PING)
    assert plugins.handle("!ping", OWNER) == "pong"      # the good one survives


def test_plugin_missing_its_contract_is_skipped(env):
    write(env, "nope.py", "MATCH = r'^!nope$'\n")        # no handle()
    assert plugins.load() == []


def test_raising_plugin_reports_instead_of_crashing(env):
    write(env, "boom.py",
          'MATCH = r"^!boom$"\ndef handle(m, c): raise ValueError("bang")\n')
    out = plugins.handle("!boom", OWNER)
    assert "boom" in out and "bang" in out


# ── integration with the built-in router ────────────────────────────────────
def test_builtins_win_over_plugins(env):
    """A plugin must not be able to shadow a core command."""
    write(env, "hijack.py",
          'MATCH = r"^!stop$"\ndef handle(m, c): return "HIJACKED"\n')
    ctx = dict(OWNER, channel="D012ABCDEF", thread=None, session_key=None,
               is_dm=True, name_of=str, user_ids=[],
               is_user_id=lambda t: False, is_channel_id=lambda t: False)
    assert commands.handle("!stop", ctx) != "HIJACKED"


def test_router_reaches_plugins_after_builtins(env):
    write(env, "ping.py", PING)
    ctx = dict(OWNER, channel="D012ABCDEF", thread=None, session_key=None,
               is_dm=True, name_of=str, user_ids=[],
               is_user_id=lambda t: False, is_channel_id=lambda t: False)
    assert commands.handle("!ping", ctx) == "pong"


def test_guest_still_blocked_from_builtins_via_router(env):
    ctx = dict(GUEST, channel="C012ABCDEF", thread=None, session_key=None,
               is_dm=False, name_of=str, user_ids=[],
               is_user_id=lambda t: False, is_channel_id=lambda t: False)
    assert commands.handle("!jobs", ctx) is None


def test_plugins_listing_command(env):
    write(env, "ping.py", PING)
    ctx = dict(OWNER, channel="D012ABCDEF", thread=None, session_key=None,
               is_dm=True, name_of=str, user_ids=[],
               is_user_id=lambda t: False, is_channel_id=lambda t: False)
    out = commands.handle("!plugins", ctx)
    assert "ping" in out and "say pong" in out


def test_plugins_listing_when_empty(env):
    ctx = dict(OWNER, channel="D012ABCDEF", thread=None, session_key=None,
               is_dm=True, name_of=str, user_ids=[],
               is_user_id=lambda t: False, is_channel_id=lambda t: False)
    assert "plugins" in commands.handle("!plugins", ctx).lower() or \
        "플러그인" in commands.handle("!plugins", ctx)
