"""HTTP API: register, post message -> wake row + receipts, internal auth, question long-poll."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


def test_register_seeds_and_excludes(client, repo):
    r = client.post("/api/projects/register", json={"path": str(repo)}).json()
    assert r["created"] is True and r["is_git"] is True
    pid = r["id"]
    assert client.post("/api/projects/register", json={"path": str(repo)}).json() == {**r, "created": False}
    assert (repo / ".aiteams" / "state.db").exists()
    assert ".aiteams/" in (repo / ".git" / "info" / "exclude").read_text().splitlines()
    convs = client.get(f"/api/p/{pid}/conversations").json()
    assert [c["id"] for c in convs] == ["general", "standup", "dm-manager", "dm-lead", "dm-lead-2", "dm-product", "dm-frontend", "dm-backend", "dm-qa"]
    assert client.get("/api/health").json()["ok"] is True
    assert client.get(f"/api/p/{pid}").json()["paused"] is False


def test_register_non_git(client, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    r = client.post("/api/projects/register", json={"path": str(d)}).json()
    assert r["is_git"] is False
    assert client.get("/api/projects").json()[0]["is_git"] is False


def test_post_message_creates_wake_and_receipts(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    m = client.post(f"/api/p/{pid}/messages", json={"conversation": "general", "text": "@manager ask @backend to list files"}).json()
    assert m["author"] == "master" and m["mentions"] == ["manager", "backend"]
    assert sorted(r["role"] for r in m["receipts"]) == ["backend", "manager"]
    assert all(r["state"] == "queued" for r in m["receipts"])
    proj = client.app.state.app.projects[pid]
    wakes = proj.db.queued_wakes()
    assert {w["role"] for w in wakes} == {"manager", "backend"} and all(w["origin"] == "master" for w in wakes)
    team = client.get(f"/api/p/{pid}/team").json()["roles"]
    assert {r["role"]: r["state"] for r in team}["backend"] == "queued"
    assert {r["role"]: r["state"] for r in team}["qa"] == "idle"
    listed = client.get(f"/api/p/{pid}/messages", params={"conversation": "general"}).json()
    assert [x["id"] for x in listed] == [m["id"]]
    t = client.post(f"/api/p/{pid}/conversations", json={"title": "Login feature"}).json()
    assert t["kind"] == "feature" and t["id"] == "feature-1" and t["title"] == "F-1 Login feature"
    assert client.get(f"/api/p/{pid}/messages", params={"conversation": "F-1 Login feature"}).json() == []


def test_pause_toggle(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    assert client.post(f"/api/p/{pid}/pause", json={"paused": True}).json() == {"paused": True}
    assert client.get(f"/api/p/{pid}").json()["paused"] is True


def test_internal_requires_running_turn(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    proj = client.app.state.app.projects[pid]
    assert client.post("/internal/messages", json={"conversation": "general", "text": "x"}).status_code == 403
    t = proj.db.create_turn("backend", "sess", "master", "general", token="tok-1")
    h = {"X-AITeams-Project": pid, "X-AITeams-Turn": str(t["id"]), "X-AITeams-Token": "tok-1"}
    assert client.post("/internal/messages", json={"conversation": "general", "text": "x"}, headers=h).status_code == 403
    proj.db.update_turn(t["id"], state="running")
    # right turn id, wrong/missing token -> 403 "bad turn token"; right token -> 200
    bad = client.post("/internal/messages", json={"conversation": "general", "text": "x"}, headers={**h, "X-AITeams-Token": "nope"})
    assert bad.status_code == 403 and bad.json()["detail"] == "bad turn token"
    assert client.post("/internal/messages", json={"conversation": "general", "text": "x"},
                       headers={k: v for k, v in h.items() if k != "X-AITeams-Token"}).status_code == 403
    r = client.post("/internal/messages", json={"conversation": "general", "text": "files: a, b @lead", "type": "Finding"}, headers=h)
    assert r.status_code == 200
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "general"}).json()[-1]
    assert m["author"] == "backend" and m["type"] == "Finding" and m["mentions"] == ["lead"] and m["turn_id"] == t["id"]
    assert proj.db.queued_wake("lead") is not None and proj.db.queued_wake("backend") is None
    assert client.get("/internal/conversations", headers=h).json()[0]["id"] == "general"
    assert client.get("/internal/messages", params={"conversation": "general"}, headers=h).json()[-1]["text"].startswith("files")


def test_role_post_acks_digest_messages(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    proj = client.app.state.app.projects[pid]
    m = client.post(f"/api/p/{pid}/messages", json={"conversation": "dm-backend", "text": "hi"}).json()
    wake = proj.db.queued_wake("backend")
    t, h = running_turn(proj.db, "backend", "dm-backend", wake_id=wake["id"], pid=pid)
    client.post("/internal/messages", json={"conversation": "general", "text": "elsewhere"}, headers=h)
    assert proj.db.receipts_for([m["id"]])[m["id"]] == [{"role": "backend", "state": "queued"}]
    client.post("/internal/messages", json={"conversation": "dm-backend", "text": "hello back"}, headers=h)
    assert proj.db.receipts_for([m["id"]])[m["id"]] == [{"role": "backend", "state": "acked"}]


def test_permission_passthrough_and_question_long_poll(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    proj = client.app.state.app.projects[pid]
    t, h = running_turn(proj.db, "product", "dm-product", pid=pid)
    r = client.post("/internal/permission", json={"tool_name": "Write", "input": {"file_path": "a"}}, headers=h).json()
    assert r == {"behavior": "allow", "updatedInput": {"file_path": "a"}}

    q = {"questions": [{"question": "Which DB?", "header": "DB", "multiSelect": False,
                        "options": [{"label": "Postgres", "description": "d1"}, {"label": "SQLite", "description": "d2"}]}]}
    out = {}
    # F-26: conftest's 2 s ask_timeout_s is for the expiry test below; here the question must outlive the polling and the
    # answer however busy the machine is, or it expires first and the answer is a 409.
    client.app.state.app.settings["ask_timeout_s"] = 60

    def ask():
        out["resp"] = client.post("/internal/permission", json={"tool_name": "AskUserQuestion", "input": q, "tool_use_id": "tu1"},
                                  headers=h).json()
    th = threading.Thread(target=ask)
    th.start()
    for _ in range(600):  # up to 30 s; it normally takes a few polls
        pending = client.get(f"/api/p/{pid}/questions").json()
        if pending:
            break
        time.sleep(0.05)
    assert pending[0]["role"] == "product" and pending[0]["payload"] == q and pending[0]["tool_use_id"] == "tu1"
    a = client.post(f"/api/p/{pid}/questions/{pending[0]['id']}/answer", json={"answers": {"Which DB?": "Postgres"}}).json()
    assert a["state"] == "answered" and a["answers"] == {"Which DB?": "Postgres"}
    th.join(5)
    assert out["resp"]["behavior"] == "allow" and out["resp"]["updatedInput"]["answers"] == {"Which DB?": "Postgres"}
    assert client.get(f"/api/p/{pid}/questions", params={"state": "answered"}).json()[0]["id"] == pending[0]["id"]
    assert client.post(f"/api/p/{pid}/questions/{pending[0]['id']}/answer", json={"answers": {}}).status_code == 409


def test_question_timeout_expires(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    proj = client.app.state.app.projects[pid]
    t, h = running_turn(proj.db, "product", "dm-product", pid=pid)
    q = {"questions": [{"question": "Q?", "header": "H", "multiSelect": False, "options": [{"label": "A", "description": ""}]}]}
    r = client.post("/internal/permission", json={"tool_name": "AskUserQuestion", "input": q}, headers=h).json()  # ask_timeout_s=2
    assert r["behavior"] == "deny" and "did not answer" in r["message"]
    assert client.get(f"/api/p/{pid}/questions", params={"state": "expired"}).json()[0]["state"] == "expired"
