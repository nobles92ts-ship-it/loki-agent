"""Discord adapter — surface classification, session keys, chunking, id shapes.

The adapter reads its token at import, so the environment is primed before the
import below. Channel objects are built with ``object.__new__`` because only
their type and id matter here — no gateway, no network.
"""
import os

import pytest

discord = pytest.importorskip("discord")

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-not-real")
os.environ.setdefault("DISCORD_OWNER_ID", "111111111111111111")

from loki.core import config, sessions                       # noqa: E402
from loki.platforms.discord import adapter                   # noqa: E402


def _channel(cls, cid):
    """A channel of the right type with an id — enough for classification."""
    ch = object.__new__(cls)
    ch.id = cid
    return ch


DM = _channel(discord.DMChannel, 900000000000000001)
THREAD = _channel(discord.Thread, 900000000000000002)
TEXT = _channel(discord.TextChannel, 900000000000000003)


# ── surface classification ──────────────────────────────────────────────────
def test_dm_is_a_dm():
    assert adapter._is_dm(DM) is True
    assert adapter._is_dm(TEXT) is False
    assert adapter._is_dm(THREAD) is False


def test_thread_is_a_thread():
    assert adapter._is_thread(THREAD) is True
    assert adapter._is_thread(TEXT) is False


# ── session keys ────────────────────────────────────────────────────────────
def test_dm_gets_a_rolling_session():
    assert adapter._session_key(DM) == f"dm:{DM.id}"


def test_thread_gets_its_own_session():
    assert adapter._session_key(THREAD) == f"thread:{THREAD.id}:{THREAD.id}"


def test_guild_channel_gets_no_session():
    """Several people share a channel — no session, so no context bleed."""
    assert adapter._session_key(TEXT) is None


def test_session_keys_never_collide_across_surfaces():
    keys = {adapter._session_key(DM), adapter._session_key(THREAD)}
    assert len(keys) == 2


# ── mention stripping ───────────────────────────────────────────────────────
def test_strips_every_mention_form():
    for raw in ("<@111111111111111111> hi", "<@!111111111111111111> hi",
                "<@&222222222222222222> hi"):
        assert adapter._strip_mention(raw) == "hi"


def test_leaves_plain_text_alone():
    assert adapter._strip_mention("deploy the thing") == "deploy the thing"
    assert adapter._strip_mention("") == ""


def test_keeps_channel_references():
    """`<#id>` is a channel reference, not a mention of the bot."""
    assert adapter._strip_mention("check <#123> please") == "check <#123> please"


# ── chunking (Discord's 2000-char ceiling) ──────────────────────────────────
def test_short_text_is_one_chunk():
    assert list(adapter._chunks("hello")) == ["hello"]


def test_empty_text_falls_back_to_a_placeholder():
    assert list(adapter._chunks("")) == [config.t("empty")]


def test_long_text_stays_under_the_limit():
    chunks = list(adapter._chunks("x" * 5000))
    assert chunks and all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == "x" * 5000


def test_splits_on_a_newline_when_there_is_one():
    body = ("a" * 1500) + "\n" + ("b" * 1000)
    first = next(iter(adapter._chunks(body)))
    assert first == "a" * 1500                # cut at the newline, not mid-word


def test_split_does_not_lose_content():
    body = "\n".join(f"line {i}" for i in range(600))
    assert "".join(adapter._chunks(body)) == body


# ── id shapes handed to the shared command router ───────────────────────────
def test_snowflakes_are_recognised_as_ids():
    assert adapter._SNOWFLAKE_RE.fullmatch("123456789012345678")
    assert adapter._SNOWFLAKE_RE.fullmatch("U012ABCDEF") is None
    assert adapter._SNOWFLAKE_RE.fullmatch("acme") is None


def test_user_mentions_are_extracted_as_bare_ids():
    found = adapter._USER_MENTION_RE.findall(
        "<@111111111111111111> and <@!222222222222222222>")
    assert found == ["111111111111111111", "222222222222222222"]


def test_discord_ids_survive_the_core_org_validator():
    """core.orgs must not assume Slack id shapes (it used to)."""
    from loki.core import orgs
    assert orgs._ID_RE.match("123456789012345678")


# ── async bridge ────────────────────────────────────────────────────────────
def test_call_before_the_loop_is_up_returns_none(monkeypatch):
    """Anything that posts before on_ready degrades quietly instead of raising."""
    monkeypatch.setattr(adapter, "_loop", None)

    async def noop():
        return "posted"

    assert adapter._call(noop()) is None


def test_resolve_channel_rejects_a_bad_id(monkeypatch):
    monkeypatch.setattr(adapter, "_loop", None)
    assert adapter._resolve_channel("not-an-id") is None


# ── the adapter reuses core session policy, not its own copy ────────────────
def test_session_policy_comes_from_core():
    assert adapter._session_key(DM) == sessions.key_for(
        str(DM.id), None, is_dm=True)
