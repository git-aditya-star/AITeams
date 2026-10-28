"""Approvals (request/decide/set_phase) and the Inbox (questions, approvals, master-stage commits, typed messages)."""
import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from tests.conftest import AUTH, BRIEF, BRIEF_SUMMARY, running_turn


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
    return running_turn(proj.db, role, pid=pid)[1]


def test_request_and_approve_with_phase(client, pid):
    proj = client.app.state.app.projects[pid]
    f = client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"}).json()
    proj.db.set_wake_state(proj.db.queued_wakes()[0]["id"], "started")
    product = turn(client, pid, "product")
    r = client.post("/internal/approvals", json={"feature": "feature-1", "title": "Scope lock: Export", **BRIEF}, headers=product).json()
    assert r == {"id": 1, "state": "pending"}
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert m["type"] == "Approval" and m["ref"] == "approval:1" and m["mentions"] == ["master"] and m["author"] == "product"
    assert m["text"] == "**Approval requested: Scope lock: Export**\n\n" + BRIEF_SUMMARY
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
    r = client.post("/internal/approvals", json={"title": "Skip flaky test?", **BRIEF}, headers=qa).json()
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert m["ref"] == f"approval:{r['id']}"
    r = client.post(f"/api/p/{pid}/approvals/{r['id']}/decide", json={"decision": "changes", "note": "fix it instead"}).json()
    assert r["state"] == "changes_requested"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert "Changes requested on Skip flaky test?: fix it instead" in last["text"] and last["mentions"] == ["qa"]
    assert client.get(f"/api/p/{pid}/approvals", params={"state": "all"}).json()[0]["state"] == "changes_requested"
    assert client.post(f"/api/p/{pid}/approvals/99/decide", json={"decision": "approve"}).status_code == 404


def test_reject_with_and_without_a_note(client, pid):
    """F-25: `reject` closes the approval as `rejected`, tells the requester, and is final."""
    proj = client.app.state.app.projects[pid]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    product, qa = turn(client, pid, "product"), turn(client, pid, "qa")
    a1 = client.post("/internal/approvals", json={"feature": "F-1", "title": "Use skill: acme:deploy", **BRIEF},
                     headers=product).json()["id"]
    a2 = client.post("/internal/approvals", json={"title": "Skip flaky test?", **BRIEF}, headers=qa).json()["id"]
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 2
    proj.db.set_wake_state(proj.db.queued_wakes()[0]["id"], "started")
    r = client.post(f"/api/p/{pid}/approvals/{a1}/decide", json={"decision": "reject", "note": "not now"}).json()
    assert r["state"] == "rejected" and r["note"] == "not now" and r["resolved_at"]
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert last["author"] == "master" and last["type"] == "Decision" and last["mentions"] == ["product"]
    assert last["text"] == "@product Rejected: Use skill: acme:deploy\n\nnot now"
    assert [w["role"] for w in proj.db.queued_wakes()] == ["product"]  # the requester hears about it
    act = proj.db.q("SELECT kind, text FROM activity ORDER BY id DESC LIMIT 1")[0]
    assert (act["kind"], act["text"]) == ("approval", "Rejected: Use skill: acme:deploy")
    # no note, no feature: the Decision goes to the role's DM, with no trailing blank line
    r = client.post(f"/api/p/{pid}/approvals/{a2}/decide", json={"decision": "reject"}).json()
    assert r["state"] == "rejected" and r["note"] is None
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert last["text"] == "@qa Rejected: Skip flaky test?" and last["mentions"] == ["qa"]
    # both left the Inbox; a second decision on either is 409; the list filter knows the new state
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0
    for aid in (a1, a2):
        for d in ("reject", "approve", "changes"):
            assert client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": d}).status_code == 409
    assert [x["id"] for x in client.get(f"/api/p/{pid}/approvals", params={"state": "rejected"}).json()] == [a2, a1]
    assert client.get(f"/api/p/{pid}/approvals", params={"state": "approved"}).json() == []
    assert client.get(f"/api/p/{pid}/approvals", params={"state": "pending"}).json() == []
    assert client.get(f"/api/p/{pid}/approvals", params={"state": "refused"}).status_code == 400
    m = [m for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json() if m.get("approval")][0]
    assert m["approval"]["state"] == "rejected"  # the embedded card row is live


def test_a_bad_decision_is_a_400_that_names_all_three(client, pid):
    qa = turn(client, pid, "qa")
    aid = client.post("/internal/approvals", json={"title": "Skip flaky test?", **BRIEF}, headers=qa).json()["id"]
    for bad in ("rejected", "Reject", "no", ""):
        r = client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": bad})
        assert r.status_code == 400 and r.json()["detail"] == "decision must be approve|changes|reject", bad
    assert client.get(f"/api/p/{pid}/approvals").json()[0]["state"] == "pending"  # nothing changed


def test_reject_never_sets_the_phase_and_changes_requested_keeps_its_words(client, pid):
    proj = client.app.state.app.projects[pid]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    product = turn(client, pid, "product")
    a1 = client.post("/internal/approvals", json={"feature": "F-1", "title": "Scope lock: Export", **BRIEF},
                     headers=product).json()["id"]
    r = client.post(f"/api/p/{pid}/approvals/{a1}/decide", json={"decision": "reject", "set_phase": "build"})
    assert r.json()["state"] == "rejected" and proj.db.feature(1)["phase"] == "intake"
    a2 = client.post("/internal/approvals", json={"feature": "F-1", "title": "Scope lock: Export v2", **BRIEF},
                     headers=product).json()["id"]
    client.post(f"/api/p/{pid}/approvals/{a2}/decide", json={"decision": "changes", "note": "narrower"})
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert last["text"] == "@product Changes requested on Scope lock: Export v2: narrower"
    act = proj.db.q("SELECT text FROM activity ORDER BY id DESC LIMIT 1")[0]["text"]
    assert act == "Changes requested: Scope lock: Export v2"


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
