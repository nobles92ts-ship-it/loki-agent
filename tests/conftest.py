"""Shared test setup.

Makes the repo root importable, and provides a Slack adapter loaded against a
stubbed SDK so dispatch and command behaviour can be tested without a
workspace, a token, or a network call.
"""
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OWNER = "UOWNER"
BOT = "UBOT"


class FakeClient:
    """Records Slack Web API calls instead of making them."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail: set[str] = set()           # method names that should raise
        self.fail_channels: set[str] = set()  # channels Slack should reject

    def __getattr__(self, name):
        def _call(**kw):
            self.calls.append((name, kw))
            if name in self.fail or kw.get("channel") in self.fail_channels:
                raise RuntimeError(f"{name} failed")
            return {
                "ok": True,
                "ts": "1700000000.000100",
                "user_id": BOT,
                "channel": {"id": "D0OWNER", "name": "general"},
                "user": {"profile": {"display_name": "tester"}},
                "messages": [],
            }
        return _call

    def of(self, name: str) -> list[dict]:
        """Every kwargs dict passed to `name`."""
        return [kw for n, kw in self.calls if n == name]

    def texts(self) -> list[str]:
        return [kw.get("text", "") for n, kw in self.calls
                if n == "chat_postMessage"]

    def reset(self) -> None:
        self.calls.clear()


class FakeApp:
    def __init__(self, *a, **kw):
        self.client = FakeClient()

    def _decorator(self, *a, **kw):
        return lambda fn: fn

    event = action = message = command = shortcut = view = _decorator


def _stub_slack_sdk() -> None:
    """Install a no-network slack_bolt so importing the adapter is safe."""
    if isinstance(sys.modules.get("slack_bolt"), types.ModuleType) and \
            getattr(sys.modules["slack_bolt"], "_loki_stub", False):
        return
    bolt = types.ModuleType("slack_bolt")
    bolt.App = FakeApp
    bolt._loki_stub = True

    adapter_pkg = types.ModuleType("slack_bolt.adapter")
    socket_mode = types.ModuleType("slack_bolt.adapter.socket_mode")

    class SocketModeHandler:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise AssertionError("tests must never start the socket handler")

    socket_mode.SocketModeHandler = SocketModeHandler
    adapter_pkg.socket_mode = socket_mode

    sys.modules["slack_bolt"] = bolt
    sys.modules["slack_bolt.adapter"] = adapter_pkg
    sys.modules["slack_bolt.adapter.socket_mode"] = socket_mode


@pytest.fixture(autouse=True)
def _isolate_provider(tmp_path, monkeypatch):
    """Every test starts on the default provider, and none can change it.

    Without this the suite reads `state/provider.json` — the choice the machine
    is actually running under. A `!provider gemini` left over from a live smoke
    then reroutes every spawn in tests that never mentioned a provider, and
    `test_account`'s assertions about `CLAUDE_CONFIG_DIR` fail somewhere far
    from the cause. Tests that care about the switch patch this again with
    their own file.

    `LOKI_OWNER_MODE` is the same kind of machine setting: left as the machine
    has it, every owner-DM test on an install with `restricted` would take the
    guarded route and write its settings file into the real `state/`.
    """
    from loki.core import providers
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(providers.config, "PROVIDER", "claude")
    monkeypatch.setattr(providers.config, "OWNER_MODE", "full")


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """No test may write the machine's real `state/` directory.

    Every module here binds its path at import time from `config.STATE`, so
    patching `config.STATE` afterwards does nothing — each one has to be
    redirected by name. Miss one and the suite quietly edits live state.

    That is not hypothetical. `!listen`'s new warning latches a flag the first
    time a channel message arrives; the adapter tests push channel messages
    through `on_message`, `autolisten` was not isolated, and the suite set
    `seen: true` on the running install — switching off the very warning it was
    testing. `state/provider.json` had bitten the same way a day earlier. Two
    instances of one class, so the fix is the whole list rather than a third
    one-off.

    Tests that care about a particular file patch it again with their own.
    """
    from loki.core import (account, autolisten, blocked, botallow, botmute,
                           budget, goals, health, learn, nudge, providers,
                           ratelimit, scheduler, selftest, sessions, usage)
    from loki.core.providers import groq

    # a name a test would not pick for itself — `tmp_path/"state"` collides
    # with the several that build their own state dir there
    state = tmp_path / "_isolated_state"
    state.mkdir(exist_ok=True)
    for mod, attr, name in (
            (account, "STATE_FILE", "account.json"),
            (autolisten, "_FILE", "autolisten.json"),
            (blocked, "_FILE", "blocked_channels.json"),
            (botallow, "ALLOW_FILE", "bots_allowed.json"),
            (botmute, "_FILE", "bot_muted.json"),
            (budget, "BUDGET_FILE", "budget.json"),
            (goals, "_FILE", "goals.json"),
            (health, "_FILE", "health.json"),
            (learn, "LEARN_FILE", "learnings.md"),
            (nudge, "_FILE", "nudges.json"),
            (providers, "STATE_FILE", "provider.json"),
            (ratelimit, "RATE_FILE", "ratelimit.json"),
            (scheduler, "SCHED_FILE", "schedules.json"),
            (selftest, "RESULT_FILE", "selftest.json"),
            (sessions, "_FILE", "sessions.json"),
            (usage, "USAGE_FILE", "usage.jsonl"),
            (groq, "_STORE", "groq")):
        monkeypatch.setattr(mod, attr, state / name)
    # autolisten caches its file in memory at import, so the redirect alone
    # would still let one test's zones leak into the next.
    monkeypatch.setattr(autolisten, "_state",
                        {"channels": set(), "threads": set(), "seen": False})


@pytest.fixture(autouse=True)
def _isolate_style_note(tmp_path, monkeypatch):
    """`config` loads the real `.env` at import, so WORK_DIR is the machine's
    own folder — and `<WORK_DIR>/loki/style.md` rides in front of every prompt.
    Without this, the owner's live note leaks into every prompt assertion.
    Tests that want a note point this at their own file."""
    from loki.core import prompt
    monkeypatch.setattr(prompt, "style_path", lambda: tmp_path / "_no_style.md")


@pytest.fixture(scope="session")
def slack_adapter(tmp_path_factory):
    """The imported Slack adapter, wired to a fake client (session-scoped:
    module import is global, so per-test state is reset by `adapter`)."""
    _stub_slack_sdk()
    work = tmp_path_factory.mktemp("work")
    env = {
        "SLACK_BOT_TOKEN": "xoxb-test",
        "SLACK_APP_TOKEN": "xapp-test",
        "ALLOWED_USER_ID": OWNER,
        "WORK_DIR": str(work),
        "SELFTEST_ON_BOOT": "0",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        from loki.platforms.slack import adapter as mod
        mod.BOT_USER_ID = BOT
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def adapter(slack_adapter, tmp_path, monkeypatch):
    """Per-test adapter: fresh WORK_DIR, empty client log, no real job queue.

    Persistent side channels (dedup, the rolling rate limiter) are neutralised
    so repeated runs stay deterministic — a suite that dispatches a handful of
    guests would otherwise trip the real hourly cap in `state/`.
    """
    from loki.core import config, dedup, ratelimit, sessions, usage

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    slack_adapter.app.client.reset()
    slack_adapter.app.client.fail = set()
    slack_adapter.app.client.fail_channels = set()

    submitted: list[dict] = []
    monkeypatch.setattr(slack_adapter.jobs, "submit",
                        lambda job: (submitted.append(job), ("j1", 0))[1])
    monkeypatch.setattr(dedup, "already_seen", lambda _id: False)
    monkeypatch.setattr(ratelimit, "check", lambda user, limit=None: (True, 0))
    monkeypatch.setattr(usage, "record", lambda *a, **kw: None)
    monkeypatch.setattr(sessions, "_FILE", tmp_path / "sessions.json")

    slack_adapter.work = work
    slack_adapter.submitted = submitted
    return slack_adapter


def event(text="hi", user=OWNER, channel="D0OWNER", ts="1.1", **extra) -> dict:
    """A minimal Slack message event."""
    ev = {"text": text, "user": user, "channel": channel, "ts": ts,
          "channel_type": "im" if channel.startswith("D") else "channel"}
    ev.update(extra)
    return ev
