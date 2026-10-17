"""F-18: dashboard().activity_hourly (24 local-hour buckets) and shipped.timeline (done in the last 14 days)."""
import time

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH


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


def at(db, mid, t):
    db.x("UPDATE messages SET created_at=? WHERE id=?", t, mid)


def test_activity_hourly_buckets(client, pid):
    proj = proj_of(client, pid)
    db = proj.db
    now = time.time()
    cur = workflow.hour_start(now)
    db.x("UPDATE messages SET created_at=0")  # the seeded channel/system lines are out of the window
    at(db, db.add_message("general", "lead", "Message", "now", [], None)["id"], now)
    at(db, db.add_message("general", "qa", "Message", "now too", [], None)["id"], cur + 1)
    at(db, db.add_message("general", "product", "Message", "5h ago", [], None)["id"], cur - 5 * 3600 + 10)
    at(db, db.add_message("general", "system", "System", "not counted", [], None)["id"], now)
    at(db, db.add_message("general", "frontend", "Message", "25h ago", [], None)["id"], cur - 25 * 3600)
    at(db, db.add_message("general", "frontend", "Message", "oldest hour", [], None)["id"], cur - 23 * 3600)
    done = db.create_commit(None, "backend", None, ["a.py"], "m", {}, "committed")
    db.x("UPDATE commits SET updated_at=? WHERE id=?", cur - 2 * 3600 + 5, done["id"])
    db.create_commit(None, "backend", None, ["b.py"], "m", {}, "master_review")  # not committed: not counted
    h = client.get(f"/api/p/{pid}/dashboard").json()["activity_hourly"]
    assert len(h) == 24 and set(h[0]) == {"start", "messages", "commits"}
    assert [b["start"] for b in h] == [cur - (23 - i) * 3600 for i in range(24)]  # oldest first, ends at this hour
    assert h[23]["messages"] == 2 and h[18]["messages"] == 1 and h[0]["messages"] == 1
    assert sum(b["messages"] for b in h) == 4  # System line and the 25h-old one left out
    assert h[21]["commits"] == 1 and sum(b["commits"] for b in h) == 1


def test_hour_start_is_local():
    t = time.mktime((2026, 10, 18, 14, 37, 12, 0, 0, -1))
    assert time.localtime(workflow.hour_start(t))[:6] == (2026, 10, 18, 14, 0, 0)


def test_shipped_timeline_keeps_14_days_newest_first(client, pid, repo):
    proj = proj_of(client, pid)
    db = proj.db
    now = time.time()
    ages = {"today": 0.1, "a week ago": 7, "13 days ago": 13.9, "15 days ago": 15}
    ids = {}
    for title, days in ages.items():
        f = client.post(f"/api/p/{pid}/features", json={"text": title, "starter": "product"}).json()
        client.patch(f"/api/p/{pid}/features/{f['id']}", json={"phase": "done"})
        db.x("UPDATE features SET done_at=? WHERE id=?", now - days * 86400, f["id"])
        ids[title] = f["key"]
    (repo / ".aiteams" / "features" / ids["a week ago"] / "prd.md").write_text("# PRD\n\n**Summary:** Week-old work.\n")
    s = client.get(f"/api/p/{pid}/dashboard").json()["shipped"]
    assert [f["key"] for f in s["timeline"]] == [ids["today"], ids["a week ago"], ids["13 days ago"]]
    assert s["timeline"][1]["summary"] == "Week-old work."
    assert set(s["timeline"][0]) == {"id", "key", "title", "summary", "conversation_id", "done_at"}
    assert s["total"] == 4 and len(s["recent"]) == 4  # the existing fields are unchanged


def test_shipped_timeline_cap(client, pid):
    db = proj_of(client, pid).db
    for i in range(105):
        db.create_feature(f"f{i}", phase="done")
    assert len(client.get(f"/api/p/{pid}/dashboard").json()["shipped"]["timeline"]) == 100
