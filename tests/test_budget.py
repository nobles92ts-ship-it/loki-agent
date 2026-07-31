"""Usage budgets — caps refuse guests, mitigations stay manual by default."""
import json
import time

import pytest

from loki.core import budget, usage


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated budget + usage ledger."""
    monkeypatch.setattr(budget, "BUDGET_FILE", tmp_path / "budget.json")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.jsonl")
    budget._cache.update(stamp=None, data=None)
    return tmp_path


def spend(n, org=None, when=None):
    """Record n calls into the ledger."""
    rows = []
    for _ in range(n):
        row = {"ts": when if when is not None else time.time(),
               "kind": "guest", "user": "UG", "ok": True, "dur": 1.0,
               "reason": "ok"}
        if org:
            row["org"] = org
        rows.append(json.dumps(row))
    with usage.USAGE_FILE.open("a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


# ── defaults ─────────────────────────────────────────────────────────────────
def test_defaults_are_off_and_manual(store):
    s = budget.settings()
    assert s["mode"] == "manual"
    assert s["daily"] == 0 and s["weekly"] == 0 and s["orgs"] == {}
    assert budget.scopes() == []
    assert budget.check_guest()[0] is True
    assert budget.model_override() == ""


def test_unreadable_file_falls_back_to_defaults(store):
    budget.BUDGET_FILE.write_text("{not json", encoding="utf-8")
    budget._cache.update(stamp=None, data=None)
    assert budget.settings()["mode"] == "manual"


# ── caps ─────────────────────────────────────────────────────────────────────
def test_daily_cap_blocks_guests_when_reached(store):
    budget.set_limit("daily", 3)
    spend(2)
    assert budget.check_guest()[0] is True
    spend(1)
    allowed, key, params = budget.check_guest()
    assert allowed is False and key == "budget_reached"
    assert params == {"label": "daily", "used": 3, "limit": 3}


def test_weekly_cap_uses_a_rolling_window(store):
    budget.set_limit("weekly", 5)
    spend(5, when=time.time() - 8 * 86400)      # older than the window
    assert budget.check_guest()[0] is True
    spend(5, when=time.time() - 2 * 86400)
    assert budget.check_guest()[0] is False


def test_daily_cap_ignores_yesterday(store):
    budget.set_limit("daily", 2)
    spend(5, when=time.time() - 30 * 3600)
    assert budget.check_guest()[0] is True


def test_org_cap_is_scoped_to_that_org(store):
    budget.set_org_limit("acme", 2)
    spend(5, org="beta")
    assert budget.check_guest("acme")[0] is True
    spend(2, org="acme")
    allowed, key, params = budget.check_guest("acme")
    assert allowed is False and key == "budget_reached_org"
    assert params["label"] == "acme"
    assert budget.check_guest("beta")[0] is True     # unaffected


def test_zero_limit_means_no_cap(store):
    budget.set_limit("daily", 0)
    spend(100)
    assert budget.check_guest()[0] is True
    assert budget.scopes() == []


def test_clear_removes_every_cap(store):
    budget.set_limit("daily", 1)
    budget.set_org_limit("acme", 1)
    budget.apply("sonnet")
    spend(5)
    budget.clear()
    assert budget.check_guest("acme")[0] is True
    assert budget.model_override() == ""


# ── pause / resume ───────────────────────────────────────────────────────────
def test_pause_blocks_guests_then_resume_releases(store):
    assert budget.apply("pause") == "budget_applied_pause"
    allowed, key, _ = budget.check_guest()
    assert allowed is False and key == "budget_paused"
    assert budget.guests_paused_for() > 0

    assert budget.apply("resume") == "budget_applied_resume"
    assert budget.check_guest()[0] is True
    assert budget.apply("resume") == "budget_nochange"


def test_expired_pause_stops_blocking(store):
    budget.apply("pause")
    later = time.time() + 2 * 86400
    assert budget.check_guest(now=later)[0] is True


# ── model mitigation ─────────────────────────────────────────────────────────
def test_sonnet_switch_and_back(store):
    assert budget.apply("sonnet") == "budget_applied_sonnet"
    assert budget.model_override() == "sonnet"
    assert budget.apply("sonnet") == "budget_nochange"
    assert budget.apply("default") == "budget_applied_default"
    assert budget.model_override() == ""


def test_unknown_action_is_rejected(store):
    assert budget.apply("rm -rf") == "budget_help"
    assert budget.apply("") == "budget_help"


# ── threshold alerts ─────────────────────────────────────────────────────────
def test_manual_mode_alerts_without_changing_anything(store):
    budget.set_limit("daily", 10)
    spend(8)
    alerts = budget.note_usage()
    assert len(alerts) == 1
    assert alerts[0]["threshold"] == 80 and alerts[0]["applied"] == ""
    assert budget.model_override() == ""          # manual: nothing applied


def test_alert_fires_once_per_threshold_per_day(store):
    budget.set_limit("daily", 10)
    spend(8)
    assert len(budget.note_usage()) == 1
    assert budget.note_usage() == []              # same level → quiet
    spend(2)
    alerts = budget.note_usage()                  # crossing 100 is new news
    assert len(alerts) == 1 and alerts[0]["threshold"] == 100
    assert budget.note_usage() == []


def test_jumping_straight_past_full_reports_only_the_top(store):
    budget.set_limit("daily", 10)
    spend(12)
    alerts = budget.note_usage()
    assert [a["threshold"] for a in alerts] == [100]


def test_auto_mode_switches_the_model_itself(store):
    budget.set_mode("auto")
    budget.set_limit("daily", 10)
    spend(8)
    alerts = budget.note_usage()
    assert alerts[0]["applied"] == "budget_applied_sonnet"
    assert budget.model_override() == "sonnet"


def test_auto_mode_does_not_re_apply(store):
    budget.set_mode("auto")
    budget.set_limit("daily", 10)
    spend(8)
    budget.note_usage()
    spend(2)
    assert budget.note_usage()[0]["applied"] == ""      # already on sonnet


def test_ignore_silences_today(store):
    budget.set_limit("daily", 10)
    spend(10)
    budget.apply("ignore")
    assert budget.note_usage() == []


def test_alerts_cover_each_scope(store):
    budget.set_limit("daily", 10)
    budget.set_org_limit("acme", 4)
    spend(8, org="acme")
    labels = {a["label"] for a in budget.note_usage()}
    assert labels == {"daily", "acme"}


def test_no_alerts_without_limits(store):
    spend(500)
    assert budget.note_usage() == []


def test_notices_are_pruned(store):
    budget.set_limit("daily", 1)
    s = budget.settings()
    s["notified"]["2000-01-01:daily:80"] = True
    budget._write(s)
    assert "2000-01-01:daily:80" not in budget.settings()["notified"]


# ── settings validation ──────────────────────────────────────────────────────
def test_setters_reject_nonsense(store):
    assert budget.set_mode("chaos") is False
    assert budget.set_limit("hourly", 5) is False
    assert budget.set_limit("daily", -1) is False
    assert budget.set_org_limit("", 5) is False
    assert budget.settings()["mode"] == "manual"


def test_org_limit_zero_removes_it(store):
    budget.set_org_limit("acme", 5)
    assert budget.settings()["orgs"] == {"acme": 5}
    budget.set_org_limit("acme", 0)
    assert budget.settings()["orgs"] == {}


def test_settings_survive_a_reload(store):
    budget.set_mode("auto")
    budget.set_limit("daily", 42)
    budget._cache.update(stamp=None, data=None)
    s = budget.settings()
    assert s["mode"] == "auto" and s["daily"] == 42


def test_one_org_over_budget_never_blocks_another(store):
    """Org caps are isolated — beta must not be blocked by acme's spending."""
    budget.set_org_limit("acme", 2)
    budget.set_org_limit("beta", 10)
    spend(9, org="acme")
    assert budget.check_guest("acme")[0] is False
    assert budget.check_guest("beta")[0] is True
    assert budget.check_guest(None)[0] is True      # unaffiliated guests too


def test_global_cap_still_binds_every_org(store):
    budget.set_limit("daily", 3)
    budget.set_org_limit("acme", 100)
    spend(3, org="beta")
    assert budget.check_guest("acme")[0] is False
