"""F-42 cuts 2, 3 and 4 and their settings: rotate at min(rotate_tokens, rotate_pct x window), subagents on `subagent_model`, a short
skill listing (`skill_desc_chars`), the validation of the four keys, and the defaults an upgraded install gets."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from aiteams import config, workflow
from aiteams.daemon import create_app
from aiteams.orchestrator import lifecycle
from aiteams.orchestrator.claude_proc import build_cmd, child_env
from tests.conftest import AUTH
from tests.test_lifecycle import FinishedRunner, ended_turn, setup

BIG = {"modelUsage": {"m": {"contextWindow": 1_000_000}}}  # a 1M window: 60% is 600k, so rotate_tokens is the limit
SMALL = {"modelUsage": {"m": {"contextWindow": 200_000}}}  # 60% is 120k, below 150k


def finish(st, proj, ctx, result, role="backend", first=False, kind="normal"):
    """End a turn of `role` with `ctx` tokens of context; unless `first`, an earlier measured turn is in the session."""
    proj.db.new_session(role, f"s-{ctx}-{kind}-{first}", "first turn")
    if not first:
        ended_turn(proj.db, role, ctx=1_000, kind=kind)
    t = ended_turn(proj.db, role, ctx=ctx, result=result, kind=kind)
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, kind=kind)))
    return t


# ---- cut 2: rotate at a fixed size -----------------------------------------------------------------------------------------
def test_rotates_at_150000_not_at_149999(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    finish(st, proj, 149_999, BIG, "backend")
    assert db.role_state("backend")["rotate_reason"] is None and db.queued_wakes() == []
    finish(st, proj, 150_000, BIG, "qa")
    rs = db.role_state("qa")
    assert rs["rotate_reason"] == "context 150k tokens (limit 150k)" and rs["rotate_handoff"] == 1
    assert db.queued_wake("qa", "handoff")


def test_the_reason_reads_like_the_design(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    finish(st, proj, 152_300, BIG)
    assert db.role_state("backend")["rotate_reason"] == "context 152k tokens (limit 150k)"


def test_a_200k_window_rotates_at_the_percentage_120k(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    finish(st, proj, 119_999, SMALL, "backend")
    assert db.role_state("backend")["rotate_reason"] is None
    finish(st, proj, 120_000, SMALL, "qa")
    assert db.role_state("qa")["rotate_reason"] == "context 120k tokens (limit 120k)"


def test_rotate_tokens_zero_falls_back_to_the_percentage(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], rotate_tokens=0)
    finish(st, proj, 599_999, BIG, "backend")
    assert db.role_state("backend")["rotate_reason"] is None  # 150k no longer rotates
    finish(st, proj, 600_000, BIG, "qa")
    assert db.role_state("qa")["rotate_reason"] == "context 600k tokens (limit 600k)"


def test_a_custom_limit_and_a_bad_one(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], rotate_tokens=80_000)
    finish(st, proj, 80_000, BIG)
    assert db.role_state("backend")["rotate_reason"] == "context 80k tokens (limit 80k)"
    for bad in ("lots", -5, True, None):
        assert config.rotation_limit({"rotate_pct": 0.6, "rotate_tokens": bad}, 1_000_000) == 150_000  # the default applies


def test_a_sessions_first_turn_never_rotates_it_logs_instead(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    t = finish(st, proj, 200_000, SMALL, "backend", first=True)  # an oversized start, as a fresh session with a huge handoff
    assert db.role_state("backend")["rotate_reason"] is None and db.queued_wakes() == []
    skipped = [a for a in db.activity() if a["kind"] == "rotation_skipped"]
    assert len(skipped) == 1 and skipped[0]["role"] == "backend" and "200k tokens (limit 120k)" in skipped[0]["text"]
    assert any(e == "activity" and d["kind"] == "rotation_skipped" for e, d in proj.hub.events)
    # the next turn of that session does rotate
    t2 = ended_turn(db, "backend", ctx=200_000, result=SMALL)
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t2)))
    assert db.role_state("backend")["rotate_reason"] == "context 200k tokens (limit 120k)"


def test_a_failed_first_turn_without_a_measurement_does_not_count_as_the_first(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("backend", "s1", "first turn")
    ended_turn(db, "backend", state="failed", ctx=None)  # no context recorded
    t = ended_turn(db, "backend", ctx=160_000, result=BIG)
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t)))
    assert db.role_state("backend")["rotate_reason"] is None  # still the session's first measured turn
    t2 = ended_turn(db, "backend", ctx=160_000, result=BIG)
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t2)))
    assert db.role_state("backend")["rotate_reason"] == "context 160k tokens (limit 150k)"


def test_the_standup_and_brainstorm_turns_rotate_too_after_the_first(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    finish(st, proj, 160_000, BIG, "qa", kind="standup")
    assert db.role_state("qa")["rotate_reason"] == "context 160k tokens (limit 150k)"


def test_context_pct_is_relative_to_the_limit(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("backend", "s1", "first turn")
    ended_turn(db, "backend", ctx=75_000, result=BIG)
    row = {r["role"]: r for r in workflow.team_status(st, proj)}["backend"]
    assert row["context_tokens"] == 75_000 and row["context_limit"] == 150_000 and row["context_pct"] == pytest.approx(0.5)
    st.settings["rotate_tokens"] = 0  # the percentage alone: 600k
    row = {r["role"]: r for r in workflow.team_status(st, proj)}["backend"]
    assert row["context_limit"] == 600_000 and row["context_pct"] == pytest.approx(0.125)


# ---- cut 3: subagents on Sonnet --------------------------------------------------------------------------------------------
def env_for(**kw):
    return child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "backend", 7, 600, "tok", **kw)


def test_subagent_model_sets_the_spawn_env_and_empty_leaves_it_unset(monkeypatch):
    assert env_for(subagent_model="sonnet")["CLAUDE_CODE_SUBAGENT_MODEL"] == "sonnet"
    assert "CLAUDE_CODE_SUBAGENT_MODEL" not in env_for(subagent_model="")
    assert "CLAUDE_CODE_SUBAGENT_MODEL" not in env_for()
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "opus")  # the daemon's own env never decides: the setting does
    assert "CLAUDE_CODE_SUBAGENT_MODEL" not in env_for(subagent_model="")
    assert env_for(subagent_model="haiku")["CLAUDE_CODE_SUBAGENT_MODEL"] == "haiku"


def test_the_setting_reaches_a_turns_spawn_env_per_turn(db, home, monkeypatch):
    """TurnRunner passes `subagent_model` from the live settings (read per turn)."""
    from aiteams.orchestrator import turn as turn_mod
    from tests.test_claude_proc import ASSISTANT, INIT, RESULT, fake_cmd, fake_env
    st, proj = setup(db, home, monkeypatch, [])
    seen = []
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: (seen.append(k.get("subagent_model")), fake_env([INIT, ASSISTANT, RESULT]))[1])
    for value in ("sonnet", "", "haiku"):
        st.settings["subagent_model"] = value
        m = db.add_message("dm-backend", "master", "Message", "hi", [], None)
        db.set_receipt(m["id"], "backend", "queued")
        asyncio.run(turn_mod.TurnRunner(st, proj, db.add_wake("backend", "dm-backend", [m["id"]], "master")).run())
    assert seen == ["sonnet", "", "haiku"]
    assert config.subagent_model({}) == "sonnet" and config.subagent_model({"subagent_model": 7}) == "sonnet"


# ---- cut 4: a short skill listing ------------------------------------------------------------------------------------------
def settings_json(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])


def test_skill_desc_chars_puts_the_key_in_settings_and_zero_leaves_it_out():
    base = {"max_turns": 40}
    for chars, want in ((80, 80), (120, 120)):
        cmd = build_cmd({**base, "skill_desc_chars": chars}, "backend", env_for(), "/tmp/sys.md", "s1", False, "opus")
        assert settings_json(cmd)["skillListingMaxDescChars"] == want
    off = settings_json(build_cmd({**base, "skill_desc_chars": 0}, "backend", env_for(), "/tmp/sys.md", "s1", False, "opus"))
    assert "skillListingMaxDescChars" not in off and "hooks" in off  # the hook settings are untouched
    default = settings_json(build_cmd({**base, **{k: config.DEFAULT_SETTINGS[k] for k in ("skill_desc_chars",)}}, "backend",
                                      env_for(), "/tmp/sys.md", "s1", False, "opus"))
    assert default["skillListingMaxDescChars"] == 80
    for bad in ("many", -1, True):  # hand-edited garbage falls back to the default, never crashes the spawn
        cmd = build_cmd({**base, "skill_desc_chars": bad}, "backend", env_for(), "/tmp/sys.md", "s1", False, "opus")
        assert settings_json(cmd)["skillListingMaxDescChars"] == 80


# ---- the settings -----------------------------------------------------------------------------------------------------------
def test_defaults_reach_an_install_without_the_keys(home):
    (home / "settings.json").write_text(json.dumps({"max_concurrent": 0, "rotate_pct": 0.6}))  # an older file: none of the four keys
    s = config.load_settings()
    assert (s["rotate_tokens"], s["wake_history"], s["subagent_model"], s["skill_desc_chars"]) == (150000, "new", "sonnet", 80)
    assert config.wake_history({"wake_history": "bogus"}) == "new" and config.wake_history({"wake_history": "full"}) == "full"


@pytest.fixture
def client(home):
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH) as c:
        yield c


def test_the_put_saves_and_validates_the_four_keys(client, home):
    r = client.put("/api/settings", json={"wake_history": "full", "rotate_tokens": 0, "subagent_model": "", "skill_desc_chars": 0})
    assert r.status_code == 200, r.text
    saved = json.loads((home / "settings.json").read_text())
    assert (saved["wake_history"], saved["rotate_tokens"], saved["subagent_model"], saved["skill_desc_chars"]) == ("full", 0, "", 0)
    assert client.app.state.app.settings["wake_history"] == "full"  # the live settings: read per turn, no restart
    assert client.get("/api/settings").json()["rotate_tokens"] == 0
    ok = client.put("/api/settings", json={"subagent_model": "  claude-sonnet-5-5 ", "rotate_tokens": 120000, "wake_history": "new"})
    assert ok.status_code == 200 and ok.json()["subagent_model"] == "claude-sonnet-5-5"
    for body, word in (({"wake_history": "some"}, "wake_history"), ({"rotate_tokens": -1}, "rotate_tokens"),
                       ({"rotate_tokens": "150k"}, "rotate_tokens"), ({"rotate_tokens": True}, "rotate_tokens"),
                       ({"skill_desc_chars": 1.5}, "skill_desc_chars"), ({"skill_desc_chars": -80}, "skill_desc_chars"),
                       ({"subagent_model": "two words"}, "subagent_model"), ({"subagent_model": 5}, "subagent_model"),
                       ({"subagent_model": "x" * 81}, "subagent_model")):
        r = client.put("/api/settings", json=body)
        assert r.status_code == 400 and word in r.json()["detail"], body
    assert client.get("/api/settings").json()["rotate_tokens"] == 120000  # a refused PUT saved nothing
