"""Alias dispatch — expansion, shadowing, and who is allowed to fire one."""
import pytest

from conftest import event


@pytest.fixture
def aliased(adapter):
    """Adapter with `!standup` and `!review` defined in the work dir."""
    (adapter.work / "loki").mkdir()
    adapter.alias.aliases_file().write_text(
        "## Aliases\n"
        "- standup: Summarize yesterday's commits\n"
        "- review: Review {args} and list risks\n",
        encoding="utf-8")
    adapter.alias._invalidate()
    return adapter


# ── firing ───────────────────────────────────────────────────────────────────
def test_alias_expands_into_the_prompt(aliased):
    aliased._dispatch({"event_id": "a1"}, event(text="!standup"),
                      is_mention=False)
    job = aliased.submitted[0]
    assert job["text"] == "Summarize yesterday's commits"
    assert job["kind"] == "alias"
    assert "standup" in job["reply_prefix"]


def test_alias_placeholder_receives_arguments(aliased):
    aliased._dispatch({"event_id": "a2"}, event(text="!review PR 12"),
                      is_mention=False)
    assert aliased.submitted[0]["text"] == "Review PR 12 and list risks"


def test_alias_without_placeholder_appends_arguments(aliased):
    aliased._dispatch({"event_id": "a3"}, event(text="!standup for the API team"),
                      is_mention=False)
    assert aliased.submitted[0]["text"].endswith("for the API team")


def test_unknown_bang_word_still_reaches_the_brain(aliased):
    aliased._dispatch({"event_id": "a4"}, event(text="!nosuchthing hello"),
                      is_mention=False)
    assert aliased.submitted[0]["text"] == "!nosuchthing hello"
    assert aliased.submitted[0]["kind"] == "owner"


def test_builtin_commands_are_never_shadowed(aliased):
    """An alias file claiming a built-in name must not intercept it."""
    aliased.alias.aliases_file().write_text(
        "## Aliases\n- jobs: pwn the owner\n", encoding="utf-8")
    aliased.alias._invalidate()
    aliased._dispatch({"event_id": "a5"}, event(text="!jobs"), is_mention=False)
    assert aliased.submitted == []                    # handled as the built-in
    assert "job" in aliased.app.client.texts()[0].lower()


# ── permissions ──────────────────────────────────────────────────────────────
def test_guest_cannot_fire_an_ungranted_alias(aliased, monkeypatch):
    monkeypatch.setattr(aliased.orgs, "resolve", lambda u, c: None)
    aliased._dispatch({"event_id": "a6"},
                      event(text="!standup", user="UGUEST", channel="C0PUB"),
                      is_mention=True)
    assert aliased.submitted == []                    # silent: never confirms it exists
    assert aliased.app.client.texts() == []


def test_org_member_fires_a_granted_alias_read_only(aliased, monkeypatch):
    monkeypatch.setattr(aliased.orgs, "resolve", lambda u, c: "acme")
    monkeypatch.setattr(aliased.orgs, "allows_command",
                        lambda org, cmd: (org, cmd) == ("acme", "standup"))
    aliased._dispatch({"event_id": "a7"},
                      event(text="!standup", user="UGUEST", channel="C0PUB"),
                      is_mention=True)
    job = aliased.submitted[0]
    assert job["text"] == "Summarize yesterday's commits"
    assert job["permission_mode"] == "plan"           # still read-only
    assert job["org"] == "acme"


def test_org_member_denied_a_different_alias(aliased, monkeypatch):
    monkeypatch.setattr(aliased.orgs, "resolve", lambda u, c: "acme")
    monkeypatch.setattr(aliased.orgs, "allows_command",
                        lambda org, cmd: cmd == "standup")
    aliased._dispatch({"event_id": "a8"},
                      event(text="!review x", user="UGUEST", channel="C0PUB"),
                      is_mention=True)
    assert aliased.submitted == []


def test_guest_alias_still_passes_the_rate_limit(aliased, monkeypatch):
    monkeypatch.setattr(aliased.orgs, "resolve", lambda u, c: "acme")
    monkeypatch.setattr(aliased.orgs, "allows_command", lambda o, c: True)
    monkeypatch.setattr(aliased.ratelimit, "check", lambda u, limit=None: (False, 7))
    aliased._dispatch({"event_id": "a9"},
                      event(text="!standup", user="UGUEST", channel="C0PUB"),
                      is_mention=True)
    assert aliased.submitted == []
    assert "7" in aliased.app.client.texts()[0]


# ── management command ───────────────────────────────────────────────────────
def test_alias_add_list_remove_round_trip(adapter):
    (adapter.work / "loki").mkdir()
    adapter._dispatch({"event_id": "m1"},
                      event(text="!alias add deploy ship it"), is_mention=False)
    assert adapter.alias.get("deploy") == "ship it"

    adapter._dispatch({"event_id": "m2"}, event(text="!alias list"),
                      is_mention=False)
    assert "deploy" in adapter.app.client.texts()[-1]

    adapter._dispatch({"event_id": "m3"}, event(text="!alias remove deploy"),
                      is_mention=False)
    assert adapter.alias.get("deploy") is None
    assert adapter.submitted == []


def test_alias_add_rejects_reserved_name(adapter):
    (adapter.work / "loki").mkdir()
    adapter._dispatch({"event_id": "m4"}, event(text="!alias add stop nope"),
                      is_mention=False)
    assert adapter.alias.get("stop") is None
    assert "stop" in adapter.app.client.texts()[0]


def test_bare_alias_shows_help(adapter):
    adapter._dispatch({"event_id": "m5"}, event(text="!alias"), is_mention=False)
    assert "!alias" in adapter.app.client.texts()[0]


def test_guest_cannot_manage_aliases(adapter):
    (adapter.work / "loki").mkdir()
    adapter._dispatch({"event_id": "m6"},
                      event(text="!alias add pwn do bad things", user="UGUEST",
                            channel="C0PUB"),
                      is_mention=True)
    assert adapter.alias.get("pwn") is None
    assert adapter.submitted[0]["permission_mode"] == "plan"


def test_korean_alias_command_and_name(adapter):
    (adapter.work / "loki").mkdir()
    adapter._dispatch({"event_id": "m7"},
                      event(text="!별칭 add 보고서 주간보고 초안 써줘"),
                      is_mention=False)
    assert adapter.alias.get("보고서") == "주간보고 초안 써줘"

    adapter._dispatch({"event_id": "m8"}, event(text="!보고서"), is_mention=False)
    assert adapter.submitted[0]["text"] == "주간보고 초안 써줘"


def test_core_router_is_fail_closed_without_owner_flag():
    """The router refuses on its own, rather than trusting each adapter."""
    from loki.core import commands
    assert commands.handle("!stop", {}) is None
    assert commands.handle("!budget off", {"is_owner": False}) is None
    assert commands.handle("!alias add x y", {"is_owner": False}) is None
