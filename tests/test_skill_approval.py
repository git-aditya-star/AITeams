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

    assert ok("local-preview") is False
    approval("backend", "Use team-skill: local-preview", "pending")
    approval("backend", "Use team-skill: cli-use", "rejected")
    approval("frontend", "Use team-skill: docs-maker", "approved")
    assert not ok("local-preview") and not ok("cli-use") and not ok("docs-maker")
    approval("backend", "Use team-skill: local-preview", "approved")
    approval("backend", "Use team-skill: foo", "approved")
    assert ok("local-preview") is True and ok("foo") is True
    for near in ("foobar", "fo", "Foo", "foo ", "team-skills:foo", "a b"):
        assert ok(near) is False, near
    approval("backend", "Use team-skill: a b/c", "approved")
    assert ok("a b/c") is True  # the hook URL-encodes the name; the endpoint sees it decoded
    assert client.get("/internal/skill-approval", params={"skill": "foo"}).status_code == 403  # turn auth required
