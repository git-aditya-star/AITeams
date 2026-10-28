"""F-38: when the whole team goes idle, a conversation waiting on a role is nudged, then the Manager is told, then it is
marked stuck; anyone acting resets it; turns that run or wait too long are flagged (never stopped)."""
import asyncio
import time

import pytest

from aiteams import stalls, workflow
from aiteams.api.public import post_message
from aiteams.orchestrator import scheduler as sched_mod
from aiteams.runtime import AppState
from tests.test_lifecycle import Notifier, setup
from tests.test_scheduler import StubRunner


def feat(db, title="Export", owner="lead-2", lead="lead-2", phase="build"):
    f = db.create_feature(title, phase, owner)
    db.update_feature(f["id"], lead=lead)
    return db.feature(f["id"])


def say(db, f, author, text, mentions=(), type_="Message"):
    return db.add_message(f["conversation_id"], author, type_, text, list(mentions), None)


def idle(st, proj):
    return asyncio.run(stalls.on_idle(st, proj))


def clear_wakes(db):
    db.x("DELETE FROM wake_queue")  # the woken roles ran and did nothing


def wakes(db, role=None):
    return [w for w in db.queued_wakes() if role in (None, w["role"])]


@pytest.fixture
def env(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    st.scheduler = Notifier()
    return st, proj, db


# ---- the ladder ---------------------------------------------------------------------------------------------------------
def test_a_mention_with_no_reply_climbs_nudge_then_manager_then_stuck_and_stops(env):
    st, proj, db = env
    f = feat(db)
    m = say(db, f, "manager", "@backend please file the request", ["backend"])
    assert idle(st, proj) == [{"feature": "F-1", "step": 1, "who": "backend"}]
    row = db.stall_row(f["id"])
    assert (row["who"], row["kind"], row["ref"], row["step"]) == ("backend", "message", f"msg:{m['id']}", 1)
    line = db.messages(f["conversation_id"])[-1]
    assert (line["author"], line["type"], line["mentions"]) == ("system", "System", ["backend"])
    assert line["text"] == f"Nudged @backend: #{m['id']} asks for action, with no reply."
    w = wakes(db)
    assert [(x["role"], x["kind"], x["origin"], x["conversation_id"], x["message_ids"]) for x in w] == [
        ("backend", "normal", "system", f["conversation_id"], [line["id"]])]
    assert st.scheduler.notified >= 1
    # the nudge is a System line: the same wait, not a new one
    assert stalls.find(proj, db.feature(f["id"]))["key"] == ("backend", f"msg:{m['id']}")
    assert db.feature(f["id"])["stall"] == {"who": "backend", "kind": "message", "ref": f"msg:{m['id']}", "step": 1,
                                            "stuck": False, "since": row["created_at"]}
    frames = [d for e, d in proj.hub.events if e == "feature" and d["id"] == f["id"]]
    assert frames and frames[-1]["stall"]["step"] == 1
    # second idle: the Manager is told once, in dm-manager, with the list
    clear_wakes(db)
    assert idle(st, proj) == [{"feature": "F-1", "step": 2, "who": "backend"}]
    lst = db.messages("dm-manager")[-1]
    assert lst["author"] == "system" and lst["mentions"] == ["manager"]
    assert lst["text"] == ("These conversations are still waiting after a nudge:\n"
                           f"- F-1 · waiting on Backend · #{m['id']} asks for action, with no reply")
    assert [(x["role"], x["message_ids"]) for x in wakes(db)] == [("manager", [lst["id"]])]  # no second wake for backend
    # third idle: stuck, one System line in the feature, no wake of anyone
    clear_wakes(db)
    assert idle(st, proj) == [{"feature": "F-1", "step": 3, "who": "backend"}]
    stuck = db.messages(f["conversation_id"])[-1]
    assert stuck["text"] == "Stuck: still waiting on Backend after a nudge and the Manager. No more automatic wakes."
    assert stuck["mentions"] == [] and wakes(db) == []
    assert db.feature(f["id"])["stall"]["stuck"] is True and db.feature(f["id"])["stall"]["step"] == 3
    # after that nothing more happens, however often the team goes idle
    n = len(db.q("SELECT id FROM messages"))
    assert idle(st, proj) == [] and idle(st, proj) == []
    assert len(db.q("SELECT id FROM messages")) == n and wakes(db) == []


def test_one_manager_list_per_idle_names_every_feature_that_reached_step_two(env):
    st, proj, db = env
    a, b = feat(db, "A"), feat(db, "B", owner="lead", lead="lead")
    say(db, a, "manager", "@backend go", ["backend"])
    say(db, b, "manager", "@qa test", ["qa"])
    assert [c["step"] for c in idle(st, proj)] == [1, 1]
    clear_wakes(db)
    assert [c["step"] for c in idle(st, proj)] == [2, 2]
    lines = db.messages("dm-manager")[-1]["text"].splitlines()
    assert lines[0].endswith("after a nudge:") and lines[1].startswith("- F-1 · waiting on Backend") \
        and lines[2].startswith("- F-2 · waiting on Qa")
    assert [w["role"] for w in wakes(db)] == ["manager"] and len(wakes(db)[0]["message_ids"]) == 1  # one wake, one list
    assert len([m for m in db.messages("dm-manager") if m["author"] == "system"]) == 1


def test_the_manager_being_nudged_and_told_in_one_idle_gets_one_merged_wake(env):
    st, proj, db = env
    f, g = feat(db, "A"), feat(db, "B")
    say(db, f, "backend", "@manager what now", ["manager"])
    say(db, g, "lead-2", "@backend go", ["backend"])
    idle(st, proj)  # f: manager nudged, g: backend nudged
    clear_wakes(db)
    say(db, g, "qa", "@manager one more", ["manager"])  # g's wait changes: back to step 1, waiting on the manager
    idle(st, proj)
    assert db.stall_row(f["id"])["step"] == 2 and db.stall_row(g["id"])["step"] == 1
    assert len(wakes(db, "manager")) == 1 and len(wakes(db, "manager")[0]["message_ids"]) == 2  # nudge + list, merged


def test_a_commit_in_lead_review_nudges_the_features_lead_and_a_decision_clears_it(env):
    st, proj, db = env
    f = feat(db)
    c = db.create_commit(f["id"], "backend", None, ["a.py"], "feat: x", {}, "lead_review")
    assert idle(st, proj) == [{"feature": "F-1", "step": 1, "who": "lead-2"}]
    row = db.stall_row(f["id"])
    assert (row["kind"], row["ref"]) == ("review", f"commit:{c['id']}")
    assert db.messages(f["conversation_id"])[-1]["text"] == f"Nudged @lead-2: commit request #{c['id']} waits on its review."
    assert [w["role"] for w in wakes(db)] == ["lead-2"]
    proj.hub.events.clear()
    asyncio.run(workflow.review_commit(st, proj, "lead-2", c["id"], "approve", None))  # now it waits on the Master
    assert db.stall_row(f["id"]) is None and db.feature(f["id"])["stall"] is None
    assert any(e == "feature" and d["stall"] is None for e, d in proj.hub.events)
    clear_wakes(db)
    assert idle(st, proj) == []


@pytest.mark.parametrize("how", ["reject", "withdraw"])
def test_other_commit_changes_also_clear_the_row(env, how):
    st, proj, db = env
    f = feat(db)
    c = db.create_commit(f["id"], "backend", None, ["a.py"], "feat: x", {}, "lead_review")
    idle(st, proj)
    if how == "reject":
        asyncio.run(workflow.review_commit(st, proj, "lead-2", c["id"], "reject", "no"))
    else:
        asyncio.run(workflow.withdraw_request(st, proj, "backend", "commit", c["id"], "superseded"))
    assert db.stall_row(f["id"]) is None


def test_a_commit_without_a_feature_lead_waits_on_lead_one(env):
    st, proj, db = env
    f = feat(db, lead=None)
    db.create_commit(f["id"], "backend", None, ["a.py"], "feat: x", {}, "lead_review")
    assert idle(st, proj)[0]["who"] == "lead"


# ---- what counts as a stall ------------------------------------------------------------------------------------------------
def test_an_unaddressed_handoff_waits_on_the_owner_else_on_the_manager(env):
    st, proj, db = env
    f, g, h = feat(db, "A", owner="lead-2"), feat(db, "B", owner="lead-2"), feat(db, "C", owner=None)
    a = say(db, f, "backend", "Done with the API, over to you", [], "Handoff")
    say(db, g, "lead-2", "I'm blocked on a decision", [], "Blocker")  # the owner wrote it: the Manager
    say(db, h, "qa", "Which browser?", [], "Question")  # no owner: the Manager
    out = {c["feature"]: c["who"] for c in idle(st, proj)}
    assert out == {"F-1": "lead-2", "F-2": "manager", "F-3": "manager"}
    assert (db.stall_row(f["id"])["kind"], db.stall_row(f["id"])["ref"]) == ("unaddressed", f"msg:{a['id']}")
    assert db.messages(f["conversation_id"])[-1]["text"] == f"Nudged @lead-2: #{a['id']} (Handoff) mentions nobody."
    # a Concern too; a plain Message that mentions nobody is not a hand-off
    k = feat(db, "D")
    say(db, k, "qa", "just thinking aloud", [], "Message")
    assert [c["feature"] for c in idle(st, proj) if c["feature"] == "F-4"] == []


def test_nothing_that_waits_on_the_master_or_on_nobody_is_a_stall(env):
    st, proj, db = env
    a, b, c, d, e = (feat(db, t) for t in "ABCDE")
    say(db, a, "backend", "@master your call", ["master"])  # mentions only the Master
    say(db, b, "lead-2", "@backend go", ["backend"])
    db.create_approval(b["id"], "lead-2", None, "Design: x", "s")  # a pending approval comes first: the Master
    say(db, c, "qa", "all green", [])  # no mention
    say(db, d, "lead-2", "@backend go", ["backend"])
    db.update_feature(d["id"], phase="done")
    say(db, e, "lead-2", "@backend go", ["backend"])
    db.update_feature(e["id"], archived=True)
    q = feat(db, "Q")
    say(db, q, "backend", "@backend note to self", ["backend"])  # you can't wait on yourself
    assert idle(st, proj) == []
    assert db.stall_rows() == [] and wakes(db) == []


def test_a_row_is_dropped_when_its_feature_is_done_or_the_wait_is_over(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend go", ["backend"])
    idle(st, proj)
    db.update_feature(f["id"], phase="done")
    clear_wakes(db)
    proj.hub.events.clear()
    assert idle(st, proj) == [] and db.stall_rows() == []


def test_our_own_system_lines_never_count_as_the_newest_message(env):
    st, proj, db = env
    f = feat(db)
    m = say(db, f, "manager", "@backend go", ["backend"])
    db.add_message(f["conversation_id"], "system", "System", "Lead 2 picked automatically.", ["lead-2"], None)
    assert stalls.find(proj, db.feature(f["id"]))["ref"] == f"msg:{m['id']}"


# ---- a reply, or anyone acting, resets the ladder ---------------------------------------------------------------------------
def post(st, proj, f, author, text, mentions=(), type_="Message"):
    return asyncio.run(post_message(st, proj, f["conversation_id"], author, text, type_, list(mentions), None))


def test_a_reply_from_the_role_clears_the_row_and_the_chip(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend go", ["backend"])
    idle(st, proj)
    clear_wakes(db)
    proj.hub.events.clear()
    post(st, proj, f, "backend", "on it")  # mentions nobody: the conversation moved on
    assert db.stall_row(f["id"]) is None
    assert [d["stall"] for e, d in proj.hub.events if e == "feature" and d["id"] == f["id"]] == [None]
    assert idle(st, proj) == []


def test_a_reply_that_hands_it_to_someone_else_starts_a_new_ladder_for_them(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend go", ["backend"])
    idle(st, proj)
    clear_wakes(db)
    post(st, proj, f, "backend", "@qa can you check", ["qa"])
    assert db.stall_row(f["id"]) is None  # the old wait is over
    clear_wakes(db)
    assert idle(st, proj) == [{"feature": "F-1", "step": 1, "who": "qa"}]


def test_the_manager_asking_the_same_role_again_starts_at_step_one(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend please file", ["backend"])
    idle(st, proj)
    clear_wakes(db)
    idle(st, proj)
    assert db.stall_row(f["id"])["step"] == 2
    clear_wakes(db)
    post(st, proj, f, "manager", "@backend please file the request now", ["backend"])
    assert db.stall_row(f["id"]) is None
    clear_wakes(db)
    assert idle(st, proj) == [{"feature": "F-1", "step": 1, "who": "backend"}]


def test_recheck_is_a_no_op_without_a_row_and_keeps_an_unchanged_wait(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend go", ["backend"])
    proj.hub.events.clear()
    asyncio.run(stalls.recheck(st, proj, f["id"]))
    asyncio.run(stalls.recheck(st, proj, None))
    assert proj.hub.events == [] and db.stall_row(f["id"]) is None  # no row: recheck neither creates nor announces
    idle(st, proj)
    proj.hub.events.clear()
    asyncio.run(stalls.recheck(st, proj, f["id"]))  # the same wait: kept, nothing sent
    assert db.stall_row(f["id"])["step"] == 1 and [e for e, _ in proj.hub.events if e == "feature"] == []


def test_set_stall_keeps_created_at_for_the_same_wait_only(db):
    f = db.create_feature("X")
    a = db.set_stall(f["id"], "backend", "message", "msg:1", 1)
    time.sleep(0.01)
    b = db.set_stall(f["id"], "backend", "message", "msg:1", 2)
    assert b["since"] == a["since"] and b["step"] == 2 and not b["stuck"]
    c = db.set_stall(f["id"], "backend", "message", "msg:2", 1)
    assert c["since"] > a["since"] and db.set_stall(f["id"], "backend", "message", "msg:2", 3)["stuck"] is True
    assert db.delete_stall(f["id"]) is True and db.delete_stall(f["id"]) is False and db.feature(f["id"])["stall"] is None
    assert all("stall" in x for x in db.features())


# ---- idle means idle ------------------------------------------------------------------------------------------------------------
def test_the_project_must_be_idle_for_the_check_to_run(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend go", ["backend"])
    assert stalls.is_idle(st, proj) is True
    w = db.add_wake("qa", "dm-qa", [], "master")  # something queued
    assert stalls.is_idle(st, proj) is False and asyncio.run(stalls.check_idle(st, proj)) == []
    db.x("DELETE FROM wake_queue")
    st.scheduler.running = {("p1", "qa"): object()}  # a turn running (a role blocked on a question counts too)
    assert stalls.is_idle(st, proj) is False and asyncio.run(stalls.check_idle(st, proj)) == []
    st.scheduler.running = {("other-project", "qa"): object()}  # another project's turn does not matter
    assert stalls.is_idle(st, proj) is True
    st.scheduler.running = {}
    db.set_kv("paused", True)
    assert stalls.is_idle(st, proj) is False and asyncio.run(stalls.check_idle(st, proj)) == []
    db.set_kv("paused", False)
    st.rate_limit_active = lambda: True
    assert stalls.is_idle(st, proj) is False
    st.rate_limit_active = lambda: False
    assert db.stall_rows() == [] and w
    assert asyncio.run(stalls.check_idle(st, proj)) == [{"feature": "F-1", "step": 1, "who": "backend"}]


class Staggered(StubRunner):
    """qa ends first (50 ms), backend later (400 ms): only the second end leaves the project idle."""
    async def run(self):
        self.started.set()
        await asyncio.sleep(0.05 if self.role == "qa" else 0.4)


def test_the_scheduler_checks_once_when_the_last_turn_ends(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    f = feat(db)
    say(db, f, "manager", "@lead-2 look", ["lead-2"])
    calls = []

    async def fake_on_idle(st_, proj_):
        calls.append(sorted(r for (p, r) in st_.scheduler.running))
        return []
    monkeypatch.setattr(stalls, "on_idle", fake_on_idle)
    monkeypatch.setattr(sched_mod, "TurnRunner", Staggered)

    async def go():
        st.settings["max_concurrent"] = 3
        s = sched_mod.Scheduler(st)
        st.scheduler = s
        db.add_wake("qa", "dm-qa", [], "master")
        db.add_wake("backend", "dm-backend", [], "master")
        s._tick()
        await asyncio.gather(*[r.task for r in list(s.running.values())])
    asyncio.run(go())
    assert calls == [[]]  # exactly one check, with nothing left running, after the LAST turn ended


def test_the_scheduler_does_not_check_while_a_wake_is_queued_or_on_shutdown(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    calls = []

    async def fake_on_idle(st_, proj_):
        calls.append(1)
        return []
    monkeypatch.setattr(stalls, "on_idle", fake_on_idle)
    monkeypatch.setattr(sched_mod, "TurnRunner", StubRunner)

    async def go(queue_more, shutdown=False):
        st.settings["max_concurrent"] = 3
        s = sched_mod.Scheduler(st)
        st.scheduler = s
        db.add_wake("qa", "dm-qa", [], "master")
        s._tick()
        runner = s.running[("p1", "qa")]
        if queue_more:
            db.add_wake("frontend", "dm-frontend", [], "master")  # a follow-up wake exists when qa ends
        if shutdown:
            runner.interrupt_reason = "shutdown"
        await runner.task
    asyncio.run(go(True))
    assert calls == []
    db.x("DELETE FROM wake_queue")
    asyncio.run(go(False, shutdown=True))
    assert calls == []


def test_recover_checks_each_project_once_at_start(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    f = feat(db)
    say(db, f, "manager", "@backend go", ["backend"])
    s = sched_mod.Scheduler(st)
    st.scheduler = s
    asyncio.run(s.recover())
    assert db.stall_row(f["id"])["step"] == 1 and [w["role"] for w in wakes(db)] == ["backend"]


# ---- long turns: flagged once, never stopped -----------------------------------------------------------------------------------
def old_wake(db, role, minutes):
    w = db.add_wake(role, f"dm-{role}", [], "system")
    db.x("UPDATE wake_queue SET created_at=? WHERE id=?", time.time() - minutes * 60, w["id"])
    return w


def test_a_wake_queued_too_long_is_flagged_once_and_wakes_the_manager_once(env):
    st, proj, db = env
    old_wake(db, "backend", 25)
    old_wake(db, "qa", 5)  # not long enough
    flagged = set()
    assert asyncio.run(stalls.flag_long_turns(st, proj, flagged)) == ["Backend's turn has been queued for 25m; it was not stopped."]
    line = db.messages("dm-manager")[-1]
    assert (line["author"], line["mentions"], line["text"]) == ("system", ["manager"],
                                                                "Backend's turn has been queued for 25m; it was not stopped.")
    assert [w["role"] for w in wakes(db, "manager")] == ["manager"]
    act = db.activity(1)[0]
    assert (act["kind"], act["role"]) == ("stuck_turn", "backend") and any(e == "team" for e, _ in proj.hub.events)
    assert asyncio.run(stalls.flag_long_turns(st, proj, flagged)) == []  # once each
    assert len(wakes(db, "manager")) == 1 and len([m for m in db.messages("dm-manager") if m["author"] == "system"]) == 1


def test_a_running_turn_is_flagged_but_never_interrupted(env):
    st, proj, db = env
    t = db.create_turn("backend", "s", "master", "dm-backend")
    db.update_turn(t["id"], state="running", started_at=time.time() - 30 * 60)

    class Runner:
        turn = {"id": t["id"]}

        def interrupt(self, *a):
            pytest.fail("a long turn is flagged, never stopped")
    st.scheduler.running = {("p1", "backend"): Runner()}
    lines = asyncio.run(stalls.flag_long_turns(st, proj, set()))
    assert lines == ["Backend's turn has been running for 30m; it was not stopped."]
    assert stalls.is_stuck(st, proj, "backend") is True and stalls.is_stuck(st, proj, "qa") is False
    assert db.turn(t["id"])["state"] == "running"


def test_the_manager_being_slow_is_reported_without_waking_the_manager(env):
    st, proj, db = env
    old_wake(db, "manager", 40)
    assert asyncio.run(stalls.flag_long_turns(st, proj, set())) == ["Manager's turn has been queued for 40m; it was not stopped."]
    line = db.messages("dm-manager")[-1]
    assert line["mentions"] == [] and line["author"] == "system"
    assert [w["role"] for w in wakes(db)] == ["manager"] and wakes(db)[0]["message_ids"] == []  # only the original wake


def test_stuck_turn_s_is_a_setting_and_team_status_carries_stuck(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], stuck_turn_s=60)
    st.scheduler = Notifier()
    old_wake(db, "backend", 2)
    by = {r["role"]: r["stuck"] for r in workflow.team_status(st, proj)}
    assert by["backend"] is True and by["qa"] is False and set(by.values()) == {True, False}
    st.settings["stuck_turn_s"] = 3600
    assert not any(r["stuck"] for r in workflow.team_status(st, proj))
    from aiteams import config
    assert config.DEFAULT_SETTINGS["stuck_turn_s"] == 1200


def test_the_scheduler_flags_long_turns_beside_its_tick_and_skips_paused_or_limited_projects(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    st.settings["max_concurrent"] = 0
    s = sched_mod.Scheduler(st)
    st.scheduler = s
    old_wake(db, "backend", 25)
    db.set_kv("paused", True)
    asyncio.run(s._flag_long())
    assert s.flagged == set()
    db.set_kv("paused", False)
    st.rate_limit_active = lambda: True
    asyncio.run(s._flag_long())
    assert s.flagged == set()
    st.rate_limit_active = lambda: False
    asyncio.run(s._flag_long())
    asyncio.run(s._flag_long())
    assert len(s.flagged) == 1 and len([m for m in db.messages("dm-manager") if m["author"] == "system"]) == 1


def test_filing_a_commit_request_replaces_the_old_wait_at_once(env):
    st, proj, db = env
    f = feat(db)
    say(db, f, "manager", "@backend please file", ["backend"])
    idle(st, proj)
    assert db.stall_row(f["id"])["who"] == "backend"
    (db.project_path and __import__("pathlib").Path(db.project_path) / "a.py").write_text("x = 1\n")
    c = asyncio.run(workflow.request_commit(st, proj, "backend", None, f["key"], ["a.py"], "feat: x"))
    assert c["state"] == "lead_review"
    assert db.stall_row(f["id"]) is None  # the chip no longer says "waiting on Backend": it now waits on the review
    clear_wakes(db)
    assert idle(st, proj) == [{"feature": "F-1", "step": 1, "who": "lead-2"}]
    assert db.stall_row(f["id"])["ref"] == f"commit:{c['id']}"
