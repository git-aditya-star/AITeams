"""Rotation at rotate_pct, handoff turns, fresh sessions, fail streak, Stop lines (fake claude child)."""
import asyncio
import json

from aiteams.orchestrator import lifecycle
from aiteams.orchestrator import scheduler as sched_mod
from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.turn import TurnRunner
from aiteams.runtime import AppState, Project
from tests.conftest import FakeHub
from tests.test_claude_proc import ASSISTANT, INIT, RESULT, fake_cmd, fake_env


class FinishedRunner:
    """Stands in for a TurnRunner whose turn already ended; after_turn only reads turn/role/reason."""
    def __init__(self, proj, turn, reason=None, note=None, kind="normal"):
        self.project, self.turn, self.role = proj, turn, turn["role"]
        self.interrupt_reason, self.interrupt_note, self.kind, self.model = reason, note, kind, "opus"


def setup(db, home, monkeypatch, events, **settings):
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env(events))
    st = AppState(7799)
    st.settings.update(settings)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    return st, proj


def ended_turn(db, role, state="done", kind="normal", ctx=None, result=None, conv="dm-" + "backend", error=None):
    t = db.create_turn(role, db.active_session(role) or "s", "master", conv, kind)
    return db.update_turn(t["id"], state=state, started_at=1.0, ended_at=2.0, context_tokens=ctx,
                          result_json=json.dumps(result) if result else None, error=error)


def test_context_over_threshold_queues_handoff_turn(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], rotate_pct=0.5)
    db.new_session("backend", "s1", "first turn")
    ended_turn(db, "backend", ctx=100)  # F-42: a session's first turn never rotates, so this one is the second
    t = ended_turn(db, "backend", ctx=600, result={"modelUsage": {"m": {"contextWindow": 1000}}})
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t)))
    rs = db.role_state("backend")
    assert rs["rotate_reason"] == "context 600 tokens (limit 500)" and rs["rotate_handoff"] == 1
    w = db.queued_wake("backend", "handoff")
    assert w and w["origin"] == "system" and w["conversation_id"] == "dm-backend"
    assert db.queued_wake("backend") is None  # normal wakes never see handoff wakes
    assert ("team", ) == tuple(e for e, _ in proj.hub.events)[-1:]
    # a second over-threshold turn does not queue a second handoff
    t2 = ended_turn(db, "backend", ctx=700, result={"modelUsage": {"m": {"contextWindow": 1000}}})
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t2)))
    assert len([w for w in db.queued_wakes() if w["kind"] == "handoff"]) == 1


def test_under_threshold_no_rotation(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], rotate_pct=0.6)
    db.new_session("qa", "s1", "first turn")
    t = ended_turn(db, "qa", ctx=100, result={"modelUsage": {"m": {"contextWindow": 1000}}})
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t)))
    assert db.role_state("qa")["rotate_reason"] is None and db.queued_wakes() == []


def test_handoff_turn_then_fresh_session_with_handoff_in_prompt(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [INIT, ASSISTANT, RESULT])
    db.new_session("backend", "old-session", "first turn")
    asyncio.run(lifecycle.request_rotation(st, proj, "backend", "context 63%", True))
    hw = db.queued_wake("backend", "handoff")
    # the handoff turn resumes the OLD session and asks for write_handoff
    cmds = []
    monkeypatch.setattr(turn_mod, "build_cmd", lambda s, r, e, f, sid, resume, m, eff=None: cmds.append((sid, resume)) or fake_cmd())
    t = asyncio.run(TurnRunner(st, proj, hw).run())
    assert t["kind"] == "handoff" and cmds[-1] == ("old-session", True)
    # F-9 (QA C4): a handoff turn resuming a session also records the spawned command first
    assert db.turn_events(t["id"])[0]["type"] == "aiteams_cmd"
    assert "write_handoff" in t["prompt"] and "context 63%" in t["prompt"]
    # the role wrote a handoff during that turn (as the MCP tool would)
    from aiteams import workflow
    db.update_turn(t["id"], state="running")
    asyncio.run(workflow.write_handoff(st, proj, "backend", db.turn(t["id"]), "I was on F-1, api.py half done"))
    db.update_turn(t["id"], state="done")
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, db.turn(t["id"]), kind="handoff")))
    rs = db.role_state("backend")
    assert rs["rotate_reason"] == "context 63%" and rs["rotate_handoff"] == 0
    dm = db.messages("dm-backend")
    assert [m["type"] for m in dm] == ["Handoff"] and dm[0]["author"] == "backend"
    h = db.latest_handoff("backend")
    assert h["seq"] == 1 and h["path"].endswith("/.aiteams/handoffs/backend/1.md")
    # next normal wake: fresh session, handoff at the top of the prompt, System line
    m = db.add_message("dm-backend", "master", "Message", "continue", [], None)
    w = db.add_wake("backend", "dm-backend", [m["id"]], "master")
    t2 = asyncio.run(TurnRunner(st, proj, w).run())
    assert cmds[-1][1] is False and cmds[-1][0] != "old-session"
    assert t2["session_id"] == db.active_session("backend") != "old-session"
    assert t2["prompt"].startswith("# Fresh session #2 — handoff from your previous session")
    assert "I was on F-1, api.py half done" in t2["prompt"] and "# New messages for Backend" in t2["prompt"]
    info = db.session_info("backend")
    assert info["seq"] == 2 and info["fresh_starts"] == 1 and info["reason"] == "context 63%"
    assert db.role_state("backend")["rotate_reason"] is None
    sys_lines = [x["text"] for x in db.messages("dm-backend") if x["author"] == "system"]
    assert sys_lines == ["Fresh session #2 (context 63%)"]
    assert any(a["kind"] == "fresh_session" for a in db.activity())
    fresh = [d for e, d in proj.hub.events if e == "activity" and d["kind"] == "fresh_session"]
    assert len(fresh) == 1 and fresh[0]["role"] == "backend" and fresh[0]["conversation_id"] == "dm-backend"
    stored = [a for a in db.activity() if a["kind"] == "fresh_session"]
    assert len(stored) == 1 and stored[0]["conversation_id"] == "dm-backend"


def test_handoff_turn_without_write_handoff_posts_note(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("lead", "s1", "first turn")
    asyncio.run(lifecycle.request_rotation(st, proj, "lead", "fresh start", True))
    t = ended_turn(db, "lead", kind="handoff")
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, kind="handoff")))
    assert db.role_state("lead") == {**db.role_state("lead"), "rotate_reason": "fresh start", "rotate_handoff": 0}
    assert "without `write_handoff`" in db.messages("dm-lead")[-1]["text"]


def test_rotation_without_handoff_is_instant(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [INIT, ASSISTANT, RESULT])
    db.new_session("qa", "s1", "first turn")
    assert asyncio.run(lifecycle.request_rotation(st, proj, "qa", "checkpoint: looping", False)) is True
    assert asyncio.run(lifecycle.request_rotation(st, proj, "qa", "again", True)) is False  # already pending
    assert db.queued_wakes() == []
    m = db.add_message("dm-qa", "master", "Message", "go", [], None)
    t = asyncio.run(TurnRunner(st, proj, db.add_wake("qa", "dm-qa", [m["id"]], "master")).run())
    assert t["session_id"] != "s1" and not t["prompt"].startswith("# Fresh session")
    assert db.messages("dm-qa")[-1]["text"] == "Fresh session #2 (checkpoint: looping)"


def test_fail_streak_rotates_without_handoff(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], fail_streak_rotate=3)
    db.new_session("frontend", "s1", "first turn")
    for i in range(2):
        asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, ended_turn(db, "frontend", "failed"))))
    assert db.role_state("frontend")["fail_streak"] == 2 and db.role_state("frontend")["rotate_reason"] is None
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, ended_turn(db, "frontend", "done"))))
    assert db.role_state("frontend")["fail_streak"] == 0
    for i in range(3):
        asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, ended_turn(db, "frontend", "failed"))))
    rs = db.role_state("frontend")
    assert rs["rotate_reason"] == "errors" and rs["rotate_handoff"] == 0 and rs["fail_streak"] == 0
    assert db.queued_wakes() == []
    assert "3 failed turns in a row" in db.messages("dm-frontend")[-1]["text"]


def test_stop_lines_and_activity(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("backend", "s1", "first turn")
    t = ended_turn(db, "backend", "interrupted")
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, "master")))
    t2 = ended_turn(db, "backend", "interrupted")
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t2, "manager", "runaway loop")))
    texts = [m["text"] for m in db.messages("dm-backend")]
    assert texts == ["Turn stopped by the Master.", "Turn interrupted by the Manager: runaway loop"]
    assert [a["kind"] for a in db.activity()] == ["interrupted", "stopped"]
    frames = [d for e, d in proj.hub.events if e == "activity"]
    assert [(d["kind"], d["role"], d["conversation_id"]) for d in frames] == [
        ("stopped", "backend", "dm-backend"), ("interrupted", "backend", "dm-backend")]
    assert frames[1]["text"] == "Manager interrupted backend: runaway loop"
    assert db.role_state("backend")["rotate_reason"] is None


def test_scheduler_stop_and_handoff_first(db, home, monkeypatch):
    from tests.test_scheduler import StubRunner
    monkeypatch.setattr(sched_mod, "TurnRunner", StubRunner)

    class Stoppable(StubRunner):
        def __init__(self, *a):
            super().__init__(*a)
            self.kind = self.wake.get("kind", "normal")

        def interrupt(self, reason, note=None):
            self.interrupt_reason, self.interrupt_note = reason, note

    monkeypatch.setattr(sched_mod, "TurnRunner", Stoppable)

    async def go():
        st = AppState(7799)
        st.settings["max_concurrent"] = 3
        proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
        proj.hub = FakeHub()
        st.projects["p1"] = proj
        s = sched_mod.Scheduler(st)
        st.scheduler = s
        assert s.stop("p1", "backend", "master") is False
        m = proj.db.add_message("dm-backend", "master", "Message", "hi", [], None)
        normal = proj.db.add_wake("backend", "dm-backend", [m["id"]], "master")
        handoff = proj.db.add_wake("backend", "dm-backend", [], "system", kind="handoff")
        s._tick()
        runner = s.running[("p1", "backend", "")]
        assert runner.wake["id"] == handoff["id"]  # handoff goes first even though the normal wake is older and master-origin
        assert s.stop("p1", "backend", "master") is True and runner.interrupt_reason == "master"
        await asyncio.gather(*[r.task for r in s.running.values()])
        return proj.db.wake(normal["id"])["state"]

    assert asyncio.run(go()) == "queued"


def test_shutdown_interrupt_skips_bookkeeping(db, home, monkeypatch):
    """`aiteams stop` mid-handoff: rotate_handoff stays 1 so the scheduler re-queues the handoff wake."""
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("lead", "s1", "first turn")
    asyncio.run(lifecycle.request_rotation(st, proj, "lead", "context 70%", True))
    t = ended_turn(db, "lead", "interrupted", kind="handoff")
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, "shutdown", kind="handoff")))
    assert db.role_state("lead")["rotate_handoff"] == 1 and db.messages("dm-lead") == []


def test_standup_turn_does_not_consume_rotation(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [INIT, ASSISTANT, RESULT])
    db.new_session("backend", "old", "first turn")
    asyncio.run(lifecycle.request_rotation(st, proj, "backend", "checkpoint: x", False))
    su = db.create_standup(["backend"], "master")
    t = asyncio.run(TurnRunner(st, proj, db.add_wake("backend", "standup", [], "master", kind="standup", ref=su["id"])).run())
    assert t["session_id"] == "old" and db.role_state("backend")["rotate_reason"] == "checkpoint: x"
    m = db.add_message("dm-backend", "master", "Message", "go", [], None)
    t2 = asyncio.run(TurnRunner(st, proj, db.add_wake("backend", "dm-backend", [m["id"]], "master")).run())
    assert t2["session_id"] != "old" and db.role_state("backend")["rotate_reason"] is None


# ---- F-37: a normal turn that stopped at the --max-turns cap is continued, up to a limit ----------------------------------
CAPPED = "max turns reached"


class Notifier:
    running: dict = {}

    def __init__(self):
        self.notified = 0

    def notify(self):
        self.notified += 1


def capped(db, st, proj, role="backend", conv="dm-backend", **kw):
    t = ended_turn(db, role, error=CAPPED, conv=conv, **kw)
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t)))
    return t


def test_a_capped_turn_posts_a_system_line_and_wakes_the_role_again(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], max_turns=50, max_turn_continues=2)
    st.scheduler = Notifier()
    db.new_session("backend", "s1", "first turn")
    f = db.create_feature("Export")
    capped(db, st, proj, conv=f["conversation_id"])
    line = db.messages(f["conversation_id"])[-1]
    assert (line["author"], line["type"], line["mentions"]) == ("system", "System", ["backend"])
    assert line["text"] == ("Backend's turn stopped at the 50-step limit before it finished; continuing automatically (1 of 2). "
                            "Backend: check your files and this chat, finish what you were doing, then file or report.")
    wakes = db.queued_wakes()
    assert len(wakes) == 1 and wakes[0]["role"] == "backend" and wakes[0]["kind"] == "normal"
    assert (wakes[0]["origin"], wakes[0]["conversation_id"], wakes[0]["message_ids"]) == ("system", f["conversation_id"], [line["id"]])
    act = db.activity(1)[0]
    assert (act["kind"], act["role"], act["conversation_id"]) == ("max_turns", "backend", f["conversation_id"])
    assert act["text"].startswith("Backend's turn stopped at the 50-step limit")
    events = [e for e, _ in proj.hub.events]
    assert "message" in events and "activity" in events and "receipt" in events
    assert st.scheduler.notified == 1
    assert db.role_state("backend")["fail_streak"] == 0  # a capped turn is not a failure


def test_the_continuation_merges_into_a_queued_wake_instead_of_adding_one(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("backend", "s1", "first turn")
    m = db.add_message("dm-backend", "master", "Message", "also this", [], None)
    queued = db.add_wake("backend", "dm-backend", [m["id"]], "master")
    capped(db, st, proj)
    wakes = db.queued_wakes()
    assert [w["id"] for w in wakes] == [queued["id"]]  # no second wake
    line = db.messages("dm-backend")[-1]
    assert wakes[0]["message_ids"] == [m["id"], line["id"]] and wakes[0]["origin"] == "master"  # keeps the Master's priority


def test_the_default_limit_is_100_steps_and_two_continues(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("backend", "s1", "first turn")
    capped(db, st, proj)
    assert "stopped at the 100-step limit" in db.messages("dm-backend")[-1]["text"] and "(1 of 2)" in db.messages("dm-backend")[-1]["text"]


def test_after_the_limit_the_role_is_not_woken_and_the_manager_is(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], max_turns=50, max_turn_continues=2)
    db.new_session("backend", "s1", "first turn")
    for i in (1, 2):
        capped(db, st, proj)
        assert f"continuing automatically ({i} of 2)" in db.messages("dm-backend")[-1]["text"]
        db.x("DELETE FROM wake_queue")  # the role ran its continuation and capped again
    capped(db, st, proj)
    line = db.messages("dm-backend")[-1]
    assert line["text"] == "Backend hit the 50-step limit 3 turns in a row; not continuing automatically. @manager please check."
    assert (line["author"], line["mentions"]) == ("system", ["manager"])
    assert db.queued_wake("backend") is None
    w = db.queued_wake("manager")
    assert w["message_ids"] == [line["id"]] and w["origin"] == "system" and w["conversation_id"] == "dm-backend"
    assert db.activity(1)[0]["kind"] == "max_turns" and "3 turns in a row" in db.activity(1)[0]["text"]
    capped(db, st, proj)  # a fourth: still the Manager, one merged wake
    assert [w["role"] for w in db.queued_wakes()] == ["manager"] and len(db.queued_wake("manager")["message_ids"]) == 2


def test_the_manager_hitting_the_cap_too_often_is_reported_to_the_master_and_wakes_nobody(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], max_turn_continues=1)
    db.new_session("manager", "s1", "first turn")
    for _ in range(2):
        capped(db, st, proj, role="manager", conv="dm-manager")
    db.x("DELETE FROM wake_queue")
    capped(db, st, proj, role="manager", conv="dm-manager")
    line = db.messages("dm-manager")[-1]
    assert line["text"].endswith("3 turns in a row; not continuing automatically. @master please check.") and line["mentions"] == ["master"]
    assert db.queued_wakes() == []  # the Manager's stall is not handed to the Manager


def test_an_ordinary_turn_between_capped_ones_resets_the_count(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], max_turn_continues=2)
    db.new_session("backend", "s1", "first turn")
    capped(db, st, proj)
    capped(db, st, proj)
    assert "(2 of 2)" in db.messages("dm-backend")[-1]["text"]
    for state in ("done", "failed", "interrupted"):  # anything that is not a capped turn ends the streak
        db.x("DELETE FROM wake_queue")
        asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, ended_turn(db, "backend", state))))
        capped(db, st, proj)
        assert "(1 of 2)" in db.messages("dm-backend")[-1]["text"], state
        capped(db, st, proj)  # and it counts up again from there
        assert "(2 of 2)" in db.messages("dm-backend")[-1]["text"], state


def test_cap_streak_counts_only_this_roles_ended_normal_turns(db):
    db.new_session("backend", "s1", "first turn")
    assert db.cap_streak("backend") == 0
    ended_turn(db, "backend", error=CAPPED)
    ended_turn(db, "qa", state="done")  # another role's turn does not interrupt it
    ended_turn(db, "backend", kind="handoff", error=CAPPED)  # not a normal turn: neither counted nor breaking
    ended_turn(db, "backend", error=CAPPED)
    running = ended_turn(db, "backend", state="running")  # an unfinished turn is ignored
    assert db.cap_streak("backend") == 2 and db.cap_streak("qa") == 0 and db.turn(running["id"])["state"] == "running"
    ended_turn(db, "backend", state="failed", error=CAPPED)  # only a DONE turn with the cap error counts
    assert db.cap_streak("backend") == 0
    ended_turn(db, "backend", error=CAPPED)
    assert db.cap_streak("backend") == 1


def test_turns_that_are_not_continued(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [])
    db.new_session("backend", "s1", "first turn")
    # stopped by the Master or the Manager: the turn is interrupted, the Stop line stays the only line
    for reason in ("master", "manager"):
        t = ended_turn(db, "backend", state="interrupted")
        asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, reason=reason)))
    # even if the turn row says capped, an interrupt reason wins
    t = ended_turn(db, "backend", error=CAPPED)
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, reason="master")))
    # a capped handoff, standup, brainstorm turn; a successful turn; a failed one
    for kind in ("handoff", "standup", "brainstorm"):
        t = ended_turn(db, "backend", kind=kind, error=CAPPED)
        asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, kind=kind)))
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, ended_turn(db, "backend", "done"))))
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, ended_turn(db, "backend", "failed", error="boom"))))
    assert db.queued_wakes() == []
    assert not [m for m in db.messages("dm-backend") if "step limit" in m["text"]]
    assert not [a for a in db.activity() if a["kind"] == "max_turns"]


def test_rotation_and_the_cap_together_run_the_handoff_wake_first(db, home, monkeypatch):
    from tests.test_scheduler import StubRunner
    st, proj = setup(db, home, monkeypatch, [], rotate_pct=0.5)
    db.new_session("backend", "s1", "first turn")
    ended_turn(db, "backend", ctx=100)  # F-42: not the session's first turn
    capped(db, st, proj, ctx=700, result={"modelUsage": {"m": {"contextWindow": 1000}}})
    rs = db.role_state("backend")
    assert rs["rotate_reason"] == "context 700 tokens (limit 500)" and rs["rotate_handoff"] == 1
    handoff, cont = db.queued_wake("backend", "handoff"), db.queued_wake("backend")
    assert handoff and cont and cont["message_ids"] == [db.messages("dm-backend")[-1]["id"]]  # both are queued
    monkeypatch.setattr(sched_mod, "TurnRunner", StubRunner)

    async def first_running():
        st.settings["max_concurrent"] = 3
        s = sched_mod.Scheduler(st)
        st.scheduler = s
        s._tick()
        runner = s.running[("p1", "backend", "")]
        wake = runner.wake
        await asyncio.gather(*[r.task for r in s.running.values()])
        return wake
    assert asyncio.run(first_running())["id"] == handoff["id"]  # the handoff runs first; the continuation then starts the fresh session


def test_a_real_capped_turn_is_recorded_as_done_and_continues(db, home, monkeypatch):
    """The whole path: claude ends with error_max_turns, TurnRunner stores state done + "max turns reached", after_turn continues."""
    st, proj = setup(db, home, monkeypatch, [INIT, ASSISTANT, {**RESULT, "subtype": "error_max_turns", "is_error": False}],
                     max_turns=7)
    m = db.add_message("dm-backend", "master", "Message", "build it", [], None)
    w = db.add_wake("backend", "dm-backend", [m["id"]], "master")
    t = asyncio.run(TurnRunner(st, proj, w).run())
    assert (t["state"], t["error"]) == ("done", "max turns reached")
    db.set_wake_state(w["id"], "done")
    runner = FinishedRunner(proj, t)
    asyncio.run(lifecycle.after_turn(st, proj, runner))
    line = db.messages("dm-backend")[-1]
    assert "stopped at the 7-step limit" in line["text"] and "(1 of 2)" in line["text"]
    assert db.queued_wake("backend")["message_ids"] == [line["id"]]


def test_the_new_defaults_load_and_a_pinned_value_wins(home):
    from aiteams import config
    assert config.DEFAULT_SETTINGS["max_turns"] == 100 and config.DEFAULT_SETTINGS["max_turn_continues"] == 2
    s = config.load_settings()
    assert s["max_turns"] == 100 and s["max_turn_continues"] == 2
    (home / "settings.json").write_text(json.dumps({"max_concurrent": 0, "max_turns": 40}))  # the live file pins 40
    assert config.load_settings()["max_turns"] == 40 and config.load_settings()["max_turn_continues"] == 2
    saved = config.save_settings({"max_turn_continues": 3, "not_a_setting": 1})
    assert saved["max_turn_continues"] == 3 and "not_a_setting" not in saved
    assert config.load_settings()["max_turn_continues"] == 3 and config.load_settings()["max_turns"] == 40


def test_system_wake_posts_a_system_line_and_queues_or_merges_a_normal_wake(db, home, monkeypatch):
    """F-37: the reusable step (F-38 calls it too): System line mentioning the role + a normal wake with origin system."""
    st, proj = setup(db, home, monkeypatch, [])
    st.scheduler = Notifier()
    f = db.create_feature("Export")
    conv = f["conversation_id"]
    msg = asyncio.run(lifecycle.system_wake(st, proj, "qa", conv, "Please look again.", 7))
    assert (msg["author"], msg["type"], msg["mentions"], msg["text"], msg["turn_id"]) == ("system", "System", ["qa"], "Please look again.", 7)
    w = db.queued_wake("qa")
    assert (w["kind"], w["origin"], w["conversation_id"], w["message_ids"]) == ("normal", "system", conv, [msg["id"]])
    assert st.scheduler.notified == 1 and [e for e, d in proj.hub.events if e == "message"][-1:] == ["message"]
    assert ("receipt", {"message_id": msg["id"], "role": "qa", "state": "queued"}) in proj.hub.events
    second = asyncio.run(lifecycle.system_wake(st, proj, "qa", "dm-qa", "And this.", None))  # merges, one wake
    wakes = [x for x in db.queued_wakes() if x["role"] == "qa"]
    assert len(wakes) == 1 and wakes[0]["message_ids"] == [msg["id"], second["id"]] and wakes[0]["conversation_id"] == "dm-qa"
    assert st.scheduler.notified == 2
    st.scheduler = None  # no scheduler (tests, shutdown): nothing to notify, nothing fails
    asyncio.run(lifecycle.system_wake(st, proj, "frontend", "dm-frontend", "x"))
    assert db.queued_wake("frontend")
