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
