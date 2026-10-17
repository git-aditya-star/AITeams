"""F-17: features.done_at (set on done/archived, cleared on reopen), the prd.md Summary line, dashboard `shipped`."""
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


def new(client, pid, title):
    return client.post(f"/api/p/{pid}/features", json={"text": title, "starter": "product"}).json()


def patch(client, pid, fid, **body):
    r = client.patch(f"/api/p/{pid}/features/{fid}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def shipped(client, pid):
    return client.get(f"/api/p/{pid}/dashboard").json()["shipped"]


def test_done_sets_done_at_and_reopen_clears_it(db):
    f = db.create_feature("x")
    assert f["done_at"] is None
    f = db.update_feature(f["id"], phase="qa")
    assert f["done_at"] is None
    f = db.update_feature(f["id"], phase="done")
    first = f["done_at"]
    assert first is not None
    assert db.update_feature(f["id"], archived=True)["done_at"] == first  # still done: the time stays
    assert db.update_feature(f["id"], phase="build")["done_at"] == first  # archived still counts as done
    f = db.update_feature(f["id"], archived=False)
    assert f["done_at"] is None  # reopened: build and not archived
    time.sleep(0.01)
    assert db.update_feature(f["id"], phase="done")["done_at"] > first  # finishing again gives a fresh time
    assert db.create_feature("born done", phase="done")["done_at"] is not None


def test_dashboard_shipped_counts_order_and_cap(client, pid):
    proj = client.app.state.app.projects[pid]
    assert shipped(client, pid) == {"week": 0, "total": 0, "recent": [], "timeline": []}
    ids = [new(client, pid, f"feature {i}")["id"] for i in range(12)]
    for fid in ids:
        patch(client, pid, fid, phase="done")
    proj.db.x("UPDATE features SET done_at=? WHERE id=?", 1000.0, ids[0])  # long before this week
    s = shipped(client, pid)
    assert (s["week"], s["total"]) == (11, 12)
    assert [r["key"] for r in s["recent"]] == [f"F-{i}" for i in reversed(ids[2:])]  # newest 10, newest first
    assert set(s["recent"][0]) == {"id", "key", "title", "summary", "conversation_id", "done_at"}
    # reopening drops it; finishing again puts it back on top
    patch(client, pid, ids[5], phase="build")
    s = shipped(client, pid)
    assert s["total"] == 11 and f"F-{ids[5]}" not in [r["key"] for r in s["recent"]]
    patch(client, pid, ids[5], phase="done")
    assert shipped(client, pid)["recent"][0]["key"] == f"F-{ids[5]}"


def test_week_starts_monday_local_midnight():
    t = time.mktime((2026, 10, 18, 15, 30, 0, 0, 0, -1))  # Sunday afternoon
    assert time.localtime(workflow.week_start(t))[:6] == (2026, 10, 12, 0, 0, 0)
    monday = time.mktime((2026, 10, 12, 0, 0, 0, 0, 0, -1))
    assert workflow.week_start(monday) == monday


def test_summary_line_from_prd(client, pid, repo):
    f = new(client, pid, "Export")
    docs = repo / ".aiteams" / "features" / f["key"]
    assert workflow.feature_summary(str(repo), f["key"]) is None  # no prd.md
    (docs / "prd.md").write_text("# PRD\n\nno summary here\n")
    assert workflow.feature_summary(str(repo), f["key"]) is None
    (docs / "prd.md").write_text("# PRD\n\n**Summary:**  Export the orders table as CSV.  \n\n**Summary:** second\n")
    assert workflow.feature_summary(str(repo), f["key"]) == "Export the orders table as CSV."
    (docs / "prd.md").write_text("\n" * 45 + "**Summary:** too far down\n")
    assert workflow.feature_summary(str(repo), f["key"]) is None  # only the first 40 lines are read
    (docs / "prd.md").write_text("**Summary:** " + "x" * 300 + "\n")
    assert workflow.feature_summary(str(repo), f["key"]) == "x" * 200
    (docs / "prd.md").write_text("**Summary:** Export the orders table as CSV.\n")
    patch(client, pid, f["id"], phase="done")
    row = shipped(client, pid)["recent"][0]
    assert row["summary"] == "Export the orders table as CSV." and row["conversation_id"] == f["conversation_id"]
