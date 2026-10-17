"""Commit requests: Lead review, Master approve (real git), stale/force, lead-direct, reject, lock conflicts."""
import subprocess

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
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Aditya Test", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Aditya Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def turn(client, pid, role):
    proj = client.app.state.app.projects[pid]
    t = proj.db.create_turn(role, "sess", "master", "general")
    proj.db.update_turn(t["id"], state="running")
    return {"X-AITeams-Project": pid, "X-AITeams-Turn": str(t["id"])}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_full_flow_backend_lead_master(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    f = client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"}).json()
    proj.db.set_wake_state(proj.db.queued_wakes()[0]["id"], "started")
    (repo / "api_export.py").write_text("def hello():\n    return 'hi'\n")
    (repo / "README.md").write_text("# demo\nchanged but not listed\n")
    backend = turn(client, pid, "backend")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": ["api_export.py"], "message": "add export\n\nbody"}, headers=backend)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["state"] == "lead_review" and c["files"] == ["api_export.py"]
    row = proj.db.commit(c["id"])
    assert row["message"] == "F-1: add export\n\nbody" and len(row["hashes"]["api_export.py"]) == 64
    msgs = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()
    m = msgs[-1]
    assert m["type"] == "Commit" and m["ref"] == f"commit:{c['id']}" and m["mentions"] == ["lead"]
    assert m["commit"]["state"] == "lead_review" and "**Commit request #1** (1 file)" in m["text"]
    assert [w["role"] for w in proj.db.queued_wakes()] == ["lead"]
    assert client.get(f"/api/p/{pid}/inbox").json()["commits"] == []  # waiting on Lead: not in the Inbox
    assert client.get(f"/api/p/{pid}/features").json()[0]["open_commits"] == 1
    # Master cannot act yet
    assert client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"}).status_code == 409
    # only the lead reviews
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=backend).status_code == 403
    lead = turn(client, pid, "lead")
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve", "note": "lgtm"}, headers=lead).json()["state"] == "master_review"
    sysm = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert sysm["type"] == "System" and "Lead approved commit #1" in sysm["text"]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [x["id"] for x in inbox["commits"]] == [c["id"]] and inbox["commits"][0]["feature"] == "F-1" and inbox["count"] == 1
    d = client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()
    assert "+def hello():" in d["diff"] and d["stale"] == []
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve", "message": "F-1: add export (edited)\n\nbody"}).json()
    assert r["state"] == "committed" and r["sha"] and r["message"].startswith("F-1: add export (edited)")
    assert git(repo, "log", "-1", "--format=%s|%an") == "F-1: add export (edited)|Aditya Test"
    assert git(repo, "diff", "--cached", "--name-only") == "" and git(repo, "diff", "--name-only") == "README.md"  # unlisted dirty file untouched
    assert "README.md" not in git(repo, "show", "--name-only", "--format=", "HEAD")
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert last["author"] == "master" and last["type"] == "Decision" and last["mentions"] == ["backend"]
    assert f"Committed `{r['sha']}`: F-1: add export (edited)" in last["text"]
    assert "backend" in [w["role"] for w in proj.db.queued_wakes()]
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0
    # prefix check respects the F-<n> boundary
    c12 = client.post("/internal/commits", json={"feature": "F-1", "files": ["api_export.py"], "message": "F-12: other"}, headers=backend).json()
    assert proj.db.commit(c12["id"])["message"] == "F-1: F-12: other"


def test_stale_then_force_and_lead_direct_and_reject(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    (repo / "a.py").write_text("1\n")
    lead = turn(client, pid, "lead")
    c = client.post("/internal/commits", json={"feature": None, "files": ["a.py"], "message": "no feature"}, headers=lead).json()
    assert c["state"] == "master_review"  # lead skips review
    msgs = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-lead"}).json()
    assert msgs[-1]["type"] == "Commit" and msgs[-1]["mentions"] == [] and "Waiting on the Master" in msgs[-1]["text"]
    assert proj.db.commit(c["id"])["message"] == "no feature"
    (repo / "a.py").write_text("2\n")
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == {"stale": ["a.py"]}
    assert client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()["stale"] == ["a.py"]
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve", "force": True}).json()
    assert r["state"] == "committed" and git(repo, "show", "HEAD:a.py") == "2"
    # reject by the Master
    (repo / "b.py").write_text("x\n")
    c2 = client.post("/internal/commits", json={"files": ["b.py"], "message": "b"}, headers=lead).json()
    r = client.post(f"/api/p/{pid}/commits/{c2['id']}/decide", json={"decision": "reject", "note": "not now"}).json()
    assert r["state"] == "rejected" and r["note"] == "not now"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-lead"}).json()[-1]
    assert last["author"] == "master" and "Commit #2 rejected: not now" in last["text"] and last["mentions"] == ["lead"]
    # reject by the Lead
    backend = turn(client, pid, "backend")
    (repo / "c.py").write_text("x\n")
    c3 = client.post("/internal/commits", json={"files": ["c.py"], "message": "c"}, headers=backend).json()
    r = client.post(f"/internal/commits/{c3['id']}/review", json={"decision": "reject", "note": "add tests"}, headers=lead).json()
    assert r["state"] == "rejected"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-backend"}).json()[-1]
    assert last["author"] == "lead" and last["type"] == "Decision" and "Commit #3 rejected: add tests" in last["text"]
    assert "backend" in [w["role"] for w in proj.db.queued_wakes()]
    assert [x["state"] for x in client.get(f"/api/p/{pid}/commits").json()] == ["rejected", "rejected", "committed"]


def test_validation_and_lock_conflict(client, pid, repo):
    backend = turn(client, pid, "backend")
    frontend = turn(client, pid, "frontend")
    assert client.post("/internal/commits", json={"files": [], "message": "m"}, headers=backend).status_code == 400
    assert client.post("/internal/commits", json={"files": ["nope.py"], "message": "m"}, headers=backend).status_code == 400
    assert client.post("/internal/commits", json={"files": ["../x"], "message": "m"}, headers=backend).status_code == 400
    assert client.post("/internal/commits", json={"files": [".aiteams/notes/a.md"], "message": "m"}, headers=backend).status_code == 400
    assert client.post("/internal/commits", json={"files": ["x"], "message": "m"}, headers=backend).status_code == 400
    for d in (".", "sub", "sub/", str(repo)):
        (repo / "sub").mkdir(exist_ok=True)
        r = client.post("/internal/commits", json={"files": [d], "message": "m"}, headers=backend)
        assert r.status_code == 400 and "directory" in r.json()["detail"], d
    (repo / "x.py").write_text("x\n")
    client.post("/internal/locks/acquire", json={"path": "x.py"}, headers=frontend)
    r = client.post("/internal/commits", json={"files": ["x.py"], "message": "m"}, headers=backend)
    assert r.status_code == 409 and "locked by frontend" in r.json()["detail"]
    # deleting a tracked file is a valid commit
    (repo / "README.md").unlink()
    c = client.post("/internal/commits", json={"files": ["README.md"], "message": "rm readme"}, headers=backend).json()
    assert client.app.state.app.projects[pid].db.commit(c["id"])["hashes"] == {"README.md": None}
    lead = turn(client, pid, "lead")
    client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=lead)
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"}).json()
    assert r["state"] == "committed" and git(repo, "ls-files") == ""


def test_commit_failure_is_reported(client, pid, repo):
    subprocess.run(["git", "config", "--unset", "user.name"], cwd=repo)
    subprocess.run(["git", "config", "--unset", "user.email"], cwd=repo)
    (repo / "z.py").write_text("x\n")
    lead = turn(client, pid, "lead")
    c = client.post("/internal/commits", json={"files": ["z.py"], "message": "z"}, headers=lead).json()
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"},
                    headers={}).json()
    if r["state"] == "committed":
        pytest.skip("global git identity configured; cannot provoke a commit failure here")
    assert r["state"] == "failed" and r["note"]
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-lead"}).json()[-1]
    assert "Commit #1 failed" in last["text"]
