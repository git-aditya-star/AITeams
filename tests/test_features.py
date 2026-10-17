"""Features: v2 migration, New task API, PATCH system messages, docs listing/reading, wake-prompt header."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from aiteams.db import ProjectDB
from aiteams.orchestrator.prompt import build_wake_prompt


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def test_v1_db_migrates_threads_to_features(repo):
    d = repo / ".aiteams"
    d.mkdir()
    conn = sqlite3.connect(str(d / "state.db"))
    conn.executescript("""
        CREATE TABLE conversations(id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('channel','dm','thread')), title TEXT,
          role TEXT NULL, created_at REAL);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT, author TEXT, type TEXT, text TEXT,
          mentions TEXT, turn_id INTEGER NULL, created_at REAL);
        INSERT INTO conversations VALUES('general','channel','general',NULL,1.0);
        INSERT INTO conversations VALUES('login-feature','thread','Login feature',NULL,2.0);
        INSERT INTO messages(conversation_id, author, type, text, mentions, turn_id, created_at)
          VALUES('login-feature','master','Message','start','[]',NULL,3.0);
    """)
    conn.commit()
    conn.close()
    db = ProjectDB(str(repo))
    try:
        assert db.get_kv("schema_version") == 2
        conv = db.conversation("login-feature")
        assert conv["kind"] == "feature"
        feats = db.features()
        assert len(feats) == 1 and feats[0]["conversation_id"] == "login-feature" and feats[0]["phase"] == "intake"
        assert db.feature("login-feature")["title"] == "Login feature"
        db.update_feature(1, title="Login v2")
        assert db.conversation("login-feature")["title"] == "F-1 Login v2"
        m = db.messages("login-feature")[0]
        assert m["ref"] is None and m["approval"] is None
        db.x("INSERT INTO conversations(id, kind, title, role, created_at) VALUES('x','feature','x',NULL,1)")  # no CHECK
        # idempotent: a second open does nothing
        db2 = ProjectDB(str(repo))
        assert len(db2.features()) == 1
        db2.close()
    finally:
        db.close()


def test_new_task_creates_feature_and_wakes_starter(client, pid, repo):
    f = client.post(f"/api/p/{pid}/features", json={"text": "Add CSV export to the reports page\nmore detail", "starter": "product"}).json()
    assert f["key"] == "F-1" and f["title"] == "Add CSV export to the reports page" and f["phase"] == "intake"
    assert f["conversation_id"] == "feature-1" and (repo / ".aiteams" / "features" / "F-1").is_dir()
    msgs = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()
    assert len(msgs) == 1 and msgs[0]["author"] == "master" and msgs[0]["text"].startswith("@product Add CSV")
    assert msgs[0]["mentions"] == ["product"]
    proj = client.app.state.app.projects[pid]
    assert [w["role"] for w in proj.db.queued_wakes()] == ["product"]
    listed = client.get(f"/api/p/{pid}/features").json()
    assert [x["key"] for x in listed] == ["F-1"] and listed[0]["open_approvals"] == 0
    convs = client.get(f"/api/p/{pid}/conversations").json()
    assert any(c["id"] == "feature-1" and c["kind"] == "feature" for c in convs)
    assert client.post(f"/api/p/{pid}/features", json={"text": "   ", "starter": "product"}).status_code == 400
    assert client.post(f"/api/p/{pid}/features", json={"text": "x", "starter": "master"}).status_code == 400
    assert len(client.get(f"/api/p/{pid}/features").json()) == 1
    # explicit mention in the text is not duplicated
    f2 = client.post(f"/api/p/{pid}/features", json={"text": "@lead fix the typo", "starter": "lead", "title": "Typo"}).json()
    m2 = client.get(f"/api/p/{pid}/messages", params={"conversation": f2["conversation_id"]}).json()[0]
    assert m2["text"] == "@lead fix the typo" and f2["title"] == "Typo"


def test_patch_feature_posts_system_lines_and_history(client, pid):
    f = client.post(f"/api/p/{pid}/features", json={"text": "Thing", "starter": "product"}).json()
    r = client.patch(f"/api/p/{pid}/features/{f['id']}", json={"phase": "build", "owner": "lead"}).json()
    assert r["phase"] == "build" and r["owner"] == "lead"
    assert client.patch(f"/api/p/{pid}/features/{f['id']}", json={"phase": "nope"}).status_code == 400
    msgs = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()
    sys_msgs = [m for m in msgs if m["type"] == "System"]
    assert sys_msgs[-1]["text"] == "Master set phase build\nMaster set owner lead" and sys_msgs[-1]["author"] == "system"
    proj = client.app.state.app.projects[pid]
    assert [w["role"] for w in proj.db.queued_wakes()] == ["product"]  # system lines woke nobody
    client.patch(f"/api/p/{pid}/features/{f['id']}", json={"phase": "done"})
    assert client.get(f"/api/p/{pid}/features").json() == []
    assert [x["key"] for x in client.get(f"/api/p/{pid}/features", params={"history": 1}).json()] == ["F-1"]
    client.patch(f"/api/p/{pid}/features/{f['id']}", json={"phase": "qa", "archived": True})
    assert client.get(f"/api/p/{pid}/features").json() == []
    assert client.patch(f"/api/p/{pid}/features/{f['id']}", json={"archived": False}).json()["archived"] is False
    assert [x["key"] for x in client.get(f"/api/p/{pid}/features").json()] == ["F-1"]


def test_docs_listing_and_reading(client, pid, repo):
    f = client.post(f"/api/p/{pid}/features", json={"text": "Thing", "starter": "product"}).json()
    assert client.get(f"/api/p/{pid}/features/{f['id']}/docs").json() == []
    (repo / ".aiteams" / "features" / "F-1" / "prd.md").write_text("# PRD\nhello\n")
    proj = client.app.state.app.projects[pid]
    proj.db.touch_edit(".aiteams/features/F-1/prd.md", "product")
    docs = client.get(f"/api/p/{pid}/features/{f['id']}/docs").json()
    assert docs[0]["name"] == "prd.md" and docs[0]["editor"] == "product" and docs[0]["size"] == 12
    assert client.get(f"/api/p/{pid}/features/{f['id']}/docs/prd.md").json()["text"] == "# PRD\nhello\n"
    assert client.get(f"/api/p/{pid}/features/{f['id']}/docs/../x.md").status_code in (400, 404)
    assert client.get(f"/api/p/{pid}/features/{f['id']}/docs/nope.md").status_code == 404


def test_wake_prompt_feature_header(db, repo):
    f = db.create_feature("Export")
    (repo / ".aiteams" / "features" / "F-1").mkdir(parents=True)
    (repo / ".aiteams" / "features" / "F-1" / "design.md").write_text("x")
    db.touch_edit(".aiteams/features/F-1/design.md", "lead")
    db.update_feature(f["id"], phase="build", owner="lead")
    db.create_approval(f["id"], "product", None, "Scope lock", "s")
    db.acquire_lock("api/reports.py", "backend", 1)
    m = db.add_message(f["conversation_id"], "master", "Message", "@backend go", ["backend"], None)
    p = build_wake_prompt(db, "backend", [m["id"]])
    assert "## F-1 Export (feature, phase build, id `feature-1`)" in p
    assert "# Feature F-1: Export" in p and "- phase: build · owner: lead · conversation id: feature-1" in p
    assert "design.md (lead, just now)" in p and '#1 "Scope lock" (pending)' in p and "api/reports.py (backend)" in p
    assert "# Conversation: F-1 Export (feature, phase build, id `feature-1`) — last 20 messages" in p
    assert "Re-read a doc" in p
    m2 = db.add_message("dm-backend", "master", "Message", "hi", [], None)
    assert "# Feature" not in build_wake_prompt(db, "backend", [m2["id"]])
