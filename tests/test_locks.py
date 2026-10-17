"""Locks: acquire/deny/same-role, release at the end of Scheduler._run, force-unlock rules, Master release."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from aiteams.orchestrator import scheduler as sched_mod
from aiteams.orchestrator.router import route
from aiteams.runtime import AppState, Project
from tests.conftest import AUTH, FakeHub, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def turn(client, pid, role):
    proj = client.app.state.app.projects[pid]
    return proj, running_turn(proj.db, role, pid=pid)[1]


def test_acquire_and_deny(client, pid):
    proj, backend = turn(client, pid, "backend")
    _, frontend = turn(client, pid, "frontend")
    r = client.post("/internal/locks/acquire", json={"path": "api/reports.py"}, headers=backend).json()
    assert r == {"ok": True, "path": "api/reports.py"}
    r = client.post("/internal/locks/acquire", json={"path": "./api/reports.py"}, headers=frontend).json()
    assert r["ok"] is False and r["holder"] == "backend" and r["since"] > 0
    assert client.post("/internal/locks/acquire", json={"path": "api/reports.py"}, headers=backend).json()["ok"] is True
    assert client.post("/internal/locks/acquire", json={"path": "/etc/passwd"}, headers=backend).status_code == 400
    assert client.post("/internal/locks/acquire", json={"path": ".aiteams/features/F-1/prd.md"}, headers=backend).json()["ok"] is True
    locks = client.get(f"/api/p/{pid}/locks").json()
    assert {l["path"]: l["role"] for l in locks} == {"api/reports.py": "backend", ".aiteams/features/F-1/prd.md": "backend"}
    assert proj.db.file_edits_for("api/")["api/reports.py"]["role"] == "backend"


def test_release_rules(client, pid):
    proj, backend = turn(client, pid, "backend")
    _, frontend = turn(client, pid, "frontend")
    _, lead = turn(client, pid, "lead")
    client.post("/internal/locks/acquire", json={"path": "a.py"}, headers=backend)
    # another role cannot release, even with force
    assert client.post("/internal/locks/release", json={"path": "a.py", "force": True}, headers=frontend).status_code == 403
    # own release
    assert client.post("/internal/locks/release", json={"path": "a.py"}, headers=backend).json()["released"] is True
    assert client.post("/internal/locks/release", json={"path": "a.py"}, headers=backend).json()["released"] is False
    # lead force-release posts to the holder's DM and wakes them
    client.post("/internal/locks/acquire", json={"path": "a.py"}, headers=backend)
    assert client.post("/internal/locks/release", json={"path": "a.py"}, headers=lead).status_code == 403
    assert client.post("/internal/locks/release", json={"path": "a.py", "force": True}, headers=lead).json()["released"] is True
    dm = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-backend"}).json()
    assert dm[-1]["author"] == "lead" and "force-unlocked `a.py`" in dm[-1]["text"] and dm[-1]["mentions"] == ["backend"]
    assert [w["role"] for w in proj.db.queued_wakes()] == ["backend"]
    # Master release
    client.post("/internal/locks/acquire", json={"path": "b.py"}, headers=backend)
    assert client.delete(f"/api/p/{pid}/locks", params={"path": "b.py"}).json()["released"] is True
    assert client.get(f"/api/p/{pid}/locks").json() == []
    dm = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-backend"}).json()
    assert dm[-1]["type"] == "System" and "released your lock on `b.py`" in dm[-1]["text"]


class StubRunner:
    def __init__(self, state, project, wake):
        self.state, self.project, self.wake, self.db = state, project, wake, project.db
        self.role, self.turn, self.interrupt_reason, self.task = wake["role"], None, None, None

    async def run(self):
        self.db.acquire_lock("x.py", self.role, 1)
        assert self.db.locks()


def test_locks_released_when_turn_ends(db, home, monkeypatch):
    monkeypatch.setattr(sched_mod, "TurnRunner", StubRunner)

    async def go():
        st = AppState(7799)
        st.settings["max_concurrent"] = 3
        proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
        proj.hub = FakeHub()
        st.projects["p1"] = proj
        s = sched_mod.Scheduler(st)
        st.scheduler = s
        db.acquire_lock("other.py", "qa", 99)  # someone else's lock survives
        m = proj.db.add_message("general", "lead", "Message", "@backend A", ["backend"], None)
        route(proj.db, m, proj.db.conversation("general"))
        s._tick()
        await asyncio.gather(*[r.task for r in s.running.values()])
        return proj.hub.events

    events = asyncio.run(go())
    assert [l["path"] for l in db.locks()] == ["other.py"]
    assert ("locks", {"locks": [{"path": "other.py", "role": "qa", "turn_id": 99, "created_at": db.locks()[0]["created_at"]}]}) in events
