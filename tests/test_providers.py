"""The provider registry — the switch, and the rule that overrides it.

The rule is the reason this file is long: Loki's guest scope is a Claude Code
settings file, so a provider that cannot carry one must never be handed a
request that needs one. Selecting Gemini in a DM is a preference; selecting it
for a stranger's request would silently widen what that stranger can read.
"""
import json

import pytest

from loki.core import config, providers
from loki.core.providers import base, claude, codex, gemini, groq, kimi


@pytest.fixture
def clean(tmp_path, monkeypatch):
    """A registry with its own state file, always starting on the default."""
    monkeypatch.setattr(providers, "STATE_FILE", tmp_path / "provider.json")
    monkeypatch.setattr(config, "PROVIDER", "claude")
    monkeypatch.setattr(config, "RESTRICTED_MODE", "fallback")
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path))
    # Gemini refuses to spawn without a cached sign-in (see gemini.run — an
    # unauthenticated CLI eats the prompt through its login prompt). These
    # tests are about the command line and the parser, so the gate is stubbed
    # open; the gate itself is covered in test_gemini_live.py.
    monkeypatch.setattr(gemini, "available", lambda: (True, ""))
    return tmp_path


class _FakeProc:
    def __init__(self, out="", err="", rc=0):
        self.pid, self.returncode = 1, rc
        self._out, self._err = out, err

    def communicate(self, input=None, timeout=None):
        return self._out, self._err


def _capture(monkeypatch, out="", err="", rc=0) -> dict:
    """Swap the spawn for a recorder. Returns the dict it fills in."""
    seen = {}

    def fake_popen(cmd, **kw):
        seen["cmd"], seen["env"], seen["cwd"] = cmd, kw.get("env", {}), kw.get("cwd")
        return _FakeProc(out, err, rc)

    monkeypatch.setattr(base.subprocess, "Popen", fake_popen)
    return seen


# ── the switch ───────────────────────────────────────────────────────────────
def test_default_is_claude(clean):
    assert providers.name() == "claude"
    assert providers.current() is claude


def test_switch_persists(clean):
    assert providers.set_current("gemini") is True
    assert providers.name() == "gemini"
    assert json.loads((clean / "provider.json").read_text())["name"] == "gemini"


def test_switch_to_the_same_one_reports_no_change(clean):
    providers.set_current("gemini")
    assert providers.set_current("gemini") is False


def test_unknown_name_is_refused(clean):
    assert providers.set_current("gpt9") is False
    assert providers.name() == "claude"


def test_env_setting_is_what_a_restart_returns_to(clean, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER", "codex")
    assert providers.name() == "codex"          # no state file yet
    providers.set_current("gemini")
    assert providers.name() == "gemini"         # runtime switch wins while set


def test_a_corrupt_state_file_falls_back_rather_than_crashing(clean):
    (clean / "provider.json").write_text("not json{", encoding="utf-8")
    assert providers.name() == "claude"


def test_every_provider_answers_the_interface(clean):
    for mod in providers.ALL.values():
        assert isinstance(mod.available(), tuple) and len(mod.available()) == 2
        assert mod.NAME and mod.LABEL and mod.PLAN
        assert callable(mod.run)


@pytest.mark.parametrize("mod", list(providers.ALL.values()), ids=lambda m: m.NAME)
def test_a_ready_provider_carries_no_warning(mod, monkeypatch, tmp_path):
    """The note is the reason a provider is *not* ready. Returning it alongside
    a healthy result puts a permanent ⚠️ next to a provider that works — which
    is exactly what `!provider` did until this was pinned down."""
    monkeypatch.setattr(mod, "version", lambda: "1.0")
    monkeypatch.setenv("KIMI_API_KEY", "sk-moon")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    monkeypatch.setattr(mod, "_logged_in", lambda: True, raising=False)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text('{"auth_mode": "chatgpt"}',
                                               encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    ready, note = mod.available()
    assert ready and note == "", f"{mod.NAME}: ready but warns {note!r}"


# ── the rule: a sandboxed request never leaves Claude ─────────────────────────
def test_settings_file_forces_claude_even_when_another_is_selected(clean, monkeypatch):
    providers.set_current("gemini")
    seen = _capture(monkeypatch,
                    out='{"result":"ok","session_id":"s","is_error":false}')
    res = providers.run("hi", None, "plan", settings_file="C:/deny.json")
    assert res["provider"] == "claude"
    assert "--settings" in seen["cmd"]


def test_a_plain_request_follows_the_switch(clean, monkeypatch):
    providers.set_current("gemini")
    _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    assert providers.run("hi", None, "plan")["provider"] == "gemini"


def test_providers_without_a_sandbox_refuse_a_settings_file_directly(clean):
    """The belt to the registry's suspenders: a direct caller cannot bypass it."""
    for mod in (gemini, codex, groq):
        res = mod.run("hi", None, "plan", settings_file="C:/deny.json")
        assert res["error"] and res["provider"] == mod.NAME


def test_only_claude_and_kimi_claim_a_sandbox(clean):
    """Kimi drives the same binary, so its deny rules are the real thing."""
    assert claude.SANDBOX and kimi.SANDBOX
    assert not (gemini.SANDBOX or codex.SANDBOX or groq.SANDBOX)


# ── environment hygiene ──────────────────────────────────────────────────────
def test_claude_strips_inherited_anthropic_auth(clean, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")
    monkeypatch.setenv("CLAUDE_CODE_SESSION", "leak")
    seen = _capture(monkeypatch, out='{"result":"ok","session_id":"s"}')
    providers.run("hi", None, "plan")
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "CLAUDE_CODE_SESSION" not in seen["env"]


def test_the_strip_leaves_unrelated_google_credentials_alone(clean, monkeypatch):
    """MCP servers a spawned agent loads read these. A blanket GOOGLE_ sweep
    would break tools that have nothing to do with which model is answering."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "C:/sa.json")
    seen = _capture(monkeypatch, out='{"result":"ok","session_id":"s"}')
    providers.run("hi", None, "plan")
    assert seen["env"]["GOOGLE_APPLICATION_CREDENTIALS"] == "C:/sa.json"


def test_gemini_drops_an_inherited_api_key_and_asks_for_the_account(clean, monkeypatch):
    """A stray GEMINI_API_KEY in the parent shell would move a whole workspace
    onto metered billing without a word."""
    monkeypatch.setenv("GEMINI_API_KEY", "leak")
    monkeypatch.delenv("GEMINI_AUTH", raising=False)
    providers.set_current("gemini")
    seen = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("hi", None, "plan")
    assert "GEMINI_API_KEY" not in seen["env"]
    assert seen["env"]["GOOGLE_GENAI_USE_GCA"] == "true"


def test_gemini_api_key_mode_is_opt_in(clean, monkeypatch):
    monkeypatch.setenv("GEMINI_AUTH", "apikey")
    monkeypatch.setenv("GEMINI_API_KEY", "chosen")
    providers.set_current("gemini")
    seen = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("hi", None, "plan")
    assert seen["env"]["GEMINI_API_KEY"] == "chosen"
    assert "GOOGLE_GENAI_USE_GCA" not in seen["env"]


def test_codex_strips_the_api_key_so_the_chatgpt_plan_answers(clean, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "leak")
    providers.set_current("codex")
    seen = _capture(monkeypatch, out='{"type":"thread.started","thread_id":"t1"}')
    providers.run("hi", None, "plan")
    assert "OPENAI_API_KEY" not in seen["env"]


def test_kimi_redirects_the_claude_binary_and_drops_the_anthropic_login(clean, monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-moon")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leak-anthropic")
    providers.set_current("kimi")
    seen = _capture(monkeypatch, out='{"result":"ok","session_id":"k1"}')
    res = providers.run("hi", None, "plan")
    assert res["provider"] == "kimi"
    assert seen["env"]["ANTHROPIC_BASE_URL"] == kimi.DEFAULT_BASE_URL
    assert seen["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-moon"
    # the pinned Claude account must not travel to Moonshot
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen["env"]


def test_kimi_without_a_key_says_which_one(clean, monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    res = kimi.run("hi", None, "plan")
    assert res["error"] and "KIMI_API_KEY" in res["text"]


# ── gemini: the command line ─────────────────────────────────────────────────
def test_gemini_always_skips_trust(clean, monkeypatch):
    """An untrusted folder silently downgrades --approval-mode to "ask a
    human", which in a headless spawn means hang until the timeout."""
    providers.set_current("gemini")
    seen = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("hi", None, "bypassPermissions")
    assert "--skip-trust" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--approval-mode") + 1] == "yolo"


def test_gemini_plan_mode_maps_to_plan(clean, monkeypatch):
    providers.set_current("gemini")
    seen = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("hi", None, "plan")
    assert seen["cmd"][seen["cmd"].index("--approval-mode") + 1] == "plan"


def test_gemini_names_a_new_session_and_resumes_by_id(clean, monkeypatch):
    providers.set_current("gemini")
    seen = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("hi", None, "plan")
    assert "--session-id" in seen["cmd"] and "--resume" not in seen["cmd"]

    seen2 = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("hi", "g1", "plan")
    assert seen2["cmd"][seen2["cmd"].index("--resume") + 1] == "g1"
    assert "--session-id" not in seen2["cmd"]


def test_gemini_sends_the_prompt_only_on_stdin(clean, monkeypatch):
    """-p is *appended* to stdin rather than replacing it, so using both would
    send the question twice."""
    providers.set_current("gemini")
    seen = _capture(monkeypatch, out='{"response":"ok","session_id":"g1"}')
    providers.run("the question", None, "plan")
    assert "-p" not in seen["cmd"] and "the question" not in seen["cmd"]


# ── gemini: reading the answer ───────────────────────────────────────────────
def test_gemini_reads_an_error_envelope_off_stderr(clean, monkeypatch):
    """Failures print the JSON on stderr; reading only stdout turns every real
    error into "(empty response)"."""
    providers.set_current("gemini")
    _capture(monkeypatch, err=json.dumps(
        {"session_id": "g9", "error": {"type": "Error", "code": 41,
                                       "message": "Please set an Auth method"}}), rc=41)
    res = providers.run("hi", None, "plan")
    assert res["error"] and "Auth method" in res["text"] and res["session_id"] == "g9"


def test_gemini_ignores_prose_printed_beside_the_envelope(clean, monkeypatch):
    """The CLI narrates on stderr — "YOLO mode is enabled.", "Approval mode
    overridden…" — above and below the JSON it actually means."""
    providers.set_current("gemini")
    _capture(monkeypatch,
             err=("Approval mode overridden to \"default\" {not json}\n"
                  'YOLO mode is enabled.\n'
                  '{"session_id":"g5","response":"parsed anyway"}\n'
                  "trailing noise {"), rc=0)
    res = providers.run("hi", None, "plan")
    assert res["text"] == "parsed anyway" and res["session_id"] == "g5"


def test_gemini_names_the_closed_door_instead_of_dumping_a_stack_trace(
        clean, monkeypatch):
    """Google stopped serving the CLI to individual accounts in June 2026. The
    CLI reports it as an uncaught error with a screenful of node frames, which
    reads like a bug in Loki — it is a closed door, and there is a setting that
    goes around it."""
    providers.set_current("gemini")
    _capture(monkeypatch, err=(
        "YOLO mode is enabled.\n"
        "An unexpected critical error occurred:IneligibleTierError: This "
        "client is no longer supported for Gemini Code Assist for individuals. "
        "To continue using Gemini, please migrate to the Antigravity suite.\n"
        "    at throwIneligibleOrProjectIdError (file:///C:/x/chunk.js:309966:11)\n"
        "    at _doSetupUser (file:///C:/x/chunk.js:309955:5)\n"
        "    at process.processTicksAndRejections (node:internal:104:5)\n"), rc=1)
    res = providers.run("hi", None, "plan")
    assert res["error"]
    assert "GEMINI_API_KEY" in res["text"]        # the way around it
    assert "processTicksAndRejections" not in res["text"]


def test_a_raw_failure_loses_its_stack_frames(clean, monkeypatch):
    providers.set_current("gemini")
    _capture(monkeypatch, err=(
        "Error: something broke\n"
        + "\n".join(f"    at frame{i} (file:///x.js:{i}:1)" for i in range(40))),
        rc=1)
    res = providers.run("hi", None, "plan")
    assert "something broke" in res["text"]
    assert "at frame" not in res["text"]
    assert len(res["text"]) < 500


def test_trim_keeps_the_message_and_caps_the_length():
    assert base.trim("boom\n    at a (x.js:1:1)\n    at b (x.js:2:2)") == "boom"
    assert base.trim("x" * 900).endswith("…") and len(base.trim("x" * 900)) <= 402
    assert base.trim("") == ""


def test_gemini_reports_quota_as_quota(clean, monkeypatch):
    providers.set_current("gemini")
    _capture(monkeypatch, err=json.dumps(
        {"error": {"code": 429, "message": "Resource exhausted"}}), rc=1)
    assert providers.run("hi", None, "plan")["reason"] == "quota"


def test_gemini_retries_once_without_a_dead_session(clean, monkeypatch):
    """A dropped session should cost a fresh answer, not an error."""
    calls = []

    def fake_spawn(cmd, prompt, cwd, env, job, timeout=None):
        calls.append(cmd)
        if "--resume" in cmd:
            return "", 'Invalid session identifier "g1".', 1, False
        return json.dumps({"response": "second time", "session_id": "g2"}), "", 0, False

    monkeypatch.setattr(base, "spawn", fake_spawn)
    providers.set_current("gemini")
    res = providers.run("hi", "g1", "plan")
    assert len(calls) == 2 and not res["error"]
    assert res["text"] == "second time" and res["session_id"] == "g2"


def test_gemini_does_not_retry_work_the_user_cancelled(clean, monkeypatch):
    """`!cancel` kills the child mid-turn. Reading that as "the session is
    gone, try again" would respawn the work that was just stopped."""
    calls = []
    job = {}

    def fake_spawn(cmd, prompt, cwd, env, job_dict, timeout=None):
        calls.append(cmd)
        job_dict["cancelled"] = True          # as jobs.cancel would
        return "", 'Invalid session identifier "g1".', 1, False

    monkeypatch.setattr(base, "spawn", fake_spawn)
    providers.set_current("gemini")
    providers.run("hi", "g1", "plan", job=job)
    assert len(calls) == 1


def test_gemini_does_not_retry_a_normal_failure(clean, monkeypatch):
    calls = []

    def fake_spawn(cmd, prompt, cwd, env, job, timeout=None):
        calls.append(cmd)
        return "", json.dumps({"error": {"message": "model exploded"}}), 1, False

    monkeypatch.setattr(base, "spawn", fake_spawn)
    providers.set_current("gemini")
    res = providers.run("hi", "g1", "plan")
    assert len(calls) == 1 and "exploded" in res["text"]


# ── codex ────────────────────────────────────────────────────────────────────
def test_codex_resumes_through_the_subcommand(clean, monkeypatch):
    providers.set_current("codex")
    seen = _capture(monkeypatch, out='{"type":"thread.started","thread_id":"t1"}')
    providers.run("hi", "t1", "plan")
    assert seen["cmd"][1:4] == ["exec", "resume", "t1"]


def test_codex_permission_modes_survive_the_resume_subcommand(clean, monkeypatch):
    """`codex exec resume` accepts neither --sandbox nor --cd, so the mode has
    to travel as -c overrides that both subcommands take."""
    providers.set_current("codex")
    seen = _capture(monkeypatch, out='{"type":"thread.started","thread_id":"t1"}')
    providers.run("hi", "t1", "plan")
    assert 'sandbox_mode="read-only"' in seen["cmd"]
    assert 'approval_policy="never"' in seen["cmd"]

    seen2 = _capture(monkeypatch, out='{"type":"thread.started","thread_id":"t1"}')
    providers.run("hi", "t1", "bypassPermissions")
    assert "--dangerously-bypass-approvals-and-sandbox" in seen2["cmd"]


def test_codex_takes_the_conversation_id_from_the_event_stream(clean, monkeypatch, tmp_path):
    providers.set_current("codex")
    stream = "\n".join([json.dumps({"type": "thread.started", "thread_id": "t7"}),
                        json.dumps({"type": "turn.started"})])
    _capture(monkeypatch, out=stream)
    res = providers.run("hi", None, "plan")
    assert res["session_id"] == "t7"


def test_codex_reads_the_answer_from_the_last_message_file(clean, monkeypatch):
    providers.set_current("codex")

    def fake_spawn(cmd, prompt, cwd, env, job, timeout=None):
        path = cmd[cmd.index("--output-last-message") + 1]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("the answer\n")
        return json.dumps({"type": "thread.started", "thread_id": "t7"}), "", 0, False

    monkeypatch.setattr(base, "spawn", fake_spawn)
    res = providers.run("hi", None, "plan")
    assert res["text"] == "the answer" and not res["error"]


def test_codex_names_a_stale_login_instead_of_looking_like_a_crash(clean, monkeypatch):
    providers.set_current("codex")
    _capture(monkeypatch, out=json.dumps(
        {"type": "turn.failed",
         "error": {"message": "Your access token could not be refreshed "
                              "because your refresh token was already used."}}))
    res = providers.run("hi", None, "plan")
    # the "log in again" framing is translated, so assert on what must survive
    # translation: the key marker, and the CLI's own words underneath it
    assert res["error"] and res["text"].startswith("🔑")
    assert "refresh token" in res["text"].lower()


def test_codex_auth_mode_reads_the_cli_own_file(clean, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
        encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    assert codex.auth_mode() == "chatgpt"


def test_codex_flags_an_api_key_login_as_metered(clean, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey"}), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(codex, "version", lambda: "codex-cli 1.0")
    ready, note = codex.available()
    assert ready and "bills per token" in note


# ── groq ─────────────────────────────────────────────────────────────────────
def test_groq_without_a_key_says_which_one(clean, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    res = groq.run("hi", None, "plan")
    assert res["error"] and "GROQ_API_KEY" in res["text"]


def test_groq_keeps_its_transcript_locally(clean, monkeypatch):
    """There is no session on the far end, so continuity is a file here."""
    monkeypatch.setattr(groq, "_STORE", clean / "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    sent = {}

    class _Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

        def read(self_inner):
            return json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode()

    def fake_open(req, timeout=None):
        sent["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(groq.urllib.request, "urlopen", fake_open)
    first = groq.run("ping", None, "plan")
    assert first["text"] == "pong" and first["session_id"]

    groq.run("again", first["session_id"], "plan")
    roles = [m["role"] for m in sent["body"]["messages"]]
    assert roles == ["user", "assistant", "user"]      # history was replayed


def test_groq_forget_removes_the_transcript(clean, monkeypatch):
    monkeypatch.setattr(groq, "_STORE", clean / "groq")
    (clean / "groq").mkdir()
    groq._remember("s1", [{"role": "user", "content": "x"}])
    assert groq._path("s1").exists()
    groq.forget("s1")
    assert not groq._path("s1").exists()


def test_groq_trims_history_to_stay_under_a_free_tier_minute(clean, monkeypatch):
    monkeypatch.setattr(groq, "_STORE", clean / "groq")
    monkeypatch.setattr(groq, "MAX_TURNS", 4)
    groq._remember("s2", [{"role": "user", "content": f"m{i}"} for i in range(10)])
    assert len(groq._history("s2")) == 4


def test_groq_surfaces_the_api_message_on_a_bad_model(clean, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    import urllib.error
    import io

    def fake_open(req, timeout=None):
        raise urllib.error.HTTPError(
            "u", 400, "Bad Request", {},
            io.BytesIO(json.dumps(
                {"error": {"message": "model `nope` does not exist"}}).encode()))

    monkeypatch.setattr(groq.urllib.request, "urlopen", fake_open)
    res = groq.run("hi", None, "plan")
    assert res["error"] and "does not exist" in res["text"]


# ── shared helpers ───────────────────────────────────────────────────────────
def test_a_timeout_is_a_timeout_not_a_crash(clean, monkeypatch):
    monkeypatch.setattr(base, "spawn",
                        lambda *a, **kw: ("", "", -1, True))
    for pname in ("claude", "gemini", "codex", "kimi"):
        providers.set_current(pname)
        monkeypatch.setenv("KIMI_API_KEY", "sk-moon")
        assert providers.run("hi", None, "plan")["reason"] == "timeout"


def test_a_missing_cli_names_the_path_it_tried(clean, monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(base, "spawn", boom)
    res = providers.run("hi", None, "plan")          # claude: no pre-spawn gate
    assert res["error"] and claude.command() in res["text"]


def test_quota_phrases_are_recognised_across_vendors():
    for phrase in ("rate limit exceeded", "RESOURCE_EXHAUSTED",
                   "you have hit your usage limit", "Too Many Requests"):
        assert base.quota_hit(phrase)
    assert base.quota_hit("", 429) and base.quota_hit("", 529)
    assert not base.quota_hit("syntax error in your file")
