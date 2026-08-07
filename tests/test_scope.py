"""Guest allowlist — fail-closed by default, allowlisted paths opened."""
import json
import re
from pathlib import Path

from loki.core import config, scope

_READ_RE = re.compile(r"^Read\((.*)\)$")


def covered(denies: list[str], path) -> bool:
    """Would any deny pattern actually cover this path?

    Substring matching on the pattern list is not good enough now that the
    denies include real sibling folders — an unrelated machine's directory
    names would decide whether the assertion holds.
    """
    p = str(path).replace("\\", "/").lower()
    for d in denies:
        m = _READ_RE.match(d)
        if not m:
            continue
        base = m.group(1)
        base = (base[:-3] if base.endswith("/**") else base).rstrip("/").lower()
        if p == base or p.startswith(base + "/"):
            return True
    return False


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
    # The manifest folder is denied like everything else. It used to be exempt
    # so loki.md stayed readable, but the manifest reaches guests through the
    # prompt — and the worker's own tree can live inside that folder, which is
    # how its source, state/ and .env became readable on an empty allowlist.
    assert any("/loki/**" in d for d in denies)


def test_secret_files_denied_for_guests(tmp_path, monkeypatch):
    """The read-deny on .env and the credential files existed but was wired to
    the owner path only, so no folder exemption could ever be trusted."""
    _setup(tmp_path, monkeypatch)
    denies, _ = scope.guest_scope()
    assert any(d.startswith("Read(") and d.endswith(".env)") for d in denies)


def test_allowlisted_folder_not_denied(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    scope.manifest_file().write_text(
        f"## Allowed paths\n- {work / 'shared'}\n", encoding="utf-8")
    denies, manifest = scope.guest_scope()
    assert not covered(denies, work / "shared")
    assert covered(denies, work / "secret")
    assert "shared" in manifest


def test_workdir_drive_not_blanket_denied(tmp_path, monkeypatch):
    """A blanket root deny must never cover WORK_DIR itself.

    Deny beats allow, so `E:/**` cancelled every folder the manifest granted —
    a WORK_DIR on any drive but C: shared nothing, and the error said only
    "permission denied", which reads as a broken manifest. Reported as #2.
    """
    root = tmp_path / "drive"
    work = root / "work" / "01_x"
    (work / "shared").mkdir(parents=True)
    (root / "work" / "secrets").mkdir()          # sibling of WORK_DIR
    (root / "tools").mkdir()                     # sibling one level up
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "STATE", state)
    # Stand in for "C:/Users/**" or "E:/**" — a static root containing WORK_DIR.
    monkeypatch.setattr(
        scope, "STATIC_ROOTS", (str(root).replace("\\", "/") + "/**",))
    scope.ensure_manifest()
    scope.manifest_file().write_text(
        f"## Allowed paths\n- {work / 'shared'}\n", encoding="utf-8")

    denies, _ = scope.guest_scope()
    assert not covered(denies, work / "shared")        # the grant survives
    assert covered(denies, root / "tools")             # siblings stay shut
    assert covered(denies, root / "work" / "secrets")


def test_roots_without_workdir_stay_blanket_denied(tmp_path, monkeypatch):
    """Only the root that contains WORK_DIR loses its blanket rule."""
    _setup(tmp_path, monkeypatch)
    denies, _ = scope.guest_scope()
    assert covered(denies, "C:/Windows/System32")
    assert covered(denies, "D:/anything")


def test_workdir_at_a_drive_root_denies_nothing_of_its_own(tmp_path, monkeypatch):
    """`Path("D:/").resolve()` keeps the trailing slash, and the empty trailing
    part would match no sibling — denying every child of WORK_DIR itself."""
    work = tmp_path / "root"
    (work / "shared").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work) + "\\")
    monkeypatch.setattr(config, "STATE", state)
    monkeypatch.setattr(scope, "STATIC_ROOTS", ())
    scope.ensure_manifest()
    scope.manifest_file().write_text(
        f"## Allowed paths\n- {work / 'shared'}\n", encoding="utf-8")

    denies, _ = scope.guest_scope()
    assert not covered(denies, work / "shared")


def test_write_guest_settings(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    path, _ = scope.write_guest_settings()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["permissions"]["deny"]
