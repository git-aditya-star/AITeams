"""F-21: request_commit waits for an approved `Design:` approval on a gated feature; design state in the header."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn


@pytest.fixture(autouse=True)
def design_gate_off():
    """Override of conftest's fixture: here the gate is real (migration 8 starts it when a project DB is created)."""
    yield


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def new_feature(client, pid, title="Export"):
    return client.post(f"/api/p/{pid}/features", json={"text": title, "starter": "product"}).json()


def file_commit(client, pid, role="backend", feature="F-1", name="a.py"):
    proj = proj_of(client, pid)
    (Path(proj.path) / name).write_text("x\n")
    _, hdr = running_turn(proj.db, role, pid=pid)
    body = {"files": [name], "message": "add"} | ({"feature": feature} if feature else {})
    return client.post("/internal/commits", json=body, headers=hdr)


def design_approval(client, pid, title="Design: Export", role="lead"):
    proj = proj_of(client, pid)
    _, hdr = running_turn(proj.db, role, pid=pid)
    r = client.post("/internal/approvals", json={"feature": "F-1", "title": title, "summary": "d.md"}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def decide(client, pid, aid, decision):
    r = client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": decision, "note": "ok"})
    assert r.status_code == 200, r.text


def test_new_feature_without_a_design_approval_is_refused(client, pid):
    new_feature(client, pid)
    r = file_commit(client, pid)
    assert r.status_code == 409
    assert r.json()["detail"] == ("F-1's design isn't approved yet (not requested). The Lead requests 'Design: Export' "
                                  "with the design.md link; commit requests wait until the Master approves it.")
    assert proj_of(client, pid).db.commits("all") == []  # nothing filed


def test_pending_refused_then_approved_allowed_then_redesign_blocks_again(client, pid):
    new_feature(client, pid)
    first = design_approval(client, pid)
    r = file_commit(client, pid)
    assert r.status_code == 409 and "(pending)" in r.json()["detail"]
    decide(client, pid, first, "approve")
    assert file_commit(client, pid).status_code == 200  # approved: the same request goes through
    second = design_approval(client, pid, "Design: Export v2")  # a material redesign: the latest approval rules
    r = file_commit(client, pid, name="b.py")
    assert r.status_code == 409 and "(pending)" in r.json()["detail"]
    decide(client, pid, second, "changes")
    r = file_commit(client, pid, name="b.py")
    assert r.status_code == 409 and "(changes requested)" in r.json()["detail"]
    third = design_approval(client, pid, "  design: Export v3")  # case and spaces don't matter
    decide(client, pid, third, "approve")
    assert file_commit(client, pid, name="b.py").status_code == 200


def test_a_rejected_design_blocks_commits_until_a_newer_one_is_approved(client, pid):
    """F-25: Reject on a `Design:` approval is "not approved": the gate stays shut, and the header says so."""
    new_feature(client, pid)
    db = proj_of(client, pid).db
    first = design_approval(client, pid)
    decide(client, pid, first, "reject")
    assert db.feature(1)["design"] == {"state": "rejected", "approval_id": first, "gated": True}
    assert "- design: rejected (#1)" in workflow.feature_header(db, "", db.feature(1))
    assert client.get(f"/api/p/{pid}/features/1").json()["design"]["state"] == "rejected"
    assert client.get(f"/api/p/{pid}/dashboard").json()["features"][0]["design"]["state"] == "rejected"
    r = file_commit(client, pid)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("F-1's design isn't approved yet (rejected).")
    assert db.commits("all") == []
    second = design_approval(client, pid, "Design: Export v2")  # asking again after a rejection is allowed
    assert db.feature(1)["design"]["state"] == "pending" and file_commit(client, pid).status_code == 409
    decide(client, pid, second, "approve")
    assert db.feature(1)["design"]["state"] == "approved" and file_commit(client, pid).status_code == 200
    third = design_approval(client, pid, "Design: Export v3")  # and a later rejection closes the gate again
    decide(client, pid, third, "reject")
    assert file_commit(client, pid, name="b.py").status_code == 409


def test_a_rejected_non_design_approval_does_not_touch_the_design_state(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    decide(client, pid, design_approval(client, pid), "approve")
    decide(client, pid, design_approval(client, pid, "PRD ready: Export"), "reject")
    assert db.feature(1)["design"]["state"] == "approved" and file_commit(client, pid).status_code == 200


def test_the_lead_is_gated_too_and_other_approvals_dont_count(client, pid):
    new_feature(client, pid)
    other = design_approval(client, pid, "PRD ready: Export")
    decide(client, pid, other, "approve")
    assert file_commit(client, pid, role="lead").status_code == 409  # only `Design:` approvals open the gate
    decide(client, pid, design_approval(client, pid), "approve")
    assert file_commit(client, pid, role="lead").status_code == 200


def test_old_feature_not_gated_until_it_gets_a_design_approval(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    db.x("UPDATE features SET created_at=1.0 WHERE id=1")  # created before design_gate_since
    assert db.feature(1)["design"] == {"state": "none", "approval_id": None, "gated": False}
    assert file_commit(client, pid).status_code == 200
    design_approval(client, pid)  # a redesign on an older feature gates it
    assert db.feature(1)["design"] == {"state": "pending", "approval_id": 1, "gated": True}
    assert file_commit(client, pid, name="b.py").status_code == 409


def test_a_request_without_a_feature_is_not_gated(client, pid):
    new_feature(client, pid)
    assert file_commit(client, pid, feature=None).status_code == 200


def test_header_line_and_feature_payload(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    feat = lambda: client.get(f"/api/p/{pid}/features/1").json()
    assert workflow.feature_header(db, "", db.feature(1)).splitlines()[2] == "- design: not requested"
    assert feat()["design"] == {"state": "none", "approval_id": None, "gated": True}
    aid = design_approval(client, pid)
    assert "- design: pending (#1)" in workflow.feature_header(db, "", db.feature(1))
    decide(client, pid, aid, "approve")
    assert "- design: approved (#1)" in workflow.feature_header(db, "", db.feature(1))
    assert feat()["design"] == {"state": "approved", "approval_id": 1, "gated": True}
    listed = {f["key"]: f for f in client.get(f"/api/p/{pid}/features").json()}
    assert listed["F-1"]["design"]["state"] == "approved"
    assert client.get(f"/api/p/{pid}/dashboard").json()["features"][0]["design"]["approval_id"] == 1
    new_feature(client, pid, "Old")
    db.x("UPDATE features SET created_at=1.0 WHERE id=2")
    header = workflow.feature_header(db, "", db.feature(2)).splitlines()
    assert header[2] == "- design: not requested · not gated"  # right after the phase line
    assert header[1].startswith("- phase:")
