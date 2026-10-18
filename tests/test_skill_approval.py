"""F-5: GET /internal/skill-approval is true only for the caller's own approved `Use team-skill: <name>` approval."""
import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


def test_skill_approval_exact_own_approved_only(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    db = client.app.state.app.projects[pid].db
    _, hdr = running_turn(db, "backend", pid=pid)

    def ok(skill):
        r = client.get("/internal/skill-approval", params={"skill": skill}, headers=hdr)
        assert r.status_code == 200, r.text
        return r.json()["ok"]

    def approval(role, title, state):
        a = db.create_approval(None, role, None, title, "why")
        if state != "pending":
            db.resolve_approval(a["id"], state, None)

    assert ok("team-skills:local-preview") is False
    approval("backend", "Use skill: team-skills:local-preview", "pending")
    approval("backend", "Use skill: team-skills:cli-use", "rejected")
    approval("frontend", "Use skill: team-skills:docs-maker", "approved")
    assert not ok("team-skills:local-preview") and not ok("team-skills:cli-use") and not ok("team-skills:docs-maker")
    approval("backend", "Use skill: team-skills:local-preview", "approved")
    approval("backend", "Use skill: acme:foo", "approved")
    assert ok("team-skills:local-preview") is True and ok("acme:foo") is True
    # F-23: trimmed and case-insensitive, like F-21's titles
    approval("backend", "  USE SKILL: Acme:Bar ", "approved")
    assert ok("acme:bar") is True and ok("ACME:bar ") is True
    for near in ("foo", "acme:foobar", "acme:fo", "other:foo", "team-skills:foo", "acme", ""):
        assert ok(near) is False, near
    approval("backend", "Use skill: acme:a b/c", "approved")
    assert ok("acme:a b/c") is True  # the hook URL-encodes the name; the endpoint sees it decoded
    assert client.get("/internal/skill-approval", params={"skill": "acme:foo"}).status_code == 403  # turn auth required


def test_legacy_team_skill_approval_still_counts_for_team_skills_only(client, repo):
    """F-23: an F-5 `Use team-skill: <x>` approval answers `team-skills:<x>`, and no other plugin's skill."""
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    db = client.app.state.app.projects[pid].db
    _, hdr = running_turn(db, "backend", pid=pid)

    def ok(skill):
        return client.get("/internal/skill-approval", params={"skill": skill}, headers=hdr).json()["ok"]

    for role, title in (("backend", "Use team-skill: local-preview"), ("frontend", "Use team-skill: cli-use"),
                        ("backend", "Use team-skill: shared")):
        a = db.create_approval(None, role, None, title, "why")
        db.resolve_approval(a["id"], "approved", None)
    assert ok("team-skills:local-preview") is True and ok("Team-Skills:Local-Preview") is True
    assert ok("team-skills:shared") is True
    assert ok("team-skills:cli-use") is False  # frontend's approval, not backend's
    assert ok("acme:local-preview") is False and ok("local-preview") is False  # the old title only widens team-skills


def test_a_rejected_skill_approval_keeps_the_skill_refused_until_a_new_one_is_approved(client, repo):
    """F-25: Reject is "not approved" for the Use skill gate, for the new title and the legacy one; a later approval of
    the same skill (the role asked again with new information) opens it."""
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    db = client.app.state.app.projects[pid].db
    _, hdr = running_turn(db, "backend", pid=pid)

    def ok(skill):
        return client.get("/internal/skill-approval", params={"skill": skill}, headers=hdr).json()["ok"]

    def resolve(title, state):
        a = db.create_approval(None, "backend", None, title, "why")
        db.resolve_approval(a["id"], state, "no")

    resolve("Use skill: acme:deploy", "rejected")
    resolve("Use team-skill: local-preview", "rejected")
    resolve("Use skill: acme:lint", "changes_requested")
    assert not ok("acme:deploy") and not ok("team-skills:local-preview") and not ok("acme:lint")
    resolve("Use skill: acme:deploy", "approved")  # asked again, this time approved
    assert ok("acme:deploy") is True and not ok("team-skills:local-preview")
    # through the real decide route too
    a = db.create_approval(None, "backend", None, "Use skill: acme:fmt", "why")
    client.post(f"/api/p/{pid}/approvals/{a['id']}/decide", json={"decision": "reject"})
    assert db.approval(a["id"])["state"] == "rejected" and not ok("acme:fmt")
