"""Organizations — parsing, resolution ladder, isolation, CRUD, fail-closed."""
import json
from pathlib import Path

from loki.core import config, orgs, ratelimit, scope, usage


def _setup(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "acme-shared").mkdir(parents=True)
    (work / "beta-shared").mkdir()
    (work / "secret").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", str(work))
    monkeypatch.setattr(config, "STATE", state)
    monkeypatch.setattr(orgs, "_cache",
                        {"stamp": None, "orgs": {}, "member_index": {},
                         "channel_index": {}})
    scope.ensure_manifest()
    return work


def _write_org(name, body):
    orgs.orgs_dir().mkdir(parents=True, exist_ok=True)
    orgs.org_file(name).write_text(body, encoding="utf-8")


ACME = """# Org: acme

## Members
- U0AAAA1   <!-- alice -->

## Channels
- C0SHARED1

## Commands
- summary

## Settings
- rate: 2

## Allowed paths
- {work}/acme-shared
"""


def test_parse_all_fields(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    _write_org("acme", ACME.format(work=work))
    o = orgs.get("acme")
    assert o["members"] == ["U0AAAA1"]
    assert o["channels"] == ["C0SHARED1"]
    assert o["commands"] == ["summary"]
    assert o["rate"] == 2


def test_resolution_ladder(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    _write_org("acme", ACME.format(work=work))
    _write_org("beta", "## Members\n- U0BBBB1\n## Channels\n- C0BETA1\n")
    assert orgs.resolve("U0AAAA1", None) == "acme"          # explicit member
    assert orgs.resolve("U0ZZZZ9", "C0SHARED1") == "acme"   # channel binding
    assert orgs.resolve("U0AAAA1", "C0BETA1") == "acme"     # member beats channel
    assert orgs.resolve("U0ZZZZ9", "C0NOWHERE") is None     # unaffiliated


def test_overlap_first_alphabetical_wins(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_org("bravo", "## Members\n- U0DUP01\n")
    _write_org("alpha", "## Members\n- U0DUP01\n")
    assert orgs.resolve("U0DUP01", None) == "alpha"


def test_fail_closed_on_broken_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_org("weird", "no headings at all, just prose\n- U0AAAA1\n")
    o = orgs.get("weird")
    assert o["members"] == [] and o["commands"] == []       # nothing granted
    assert orgs.resolve("U0AAAA1", None) is None


def test_mtime_reload(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_org("acme", "## Members\n- U0AAAA1\n")
    assert orgs.resolve("U0AAAA1", None) == "acme"
    import os
    f = orgs.org_file("acme")
    f.write_text("## Members\n", encoding="utf-8")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))  # force new stamp
    assert orgs.resolve("U0AAAA1", None) is None


def test_allows_command_and_rate(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    _write_org("acme", ACME.format(work=work))
    assert orgs.allows_command("acme", "summary") is True
    assert orgs.allows_command("acme", "SUMMARY") is True   # case-insensitive
    assert orgs.allows_command("acme", "deploy") is False
    assert orgs.allows_command(None, "summary") is False
    assert orgs.rate("acme") == 2
    assert orgs.rate(None) is None
    assert orgs.rate("ghost") is None


def test_org_scope_isolation(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    _write_org("acme", ACME.format(work=work))
    _write_org("beta", f"## Allowed paths\n- {work}/beta-shared\n")
    denies, manifest = scope.org_scope("acme")
    assert not any("acme-shared" in d for d in denies)      # own folder open
    assert any("beta-shared/**" in d for d in denies)       # other org denied
    assert any("secret/**" in d for d in denies)
    assert any("loki/orgs/**" in d for d in denies)         # registry owner-only
    assert "acme-shared" in manifest
    # and the other direction
    denies_b, _ = scope.org_scope("beta")
    assert any("acme-shared/**" in d for d in denies_b)     # acme denied to beta
    assert not any("beta-shared" in d for d in denies_b)    # own folder open


def test_org_scope_fail_closed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_org("empty", "## Allowed paths\n")               # nothing listed
    denies, _ = scope.org_scope("empty")
    assert any("acme-shared/**" in d for d in denies)
    denies2, manifest2 = scope.org_scope("noexist")          # missing file
    assert manifest2 == "" and any("secret/**" in d for d in denies2)


def test_write_scope_settings_per_org(tmp_path, monkeypatch):
    work = _setup(tmp_path, monkeypatch)
    _write_org("acme", ACME.format(work=work))
    p_org, m_org = scope.write_scope_settings("acme")
    p_guest, _ = scope.write_scope_settings(None)
    assert p_org != p_guest and "acme" in p_org
    data = json.loads(Path(p_org).read_text(encoding="utf-8"))
    assert data["permissions"]["deny"]
    assert "acme-shared" in m_org
    # guests are denied everything incl. org folders
    guest_denies = json.loads(Path(p_guest).read_text(encoding="utf-8"))
    assert any("acme-shared/**" in d for d in guest_denies["permissions"]["deny"])


def test_ratelimit_org_override(tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit, "RATE_FILE", tmp_path / "rl.json")
    monkeypatch.setattr(config, "GUEST_RATE_PER_HOUR", 10)
    assert ratelimit.check("U1", limit=1) == (True, 0)
    assert ratelimit.check("U1", limit=1)[0] is False       # org cap of 1 wins
    assert ratelimit.check("U2")[0] is True                 # default path intact


def test_crud_roundtrip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert orgs.create("acme") == "created"
    assert orgs.create("acme") == "exists"
    assert orgs.create("bad name!") == "badname"
    assert orgs.add_member("acme", "U0NEW01", "새사람") is True
    assert orgs.add_member("acme", "U0NEW01") is False      # dedup
    assert orgs.resolve("U0NEW01", None) == "acme"
    assert orgs.bind("acme", "C0NEW01") is True
    assert orgs.resolve("U0ELSE9", "C0NEW01") == "acme"
    assert orgs.allow_command("acme", "Summary") is True    # lowercased on write
    assert orgs.allows_command("acme", "summary") is True
    assert orgs.remove_member("acme", "U0NEW01") is True
    assert orgs.resolve("U0NEW01", None) is None
    assert orgs.unbind("acme", "C0NEW01") is True
    assert orgs.resolve("U0ELSE9", "C0NEW01") is None
    assert orgs.deny_command("acme", "summary") is True
    assert orgs.allows_command("acme", "summary") is False


def test_crud_on_missing_org(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert orgs.add_member("ghost", "U0AAAA1") is False
    assert orgs.bind("ghost", "C0AAAA1") is False
    assert orgs.remove_member("ghost", "U0AAAA1") is False


def test_usage_by_org(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.jsonl")
    usage.record("guest", "U1", True, 1.0, org="acme")
    usage.record("guest", "U2", True, 1.0, org="acme")
    usage.record("guest", "U3", True, 1.0)                  # no org
    data = usage.summarize(7)
    assert dict(data["by_org"]) == {"acme": 2}
