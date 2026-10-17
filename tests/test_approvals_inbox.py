"""Approvals (request/decide/set_phase) and the Inbox (questions, approvals, master-stage commits, typed messages)."""
import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def turn(client, pid, role):
    proj = client.app.state.app.projects[pid]
    t = proj.db.create_turn(role, "sess", "master", "general")
    proj.db.update_turn(t["id"], state="running")
    return {"X-AITeams-Project": pid, "X-AITeams-Turn": str(t["id"])}


def test_request_and_approve_with_phase(client, pid):
    proj = client.app.state.app.projects[pid]
    f = client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"}).json()
    proj.db.set_wake_state(proj.db.queued_wakes()[0]["id"], "started")
    product = turn(client, pid, "product")
    r = client.post("/internal/approvals", json={"feature": "feature-1", "title": "Scope lock: Export", "summary": "- csv only"}, headers=product).json()
    assert r == {"id": 1, "state": "pending"}
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert m["type"] == "Approval" and m["ref"] == "approval:1" and m["mentions"] == ["master"] and m["author"] == "product"
    assert m["text"] == "**Approval requested: Scope lock: Export**\n\n- csv only"
    assert proj.db.queued_wakes() == []  # @master wakes nobody
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert inbox["count"] == 1 and inbox["approvals"][0]["feature"] == "F-1" and inbox["approvals"][0]["message_id"] == m["id"]
    assert client.get(f"/api/p/{pid}/features").json()[0]["open_approvals"] == 1
    r = client.post(f"/api/p/{pid}/approvals/1/decide", json={"decision": "approve", "set_phase": "bogus"})
    assert r.status_code == 400 and client.get(f"/api/p/{pid}/approvals").json()[0]["state"] == "pending"
    r = client.post(f"/api/p/{pid}/approvals/1/decide", json={"decision": "approve", "set_phase": "spec", "note": "go"}).json()
    assert r["state"] == "approved" and r["note"] == "go"
    assert client.get(f"/api/p/{pid}/features/{f['id']}").json()["phase"] == "spec"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert last["author"] == "master" and last["type"] == "Decision" and last["mentions"] == ["product"]
    assert "Approved: Scope lock: Export (phase → spec)" in last["text"] and "go" in last["text"]
    assert [w["role"] for w in proj.db.queued_wakes()] == ["product"]
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0
    assert client.post(f"/api/p/{pid}/approvals/1/decide", json={"decision": "approve"}).status_code == 409
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-2]
    assert m["approval"]["state"] == "approved"  # embedded row is live


def test_changes_requested_and_no_feature(client, pid):
    qa = turn(client, pid, "qa")
    r = client.post("/internal/approvals", json={"title": "Skip flaky test?", "summary": "s"}, headers=qa).json()
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert m["ref"] == f"approval:{r['id']}"
    r = client.post(f"/api/p/{pid}/approvals/{r['id']}/decide", json={"decision": "changes", "note": "fix it instead"}).json()
    assert r["state"] == "changes_requested"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert "Changes requested on Skip flaky test?: fix it instead" in last["text"] and last["mentions"] == ["qa"]
    assert client.get(f"/api/p/{pid}/approvals", params={"state": "all"}).json()[0]["state"] == "changes_requested"
    assert client.post(f"/api/p/{pid}/approvals/99/decide", json={"decision": "approve"}).status_code == 404


def test_inbox_messages_and_resolve(client, pid):
    qa = turn(client, pid, "qa")
    hdr = qa
    client.post("/internal/messages", json={"conversation": "general", "text": "@master the test DB is missing", "type": "Blocker"}, headers=hdr)
    client.post("/internal/messages", json={"conversation": "general", "text": "@master which region?", "type": "Question"}, headers=hdr)
    client.post("/internal/messages", json={"conversation": "general", "text": "@master fyi done", "type": "Message"}, headers=hdr)
    client.post("/internal/messages", json={"conversation": "general", "text": "@lead question for you", "type": "Question"}, headers=hdr)
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [m["type"] for m in inbox["messages"]] == ["Question", "Blocker"] and inbox["count"] == 2
    assert inbox["messages"][0]["conversation_title"] == "general"
    blocker = inbox["messages"][1]["id"]
    assert client.post(f"/api/p/{pid}/inbox/resolve", json={"message_id": blocker}).json() == {"ok": True}
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [m["type"] for m in inbox["messages"]] == ["Question"] and inbox["count"] == 1
    assert client.post(f"/api/p/{pid}/inbox/resolve", json={"message_id": 999}).status_code == 404
    # pending AskUserQuestion shows up too
    proj = client.app.state.app.projects[pid]
    proj.db.create_question(int(hdr["X-AITeams-Turn"]), "qa", "t1", {"questions": []})
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 2
