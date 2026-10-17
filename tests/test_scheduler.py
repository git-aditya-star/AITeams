"""Scheduler: a wake taken by _tick can no longer be merged into (lost-update race)."""
import asyncio

from aiteams.orchestrator import scheduler as sched_mod
from aiteams.orchestrator.router import parse_mentions, route
from aiteams.runtime import AppState, Project
from tests.conftest import FakeHub


class StubRunner:
    def __init__(self, state, project, wake):
        self.state, self.project, self.wake, self.db = state, project, wake, project.db
        self.role, self.turn, self.interrupt_reason, self.task = wake["role"], None, None, None
        self.started = asyncio.Event()

    async def run(self):
        self.started.set()
        await asyncio.sleep(0.2)


def test_tick_marks_wake_started_before_a_concurrent_merge(db, home, monkeypatch):
    monkeypatch.setattr(sched_mod, "TurnRunner", StubRunner)

    async def go():
        st = AppState(7799)
        st.settings["max_concurrent"] = 3
        proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
        proj.hub = FakeHub()
        st.projects["p1"] = proj
        s = sched_mod.Scheduler(st)
        st.scheduler = s
        m1 = proj.db.add_message("general", "lead", "Message", "@backend A", ["backend"], None)
        w1 = route(proj.db, m1, proj.db.conversation("general"))[0]
        s._tick()  # takes w1 synchronously; the runner task has not run yet
        m2 = proj.db.add_message("general", "lead", "Message", "@backend B", parse_mentions("@backend B"), None)
        w2 = route(proj.db, m2, proj.db.conversation("general"))[0]
        runner = s.running[("p1", "backend")]
        await asyncio.wait_for(runner.started.wait(), 2)
        await asyncio.gather(*[r.task for r in s.running.values()])
        return w1, w2, runner.wake

    w1, w2, taken = asyncio.run(go())
    assert taken["id"] == w1["id"] and taken["message_ids"] == w1["message_ids"] == [1]
    assert w2["id"] != w1["id"] and w2["message_ids"] == [2] and w2["state"] == "queued"
    assert db.wake(w1["id"])["state"] == "started"
    assert db.wake(w1["id"])["message_ids"] == [1]
