"""Alias names are checked against the live dispatch, not a hardcoded list.

`RESERVED` only ever listed the shipped commands, so a fork's own commands and
anything in `plugins/` — the supported extension point — were invisible to it.
`!alias add tc …` saved happily against a live `!tc` and then never fired, with
no error at definition time and no warning at call time. Reported as #4.
"""
import re

import pytest

from loki.core import alias, commands, registry


@pytest.fixture
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"
    (w / "loki").mkdir(parents=True)
    monkeypatch.setattr(alias.config, "WORK_DIR", str(w))
    alias._invalidate()
    return w


# ── the live probe ───────────────────────────────────────────────────────────
def test_builtin_names_are_seen():
    assert registry.shadowed_by("org") == "commands"
    assert registry.shadowed_by("budget") == "commands"
    assert registry.shadowed_by("block") == "commands"      # only matches with args
    assert registry.shadowed_by("계정") == "commands"        # Korean spelling
    assert registry.shadowed_by("account") == "commands"


def test_free_name_is_free():
    assert registry.shadowed_by("standup") is None
    assert registry.shadowed_by("주간보고초안") is None


def test_alias_own_matcher_is_not_a_source():
    """`alias.CALL_RE` matches `!<anything>`; probing it would take every name."""
    assert not any(label == "alias" for label, _ in registry._sources())


def test_a_new_command_needs_no_list_update(monkeypatch):
    """The whole point: adding a matcher is enough, nothing to remember."""
    assert registry.shadowed_by("deploy") is None
    monkeypatch.setattr(commands, "_TEST_ONLY_RE",
                        re.compile(r"^!(?:deploy|배포)\b"), raising=False)
    assert registry.shadowed_by("deploy") == "commands"
    assert registry.shadowed_by("배포") == "commands"


def test_a_real_plugin_on_disk_is_seen(tmp_path, monkeypatch):
    """`plugins/` is the supported extension point, so it is the case that has
    to work — and the one a sys.modules scan alone would miss, because the
    loader execs each file without registering the module.
    """
    from loki.core import plugins
    d = tmp_path / "plugins"
    d.mkdir()
    (d / "ship.py").write_text(
        'MATCH = r"^!(?:ship|출항)\\b"\n'
        'HELP = "!ship — send it"\n'
        'def handle(match, ctx):\n    return "shipped"\n', encoding="utf-8")
    monkeypatch.setattr(plugins, "plugins_dir", lambda: d)
    plugins.reload()
    try:
        assert registry.shadowed_by("ship") == "ship"
        assert registry.shadowed_by("출항") == "ship"
        assert alias.add("ship", "x") == "shadowed:ship"
    finally:
        monkeypatch.undo()
        plugins.reload()


def test_plugin_declared_name_is_taken(monkeypatch):
    """A plugin whose MATCH is shaped oddly is still grantable by NAME."""
    monkeypatch.setattr("loki.core.plugins.listing",
                        lambda: [("weirdplugin", "", True)])
    assert registry.shadowed_by("weirdplugin") == "plugins"


# ── what the owner sees ──────────────────────────────────────────────────────
def test_add_refuses_a_shadowed_name(work, monkeypatch):
    """The reported shape: a live command that RESERVED never heard of."""
    monkeypatch.setattr(commands, "_TEST_ONLY_RE",
                        re.compile(r"^!(?:deploy)\b"), raising=False)
    assert alias.add("deploy", "ship the thing") == "shadowed:commands"
    assert alias.get("deploy") is None               # and nothing was written


def test_add_message_names_the_culprit(work, monkeypatch):
    monkeypatch.setattr(commands, "_TEST_ONLY_RE",
                        re.compile(r"^!(?:deploy)\b"), raising=False)
    reply = commands.alias_cmd("add deploy ship it")
    assert "deploy" in reply and "commands" in reply
    assert "never fire" in reply or "안 뜬다" in reply


def test_reserved_still_answers_for_shipped_and_unshipped_names(work):
    """`help` has no matcher yet — only RESERVED can catch it. `stop` has one
    too, and either refusal is correct; what must never happen is acceptance."""
    assert registry.shadowed_by("help") is None
    assert alias.add("help", "x") == "reserved"
    assert alias.add("stop", "x") == "reserved"


def test_list_flags_an_alias_a_command_later_took(work, monkeypatch):
    """The drift case the definition-time check can't catch: the alias was legal
    when it was written, and a command claimed the name afterwards."""
    alias.aliases_file().write_text(
        "## Aliases\n- deploy: ship it\n- standup: summarize\n", encoding="utf-8")
    alias._invalidate()
    monkeypatch.setattr(commands, "_TEST_ONLY_RE",
                        re.compile(r"^!(?:deploy)\b"), raising=False)

    reply = commands.alias_cmd("list")
    dead = [ln for ln in reply.splitlines() if "deploy" in ln]
    assert any("⚠️" in ln for ln in dead), reply
    assert "standup" in reply
    assert not any("⚠️" in ln for ln in reply.splitlines() if "standup" in ln)


def test_a_broken_probe_does_not_block_the_owner(work, monkeypatch):
    """Fail-open on purpose: the probe is a guard rail, not a gate. A registry
    that raises must not make `!alias add` unusable."""
    monkeypatch.setattr(registry, "shadowed_by",
                        lambda name: (_ for _ in ()).throw(RuntimeError("boom")))
    assert alias.add("standup", "summarize commits") == "added"
