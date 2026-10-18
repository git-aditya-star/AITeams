"""Standup fan-out: nothing-changed, wake active roles only, compile wake, save, failure, permissions."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn
from aiteams.orchestrator import lifecycle
from tests.test_lifecycle import FinishedRunner


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def turn(client, pid, role, kind="normal", wake=None):
    proj = client.app.state.app.projects[pid]
    return running_turn(proj.db, role, "general", kind, wake["id"] if wake else None, pid=pid)


def ended(db, role, state="done"):
    t = db.create_turn(role, "s", "master", "general")
    return db.update_turn(t["id"], state=state, started_at=time.time(), ended_at=time.time())


def test_nothing_changed(client, pid):
    r = client.post(f"/api/p/{pid}/standup").json()
    assert r["status"] == "nothing_changed"
    proj = client.app.state.app.projects[pid]
    assert "nothing changed since the start" in proj.db.messages("standup")[-1]["text"]
    assert proj.db.queued_wakes() == [] and proj.db.current_standup() is None


def test_fan_out_compile_and_save(client, pid):
    st = client.app.state.app
    proj = st.projects[pid]
    db = proj.db
    ended(db, "backend"); ended(db, "qa", "failed")  # product/lead/frontend idle
    r = client.post(f"/api/p/{pid}/standup").json()
    assert r["status"] == "collecting" and r["roles"] == ["backend", "qa"] and r["requested_by"] == "master"
    wakes = db.queued_wakes()
    assert sorted((w["role"], w["kind"], w["ref"], w["origin"]) for w in wakes) == [
        ("backend", "standup", r["id"], "master"), ("qa", "standup", r["id"], "master")]
    assert db.queued_wake("backend") is None  # normal routing never merges into a standup wake
    line = db.messages("standup")[-1]
    assert line["author"] == "system" and "waiting for backend, qa" in line["text"]
    assert db.standup(r["id"])["start_message_id"] == line["id"]
    assert client.post(f"/api/p/{pid}/standup").status_code == 409

    # backend's standup turn ends: still collecting; qa's ends (failed): compile wake for the manager
    for w in wakes:
        db.set_wake_state(w["id"], "started")
        t = db.create_turn(w["role"], "s", "master", "standup", "standup")
        db.update_turn(t["id"], state="done" if w["role"] == "backend" else "failed", wake_id=w["id"], ended_at=time.time())
        asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, db.turn(t["id"]), kind="standup")))
        if w["role"] == "backend":
            assert db.standup(r["id"])["state"] == "collecting"
    su = db.standup(r["id"])
    assert su["state"] == "compiling"
    cw = db.queued_wake("manager", "standup_compile")
    assert cw and cw["ref"] == su["id"] and cw["origin"] == "system"

    # the manager's compile turn saves the report
    db.add_message("standup", "backend", "Message", "**Done** api\n**Next** tests\n**Blocked** nothing", [], None)
    db.set_wake_state(cw["id"], "started")
    mt, headers = turn(client, pid, "manager", "standup_compile", cw)
    assert client.post("/internal/standup/save", json={"text": "## Report\n- F-1 on track"}, headers=headers).status_code == 200
    su = db.standup(r["id"])
    assert su["state"] == "done" and su["path"].endswith(".md") and su["ended_at"]
    msg = db.messages("standup")[-1]
    assert msg["type"] == "Standup" and msg["author"] == "manager" and msg["mentions"] == ["master"]
    assert db.queued_wakes() == []  # @master wakes nobody
    rep = client.get(f"/api/p/{pid}/standups/{su['id']}/report").json()
    assert "- F-1 on track" in rep["text"] and rep["state"] == "done"
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0  # Standup is not an Inbox type
    assert [a["kind"] for a in db.activity()][0] == "standup"
    assert client.get(f"/api/p/{pid}/standups").json()[0]["id"] == su["id"]
    # the next standup only counts turns after this one
    assert client.post(f"/api/p/{pid}/standup").json()["status"] == "nothing_changed"


def test_compile_turn_without_save_fails_standup(client, pid):
    st = client.app.state.app
    proj = st.projects[pid]
    db = proj.db
    ended(db, "lead")
    r = client.post(f"/api/p/{pid}/standup").json()
    w = db.queued_wakes()[0]
    t = db.create_turn("lead", "s", "master", "standup", "standup")
    db.update_turn(t["id"], state="done", wake_id=w["id"], ended_at=time.time())
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, db.turn(t["id"]), kind="standup")))
    cw = db.queued_wake("manager", "standup_compile")
    ct = db.create_turn("manager", "s", "system", "standup", "standup_compile")
    db.update_turn(ct["id"], state="done", wake_id=cw["id"], ended_at=time.time())
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, db.turn(ct["id"]), kind="standup_compile")))
    assert db.standup(r["id"])["state"] == "failed"
    assert "did not save a report" in db.messages("standup")[-1]["text"]


def test_permissions(client, pid):
    proj = client.app.state.app.projects[pid]
    _, backend = turn(client, pid, "backend")
    _, manager = turn(client, pid, "manager")
    assert client.post("/internal/standup", headers=backend).status_code == 403
    assert client.post("/internal/standup/save", json={"text": "x"}, headers=backend).status_code == 403
    assert client.post("/internal/standup/save", json={"text": "x"}, headers=manager).status_code == 409
    assert client.post("/internal/interrupt", json={"role": "qa", "reason": "x"}, headers=backend).status_code == 403
    assert client.post("/internal/interrupt", json={"role": "manager", "reason": "x"}, headers=manager).status_code == 400
    assert client.post("/internal/interrupt", json={"role": "qa", "reason": "x"}, headers=manager).json() == {"ok": False, "running": False}
    assert client.post("/internal/checkpoint", json={"role": "qa", "reason": "loops", "handoff": False}, headers=manager).json()["rotate_reason"] == "checkpoint: loops"
    assert client.post("/internal/checkpoint", json={"role": "qa", "reason": "again"}, headers=manager).status_code == 409
    assert "checkpoint (loops)" in proj.db.messages("dm-qa")[-1]["text"]
    assert client.post("/internal/standup", headers=manager).json()["status"] == "nothing_changed"
    team = client.get("/internal/team", headers=manager).json()["team"]
    assert len(team) == 7 and team[6].startswith("qa: ") and "rotation pending (checkpoint: loops)" in team[6]
    # write_handoff works for any role in any turn
    r = client.post("/internal/handoff", json={"text": "notes"}, headers=backend).json()
    assert r["path"] == ".aiteams/handoffs/backend/1.md"
    assert client.get(f"/api/p/{pid}/handoffs", params={"role": "backend"}).json()[0]["text"].endswith("notes\n")


def test_master_stop_and_fresh_start_routes(client, pid):
    proj = client.app.state.app.projects[pid]
    assert client.post(f"/api/p/{pid}/team/backend/stop").status_code == 409
    assert client.post(f"/api/p/{pid}/team/nobody/stop").status_code == 404
    proj.db.new_session("backend", "s1", "first turn")
    r = client.post(f"/api/p/{pid}/team/backend/fresh-start", json={"handoff": True}).json()
    assert r["rotate_reason"] == "fresh start" and r["rotate_handoff"] == 1
    assert proj.db.queued_wake("backend", "handoff")
    assert client.post(f"/api/p/{pid}/team/backend/fresh-start", json={"handoff": True}).status_code == 409
    r = client.post(f"/api/p/{pid}/team/qa/fresh-start", json={"handoff": False}).json()
    assert r["rotate_handoff"] == 0 and proj.db.queued_wake("qa", "handoff") is None
    t = client.get(f"/api/p/{pid}/team").json()["roles"]
    be = next(x for x in t if x["role"] == "backend")
    assert be["rotation"] == "fresh start" and be["rotation_handoff_pending"] is True and be["state"] == "queued"


def test_standup_prompt_shows_no_cost(client, pid):
    from aiteams.orchestrator.prompt import build_standup_prompt
    db = client.app.state.app.projects[pid].db
    t = ended(db, "backend")
    db.update_turn(t["id"], cost_usd=1.23)
    text = build_standup_prompt(db, "backend", {"id": 1, "requested_by": "master", "created_at": 0})
    assert "— done" in text and "$" not in text
