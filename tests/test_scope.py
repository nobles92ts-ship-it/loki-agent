"""Guest allowlist — fail-closed by default, allowlisted paths opened."""
import json
from pathlib import Path

from loki.core import config, scope


def _setup(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "secret").mkdir(parents=True)
    (work / "shared").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "STATE", state)
    scope.ensure_manifest()
    return work


def test_fail_closed_empty_manifest(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    denies, _ = scope.guest_scope()
    for tool in ("Bash", "Skill", "Task"):
        assert tool in denies
    assert any("secret/**" in d for d in denies)
    assert any("shared/**" in d for d in denies)          # nothing shared yet
    assert not any("/loki/**" in d for d in denies)       # manifest folder visible


def test_allowlisted_folder_not_denied(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    scope.manifest_file().write_text(
        f"## Allowed paths\n- {work / 'shared'}\n", encoding="utf-8")
    denies, manifest = scope.guest_scope()
    assert not any("shared" in d for d in denies)
    assert any("secret/**" in d for d in denies)
    assert "shared" in manifest


def test_write_guest_settings(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    path, _ = scope.write_guest_settings()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["permissions"]["deny"]
