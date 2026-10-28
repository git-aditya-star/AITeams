"""Feature fresh start (keep docs / start over) and the dashboard (waits_on, stale, team fields)."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from tests.conftest import AUTH, BRIEF, running_turn
from aiteams.orchestrator.prompt import build_wake_prompt, live_message_ids


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def turn(client, pid, role, conv="general"):
    proj = client.app.state.app.projects[pid]
    return running_turn(proj.db, role, conv, pid=pid)


def test_keep_docs(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    db = proj.db
    f = client.post(f"/api/p/{pid}/features", json={"text": "Export csv", "starter": "product"}).json()
    cid = f["conversation_id"]
    (repo / ".aiteams" / "features" / "F-1" / "prd.md").write_text("# PRD\n")
    old = db.add_message(cid, "product", "Message", "old talk", [], None)
    db.add_wake("backend", cid, [old["id"]], "role")  # a wake that only carries pre-reset messages
    r = client.post(f"/api/p/{pid}/features/1/fresh-start", json={"mode": "keep_docs"}).json()
    assert r["phase"] == "intake" and (repo / ".aiteams" / "features" / "F-1" / "prd.md").exists()
    conv = next(c for c in client.get(f"/api/p/{pid}/conversations").json() if c["id"] == cid)
    assert conv["reset_after"] == old["id"]
    sys_line = db.messages(cid)[-1]
    assert sys_line["author"] == "system" and sys_line["text"] == "Fresh start (keep docs) by the Master"
    assert sys_line["id"] > conv["reset_after"]
    assert db.wake(2)["state"] == "done"  # the backend wake had only old ids
    # prompts and /internal/messages only see post-reset messages
    new = db.add_message(cid, "master", "Message", "@backend new talk", ["backend"], None)
    assert live_message_ids(db, [old["id"], new["id"]]) == [new["id"]]
    p = build_wake_prompt(db, "backend", [new["id"]])
    assert "new talk" in p and "old talk" not in p and "Fresh start (keep docs)" in p
    _, h = turn(client, pid, "backend")
    texts = [m["text"] for m in client.get("/internal/messages", params={"conversation": cid}, headers=h).json()]
    assert "old talk" not in texts and "@backend new talk" in texts
    # the Master's UI still gets everything
    assert "old talk" in [m["text"] for m in client.get(f"/api/p/{pid}/messages", params={"conversation": cid}).json()]
    assert [a["kind"] for a in db.activity()][0] == "fresh_start"


def test_start_over(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    db = proj.db
    client.post(f"/api/p/{pid}/features", json={"text": "Export csv", "starter": "product"}).json()
    first = db.messages("feature-1")[0]
    assert first["author"] == "master" and first["mentions"] == ["product"]
    db.set_wake_state(db.queued_wakes()[0]["id"], "started")
    docs = repo / ".aiteams" / "features" / "F-1"
    (docs / "prd.md").write_text("# PRD\n")
    client.patch(f"/api/p/{pid}/features/1", json={"phase": "build", "owner": "lead"})
    _, h = turn(client, pid, "product", "feature-1")
    client.post("/internal/approvals", json={"feature": "F-1", "title": "Scope", **BRIEF}, headers=h)
    (repo / "a.py").write_text("x\n")
    client.post("/internal/commits", json={"feature": "F-1", "files": ["a.py"], "message": "F-1: a"}, headers=h)
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 1
    r = client.post(f"/api/p/{pid}/features/1/fresh-start", json={"mode": "start_over"}).json()
    assert r["phase"] == "intake" and r["owner"] is None
    assert not (docs / "prd.md").exists()
    arch = os.listdir(docs / "archive")
    assert len(arch) == 1 and (docs / "archive" / arch[0] / "prd.md").read_text() == "# PRD\n"
    assert db.approvals("all")[0]["state"] == "changes_requested" and db.approvals("all")[0]["note"] == "fresh start"
    assert db.commits("all")[0]["state"] == "rejected"
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0
    msgs = db.messages("feature-1")
    assert msgs[-1]["author"] == "master" and msgs[-1]["text"] == first["text"] and msgs[-1]["mentions"] == ["product"]
    assert msgs[-2]["text"] == "Fresh start (start over) by the Master"
    w = db.queued_wake("product")
    assert w and w["message_ids"] == [msgs[-1]["id"]]  # the starter is woken again
    assert client.post(f"/api/p/{pid}/features/1/fresh-start", json={"mode": "nope"}).status_code == 400


def test_dashboard_waits_on_and_stale(client, pid, repo):
    st = client.app.state.app
    proj = st.projects[pid]
    db = proj.db
    mk = lambda i: client.post(f"/api/p/{pid}/features", json={"text": f"T{i}", "starter": "product"}).json()
    f1, f2, f3, f4, f5, f6, f7 = [mk(i) for i in range(1, 8)]
    for w in db.queued_wakes():
        db.set_wake_state(w["id"], "started")
    # f1: pending approval -> master
    _, h = turn(client, pid, "product", f1["conversation_id"])
    client.post("/internal/approvals", json={"feature": "F-1", "title": "Scope", **BRIEF}, headers=h)
    # f2: lead_review commit -> lead; f3: master_review commit -> master
    (repo / "b.py").write_text("x\n"); (repo / "c.py").write_text("x\n")
    _, hb = turn(client, pid, "backend", f2["conversation_id"])
    client.post("/internal/commits", json={"feature": "F-2", "files": ["b.py"], "message": "F-2: b"}, headers=hb)
    _, hl = turn(client, pid, "lead", f3["conversation_id"])
    client.post("/internal/commits", json={"feature": "F-3", "files": ["c.py"], "message": "F-3: c"}, headers=hl)
    # f4: pending question from a turn in that conversation
    t4, _ = turn(client, pid, "qa", f4["conversation_id"])
    db.create_question(t4["id"], "qa", "tu", {"questions": []})
    # f5: queued normal wake for a role; f6: last message mentions a role; f7: nothing
    db.add_message(f5["conversation_id"], "lead", "Message", "@frontend go", ["frontend"], None)
    db.add_wake("frontend", f5["conversation_id"], [db.messages(f5["conversation_id"])[-1]["id"]], "role")
    db.add_message(f6["conversation_id"], "manager", "Message", "@qa please test", ["qa"], None)
    db.add_message(f7["conversation_id"], "qa", "Message", "done", [], None)
    d = client.get(f"/api/p/{pid}/dashboard").json()
    w = {f["key"]: f["waits_on"] for f in d["features"]}
    assert w["F-1"]["kind"] == "approval" and w["F-1"]["who"] == "master"
    assert w["F-2"] == {"kind": "review", "who": "lead", "since": w["F-2"]["since"], "ref": "commit:1"}  # F-38: + ref
    assert w["F-3"]["kind"] == "commit" and w["F-3"]["who"] == "master"
    assert w["F-4"]["kind"] == "question" and w["F-4"]["who"] == "master"
    assert w["F-5"] == {"kind": "queued", "who": "frontend", "since": None}
    assert w["F-6"]["kind"] == "message" and w["F-6"]["who"] == "qa" and w["F-6"]["ref"].startswith("msg:")
    assert w["F-7"] is None
    assert d["needs_you"] == {"questions": 1, "approvals": 1, "commits": 1, "messages": 0, "total": 3}
    assert d["phase_counts"]["intake"] == 7 and d["standup"] is None and d["last_standup"] is None
    assert not any(f["stale"] for f in d["features"])
    assert len(d["activity"]) == 7 and d["activity"][0]["kind"] == "feature_created"
    st.settings["stale_hours"] = 0
    d = client.get(f"/api/p/{pid}/dashboard").json()
    assert all(f["stale"] for f in d["features"])
    client.patch(f"/api/p/{pid}/features/7", json={"phase": "done"})
    assert [f["key"] for f in client.get(f"/api/p/{pid}/features/history").json()] == ["F-7"]
    assert "F-7" not in [f["key"] for f in client.get(f"/api/p/{pid}/dashboard").json()["features"]]
    assert d["team"][0]["role"] == "manager" and d["team"][0]["session_seq"] == 0 and d["team"][0]["current"] is None


def test_team_context_from_active_session_only(client, pid):
    proj = client.app.state.app.projects[pid]
    db = proj.db
    db.new_session("backend", "s1", "first turn")
    t = db.create_turn("backend", "s1", "master", "dm-backend")
    # F-42: the meter is relative to the rotation limit, min(150k, 60% of the window) = 120k on a 200k window
    db.update_turn(t["id"], state="done", context_tokens=90000, result_json=json.dumps({"modelUsage": {"m": {"contextWindow": 200000}}}))
    be = next(x for x in client.get(f"/api/p/{pid}/team").json()["roles"] if x["role"] == "backend")
    assert be["context_pct"] == 0.75 and be["context_limit"] == 120000 and be["session_seq"] == 1 and be["current"]["label"] == "DM backend"
    db.new_session("backend", "s2", "context 75%")
    be = next(x for x in client.get(f"/api/p/{pid}/team").json()["roles"] if x["role"] == "backend")
    assert be["context_tokens"] == 0 and be["context_pct"] == 0 and be["session_seq"] == 2 and be["fresh_starts"] == 1
