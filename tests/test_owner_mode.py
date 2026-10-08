"""`LOKI_OWNER_MODE=restricted` — your own DM, off the machine.

2026-10-02: a Slack link pasted into the owner's DM sent Codex, which that DM
runs with every tool on, looking for a way to open it — a browser tab, the
Slack desktop app launched through the shell, then the computer-use plugin to
drive it, which types and clicks on the same input the owner is using. It
missed only because it called the plugin by the wrong names. The owner wanted
none of that: Slack in, an answer out, nothing on the machine operated.

`restricted` sends the DM down the route a shared channel already takes, so on
Codex with LOKI_RESTRICTED_MODE=sealed it runs with no tools at all. What it
must keep: a screenshot dropped in the DM (it rides on the prompt) and the
guests' shared folders (read through Loki).
"""
import pytest

from loki.core import allowread, config, guard, providers, sessions
from loki.core.providers import claude, codex

OK = {"text": "ok", "session_id": "s", "error": False, "reason": "ok"}


def _dm_job(**extra) -> dict:
    job = {"text": "what is this?", "user": "UOWNER", "id": "j1",
           "channel": "D0OWNER", "ts": "1.1", "kind": "owner",
           "event_id": "e1", "permission_mode": "bypassPermissions",
           "session_key": sessions.key_for("D0OWNER", None, True)}
    job.update(extra)
    return job


@pytest.fixture
def home(adapter, tmp_path, monkeypatch):
    """No real state/ or .env reached; one shared folder granted in loki.md."""
    monkeypatch.setattr(config, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(config, "BASE", tmp_path)
    shared = adapter.work / "Shared"
    shared.mkdir()
    (adapter.work / "loki").mkdir()
    (adapter.work / "loki" / "loki.md").write_text(f"- {shared}\n", encoding="utf-8")
    return adapter


def _capture(adapter, monkeypatch) -> dict:
    seen = {}

    def fake_run(p, resume_id, mode=None, **kw):
        seen.update(kw, mode=mode)
        return OK

    monkeypatch.setattr(adapter.brain, "run_claude", fake_run)
    return seen


# ── what the adapter hands the brain ─────────────────────────────────────────
def test_by_default_your_dm_is_unchanged(home, monkeypatch):
    seen = _capture(home, monkeypatch)
    home._handle(_dm_job())
    assert seen["settings_file"] is None and seen["read_roots"] is None
    assert seen["mode"] == "bypassPermissions"


def test_restricted_dm_takes_the_channel_route(home, monkeypatch):
    monkeypatch.setattr(config, "OWNER_MODE", "restricted")
    seen = _capture(home, monkeypatch)
    home._handle(_dm_job())
    assert seen["settings_file"]                         # the restricted route
    assert [p.name for p in seen["read_roots"]] == ["Shared"]


def test_restricted_dm_is_snapshot_guarded_like_a_channel(home, monkeypatch):
    monkeypatch.setattr(config, "OWNER_MODE", "restricted")
    _capture(home, monkeypatch)
    taken = []
    monkeypatch.setattr(guard, "snapshot", lambda: taken.append(1) or {})
    monkeypatch.setattr(guard, "restore", lambda snap: [])
    home._handle(_dm_job())
    assert taken


def test_a_channel_is_unchanged_either_way(home, monkeypatch):
    monkeypatch.setattr(config, "OWNER_MODE", "restricted")
    seen = _capture(home, monkeypatch)
    home._handle(_dm_job(channel="C0PUB", thread="1.1"))
    assert seen["settings_file"] and seen["read_roots"] is None


# ── the wire: on Codex it really is sealed ───────────────────────────────────
def test_restricted_dm_runs_sealed_with_its_screenshot(home, monkeypatch):
    monkeypatch.setattr(config, "OWNER_MODE", "restricted")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "sealed")
    providers.set_current("codex")
    monkeypatch.setattr(home, "_download_attachments",
                        lambda items: (["C:/inbox/shot.png"], []))
    monkeypatch.setattr(codex, "run", lambda *a, **k: pytest.fail("ran with tools"))
    monkeypatch.setattr(claude, "run", lambda *a, **k: pytest.fail("fell back"))
    called = {}

    def fake_allowread(mod, p, resume_id, roots, job=None, images=()):
        called.update(mod=mod.NAME, roots=[r.name for r in roots], images=images)
        return OK

    monkeypatch.setattr(allowread, "run", fake_allowread)
    home._handle(_dm_job(attachments=[{"url": "u", "name": "shot.png",
                                       "kind": "image"}]))
    assert called == {"mod": "codex", "roots": ["Shared"],
                      "images": ("C:/inbox/shot.png",)}


def test_no_shared_folders_still_seals_and_keeps_the_screenshot(home, monkeypatch):
    monkeypatch.setattr(config, "OWNER_MODE", "restricted")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "sealed")
    providers.set_current("codex")
    (home.work / "loki" / "loki.md").write_text("", encoding="utf-8")
    monkeypatch.setattr(home, "_download_attachments",
                        lambda items: (["C:/inbox/shot.png"], []))
    monkeypatch.setattr(codex, "run", lambda *a, **k: pytest.fail("ran with tools"))
    called = {}
    monkeypatch.setattr(codex, "run_sealed",
                        lambda p, r, cwd=None, job=None, images=():
                        called.update(images=images) or OK)
    home._handle(_dm_job())
    assert called == {"images": ("C:/inbox/shot.png",)}
