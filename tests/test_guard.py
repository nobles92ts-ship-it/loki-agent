"""Permission files are owner-DM-only — deny rules + tamper revert."""
import json
from pathlib import Path

from loki.core import config, guard


def _setup(tmp_path, monkeypatch):
    work = tmp_path / "work"
    orgs = work / "loki" / "orgs"
    orgs.mkdir(parents=True)
    (work / "loki" / "loki.md").write_text("## Allowed paths\n- X\n", encoding="utf-8")
    (orgs / "acme.md").write_text("## Members\n- U1\n", encoding="utf-8")
    base = tmp_path / "repo"
    base.mkdir()
    (base / ".env").write_text("ALLOWED_USER_ID=U0\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "BASE", base)
    monkeypatch.setattr(config, "STATE", state)
    return work, base


# ── layer 1: deny rules ──────────────────────────────────────────────────────
def test_deny_covers_permission_files_and_source(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    joined = "\n".join(guard.deny_patterns())
    assert "Write(" in joined and "Edit(" in joined
    assert "/loki/**" in joined                       # manifest + orgs
    assert "/repo/**" in joined                       # worker's own source


def test_deny_covers_credential_reads(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "CLAUDE_CONFIG_DIR", str(tmp_path / "loki-home"))
    joined = "\n".join(guard.deny_patterns())
    assert "Read(" in joined and "Grep(" in joined and "Glob(" in joined
    assert "/repo/.env" in joined                     # bot + integration tokens
    assert joined.count(".credentials.json") >= 2     # both Claude profiles
    assert "loki-home/.claude.json" in joined


def test_private_command_files_stay_readable(tmp_path, monkeypatch):
    """A blanket read-deny on the worker folder would break private commands
    that keep a procedure file inside it."""
    _, base = _setup(tmp_path, monkeypatch)
    joined = "\n".join(guard.deny_patterns())
    src = str(base).replace("\\", "/")
    assert f"Read({src}/**)" not in joined


def test_settings_file_is_valid_deny_json(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    data = json.loads(Path(guard.settings_file()).read_text(encoding="utf-8"))
    assert data["permissions"]["deny"] == guard.deny_patterns()


# ── layer 2: tripwire ────────────────────────────────────────────────────────
def test_clean_run_reverts_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert guard.restore(guard.snapshot()) == []


def test_edited_manifest_is_reverted(tmp_path, monkeypatch):
    work, _ = _setup(tmp_path, monkeypatch)
    manifest = work / "loki" / "loki.md"
    snap = guard.snapshot()
    manifest.write_text("## Allowed paths\n- C:\\\n", encoding="utf-8")   # payload
    assert guard.restore(snap) == ["loki.md"]
    assert manifest.read_text(encoding="utf-8") == "## Allowed paths\n- X\n"


def test_edited_env_is_reverted(tmp_path, monkeypatch):
    _, base = _setup(tmp_path, monkeypatch)
    snap = guard.snapshot()
    (base / ".env").write_text("ALLOWED_USER_ID=U_ATTACKER\n", encoding="utf-8")
    assert guard.restore(snap) == [".env"]
    assert "U0" in (base / ".env").read_text(encoding="utf-8")


def test_new_org_file_is_removed(tmp_path, monkeypatch):
    work, _ = _setup(tmp_path, monkeypatch)
    snap = guard.snapshot()
    evil = work / "loki" / "orgs" / "evil.md"
    evil.write_text("## Members\n- U_ATTACKER\n## Commands\n- bts\n", encoding="utf-8")
    assert guard.restore(snap) == ["evil.md"]
    assert not evil.exists()


def test_deleted_org_file_is_restored(tmp_path, monkeypatch):
    work, _ = _setup(tmp_path, monkeypatch)
    org = work / "loki" / "orgs" / "acme.md"
    snap = guard.snapshot()
    org.unlink()
    assert guard.restore(snap) == ["acme.md"]
    assert org.read_text(encoding="utf-8") == "## Members\n- U1\n"


def test_alert_names_the_reverted_files(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert "loki.md" in guard.alert_text(["loki.md"])
