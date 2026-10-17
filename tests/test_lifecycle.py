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


def ended_turn(db, role, state="done", kind="normal", ctx=None, result=None, conv="dm-" + "backend"):
    t = db.create_turn(role, db.active_session(role) or "s", "master", conv, kind)
    return db.update_turn(t["id"], state=state, started_at=1.0, ended_at=2.0, context_tokens=ctx,
                          result_json=json.dumps(result) if result else None)


def test_context_over_threshold_queues_handoff_turn(db, home, monkeypatch):
    st, proj = setup(db, home, monkeypatch, [], rotate_pct=0.5)
    db.new_session("backend", "s1", "first turn")
    t = ended_turn(db, "backend", ctx=600, result={"modelUsage": {"m": {"contextWindow": 1000}}})
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t)))
    rs = db.role_state("backend")
    assert rs["rotate_reason"] == "context 60%" and rs["rotate_handoff"] == 1
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
    monkeypatch.setattr(turn_mod, "build_cmd", lambda s, r, e, f, sid, resume, m: cmds.append((sid, resume)) or fake_cmd())
    t = asyncio.run(TurnRunner(st, proj, hw).run())
    assert t["kind"] == "handoff" and cmds[-1] == ("old-session", True)
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
        runner = s.running[("p1", "backend")]
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
