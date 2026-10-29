"""Commit requests: Lead review, Master approve (real git), stale/force, lead-direct, reject, lock conflicts."""
import hashlib
import os
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture(autouse=True)
def no_guard(monkeypatch):
    """These tests drive many steps through one long turn per role; the F-16 refuse-once guard (tested in
    test_new_messages.py) would refuse the steps that follow other roles' posts."""
    from aiteams import workflow

    async def off(*a, **k):
        return None
    monkeypatch.setattr(workflow, "guard", off)


@pytest.fixture
def pid(client, repo):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Aditya Test", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Aditya Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def turn(client, pid, role):
    proj = client.app.state.app.projects[pid]
    return running_turn(proj.db, role, pid=pid)[1]


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_full_flow_backend_lead_master(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    f = client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"}).json()
    proj.db.set_wake_state(proj.db.queued_wakes()[0]["id"], "started")
    (repo / "api_export.py").write_text("def hello():\n    return 'hi'\n")
    (repo / "README.md").write_text("# demo\nchanged but not listed\n")
    backend = turn(client, pid, "backend")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": ["api_export.py"], "message": "feat: add export\n\nbody"}, headers=backend)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["state"] == "lead_review" and c["files"] == ["api_export.py"]
    row = proj.db.commit(c["id"])
    assert row["message"] == "feat: add export\n\nbody"  # F-27: stored as sent, no F-n prefix
    assert len(row["hashes"]["api_export.py"]) == 64
    msgs = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()
    # F-22: the feature had no Lead, so "@lead" picked one (Lead 1, the default), announced right after the card
    assert msgs[-1]["author"] == "system" and msgs[-1]["text"] == "Lead 1 picked automatically (default)."
    assert proj.db.feature(1)["lead"] == "lead"
    m = msgs[-2]
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
    assert sysm["type"] == "System" and "Lead 1 approved commit #1" in sysm["text"]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [x["id"] for x in inbox["commits"]] == [c["id"]] and inbox["commits"][0]["feature"] == "F-1" and inbox["count"] == 1
    d = client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()
    assert "+def hello():" in d["diff"] and d["stale"] == []
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve", "message": "feat: add export (edited)\n\nbody"}).json()
    assert r["state"] == "committed" and r["sha"] and r["message"].startswith("feat: add export (edited)")
    assert git(repo, "log", "-1", "--format=%s|%an") == "feat: add export (edited)|Aditya Test"
    assert git(repo, "diff", "--cached", "--name-only") == "" and git(repo, "diff", "--name-only") == "README.md"  # unlisted dirty file untouched
    assert "README.md" not in git(repo, "show", "--name-only", "--format=", "HEAD")
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert last["author"] == "master" and last["type"] == "Decision" and last["mentions"] == ["lead"]
    assert last["text"] == f"@lead Committed `{r['sha']}`: feat: add export (edited)"
    # F-20: a commit wakes only the Lead, who sequences the next step; backend sees the line in its next news
    assert "lead" in {w["role"] for w in proj.db.queued_wakes()}
    assert not any(w["role"] == "backend" and last["id"] in w["message_ids"] for w in proj.db.queued_wakes())
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0
    # F-27: a message is never rewritten, with or without an F-n tag
    c12 = client.post("/internal/commits", json={"feature": "F-1", "files": ["api_export.py"], "message": "F-12: other"}, headers=backend).json()
    assert proj.db.commit(c12["id"])["message"] == "F-12: other"


def test_stale_is_sent_back_and_lead_direct_and_reject(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    (repo / "a.py").write_text("1\n")
    lead = turn(client, pid, "lead")
    c = client.post("/internal/commits", json={"feature": None, "files": ["a.py"], "message": "no feature"}, headers=lead).json()
    assert c["state"] == "master_review"  # lead skips review
    msgs = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-lead"}).json()
    assert msgs[-1]["type"] == "Commit" and msgs[-1]["mentions"] == [] and "Waiting on the Master" in msgs[-1]["text"]
    assert proj.db.commit(c["id"])["message"] == "no feature"
    (repo / "a.py").write_text("2\n")
    head = git(repo, "rev-parse", "HEAD")
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve", "force": True})  # F-45: no override
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(c["id"]): ["a.py"]}}
    assert git(repo, "rev-parse", "HEAD") == head
    row = proj.db.commit(c["id"])
    assert row["state"] == "lead_review" and row["note"] == "changed after review: a.py"
    assert client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()["stale"] == ["a.py"]  # the filing-time hash, until a Lead approves
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=lead).status_code == 200
    assert client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()["stale"] == []
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"}).json()
    assert r["state"] == "committed" and git(repo, "show", "HEAD:a.py") == "2"
    # reject by the Master
    (repo / "b.py").write_text("x\n")
    c2 = client.post("/internal/commits", json={"files": ["b.py"], "message": "b"}, headers=lead).json()
    r = client.post(f"/api/p/{pid}/commits/{c2['id']}/decide", json={"decision": "reject", "note": "not now"}).json()
    assert r["state"] == "rejected" and r["note"] == "not now"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-lead"}).json()[-1]
    assert last["author"] == "master" and last["text"] == "@lead Commit #2 rejected: not now" and last["mentions"] == ["lead"]
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


def review(client, pid, cid):
    r = client.post(f"/internal/commits/{cid}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead"))
    assert r.status_code == 200, r.text
    return r.json()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_leads_approval_rehashes_so_an_edit_before_it_counts_as_reviewed(client, pid, repo):
    """F-45: the hashes the Master's commit is checked against are taken when the Lead approves, not when it is filed."""
    proj = client.app.state.app.projects[pid]
    (repo / "w.py").write_text("v1\n")
    cid = ask(client, pid, "backend", ["w.py"]).json()["id"]
    assert proj.db.commit(cid)["hashes"] == {"w.py": sha("v1\n")}  # filing time
    (repo / "w.py").write_text("v2\n")  # between filing and the Lead's approval
    assert client.get(f"/api/p/{pid}/commits/{cid}/diff").json()["stale"] == ["w.py"]
    assert review(client, pid, cid)["state"] == "master_review"
    assert proj.db.commit(cid)["hashes"] == {"w.py": sha("v2\n")}  # approval time
    assert client.get(f"/api/p/{pid}/commits/{cid}/diff").json()["stale"] == []
    assert approve(client, pid, cid).json()["state"] == "committed"
    assert git(repo, "show", "HEAD:w.py") == "v2"


def test_a_file_changed_after_the_leads_approval_goes_back_to_lead_review(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    (repo / "w.py").write_text("v1\n")
    cid = ask(client, pid, "backend", ["w.py"]).json()["id"]
    review(client, pid, cid)
    head = git(repo, "rev-parse", "HEAD")
    (repo / "w.py").write_text("v2 added after the review\n")
    proj.db.x("DELETE FROM wake_queue")
    r = approve(client, pid, cid, force=True)  # force is gone: it does not override
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(cid): ["w.py"]}}
    assert git(repo, "rev-parse", "HEAD") == head and status(repo) == "?? w.py"  # no git write at all
    row = proj.db.commit(cid)
    assert row["state"] == "lead_review" and row["note"] == "changed after review: w.py" and row["sha"] is None
    posts = [m for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-backend"}).json()
             if "went back to Lead review" in m["text"]]
    assert len(posts) == 1  # one line for the conversation, naming the path
    assert posts[0]["author"] == "master" and posts[0]["type"] == "Decision" and posts[0]["mentions"] == ["backend", "lead"]
    assert posts[0]["text"] == f"@backend @lead Commit #{cid} went back to Lead review: `w.py` changed after approval."
    assert sorted(w["role"] for w in proj.db.queued_wakes()) == ["backend", "lead"]  # the Lead is woken to re-review
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == 0  # it left the Master's Inbox
    assert approve(client, pid, cid).status_code == 409  # and the Master cannot commit it until the Lead approves again
    review(client, pid, cid)
    assert approve(client, pid, cid).json()["state"] == "committed" and git(repo, "show", "HEAD:w.py") == "v2 added after the review"


def test_a_file_deleted_or_restored_after_approval_is_a_change_but_a_deletion_the_lead_saw_commits(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    for n in ("d1.py", "d2.py", "d3.py"):
        (repo / n).write_text("x\n")
    git(repo, "add", "d1.py", "d2.py", "d3.py")
    git(repo, "commit", "-qm", "three files")
    (repo / "d1.py").write_text("y\n")
    cid1 = ask(client, pid, "backend", ["d1.py"]).json()["id"]
    review(client, pid, cid1)
    (repo / "d1.py").unlink()
    r = approve(client, pid, cid1)
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(cid1): ["d1.py"]}}
    assert proj.db.commit(cid1)["state"] == "lead_review" and git(repo, "ls-files").count("d1.py") == 1
    (repo / "d2.py").unlink()  # deleted before the approval and still gone: commits as it always did
    cid2 = ask(client, pid, "backend", ["d2.py"]).json()["id"]
    review(client, pid, cid2)
    assert proj.db.commit(cid2)["hashes"] == {"d2.py": None}
    assert approve(client, pid, cid2).json()["state"] == "committed" and "d2.py" not in git(repo, "ls-files")
    (repo / "d3.py").unlink()  # deleted at the approval, then it comes back: that is a change
    cid3 = ask(client, pid, "backend", ["d3.py"]).json()["id"]
    review(client, pid, cid3)
    (repo / "d3.py").write_text("back\n")
    r = approve(client, pid, cid3)
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(cid3): ["d3.py"]}}


def test_a_leads_own_request_keeps_its_filing_hashes_and_goes_back_when_edited(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    (repo / "l.py").write_text("v1\n")
    c = ask(client, pid, "lead", ["l.py"]).json()
    assert c["state"] == "master_review" and proj.db.commit(c["id"])["hashes"] == {"l.py": sha("v1\n")}  # no review, no re-hash
    (repo / "l.py").write_text("v2\n")
    r = approve(client, pid, c["id"])
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(c["id"]): ["l.py"]}}
    assert proj.db.commit(c["id"])["state"] == "lead_review"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-lead"}).json()[-1]
    assert last["mentions"] == ["lead"] and last["text"].startswith(f"@lead Commit #{c['id']} went back to Lead review")
    review(client, pid, c["id"])  # the Lead approves its own request again, which re-hashes it
    assert proj.db.commit(c["id"])["hashes"] == {"l.py": sha("v2\n")}
    assert approve(client, pid, c["id"]).json()["state"] == "committed" and git(repo, "show", "HEAD:l.py") == "v2"


def test_an_outside_file_edited_after_the_leads_approval_is_sent_back(client, pid, repo, tmp_path):
    proj = client.app.state.app.projects[pid]
    sib = sibling(tmp_path)
    real = os.path.realpath(sib / "etl.py")
    (sib / "etl.py").write_text("v2\n")
    cid = ask(client, pid, "backend", [sib / "etl.py"]).json()["id"]
    (sib / "etl.py").write_text("v3 before the review\n")
    review(client, pid, cid)
    assert proj.db.commit(cid)["hashes"] == {real: sha("v3 before the review\n")}  # hashed in its own repo, by absolute path
    (sib / "etl.py").write_text("v4 after the review\n")
    r = approve(client, pid, cid)
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(cid): [real]}}
    assert git(sib, "log", "--format=%s") == "init" and proj.db.commit(cid)["state"] == "lead_review"


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


@pytest.mark.parametrize("outcome", ["rejected", "failed"])
def test_master_decision_on_a_role_request_wakes_the_lead_too(client, pid, repo, outcome):
    """F-14: rejected and failed decisions on a non-Lead request mention (and wake) the requester and the Lead."""
    proj = client.app.state.app.projects[pid]
    (repo / "q.py").write_text("x\n")
    c = client.post("/internal/commits", json={"files": ["q.py"], "message": "q"}, headers=turn(client, pid, "qa")).json()
    if outcome == "failed":  # gone before the Lead approves, so not a change after review (F-45)
        (repo / "q.py").unlink()  # git add of a vanished, untracked path fails (the sandbox's commit_failed path)
    client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead"))
    proj.db.x("DELETE FROM wake_queue")
    if outcome == "failed":
        r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"}).json()
    else:
        r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "reject", "note": "later"}).json()
    assert r["state"] == outcome
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert last["author"] == "master" and last["mentions"] == ["qa", "lead"]
    assert last["text"].startswith(f"@qa @lead Commit #{c['id']} {outcome}")
    assert sorted(w["role"] for w in proj.db.queued_wakes()) == ["lead", "qa"]


@pytest.mark.parametrize("filer", ["backend", "lead"])
def test_master_commit_wakes_only_the_lead(client, pid, repo, filer):
    """F-20 (card #16): a successful commit mentions and wakes only the Lead, whoever filed it; a reject of the same
    kind of request still wakes the filer and the Lead (F-14)."""
    proj = client.app.state.app.projects[pid]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    proj.db.update_feature(1, lead="lead")  # a feature has its Lead; "@lead" in it means that Lead (F-22)
    (repo / "w.py").write_text("x\n")
    body = {"feature": "F-1", "files": ["w.py"], "message": "F-1: w"}
    c = client.post("/internal/commits", json=body, headers=turn(client, pid, filer)).json()
    if filer != "lead":
        client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead"))
    proj.db.x("DELETE FROM wake_queue")
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"}).json()
    assert r["state"] == "committed"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert last["author"] == "master" and last["mentions"] == ["lead"]
    assert last["text"] == f"@lead Committed `{r['sha']}`: F-1: w"
    assert [w["role"] for w in proj.db.queued_wakes()] == ["lead"]
    if filer == "lead":
        return
    (repo / "w2.py").write_text("y\n")
    body = {"feature": "F-1", "files": ["w2.py"], "message": "F-1: w2"}
    c2 = client.post("/internal/commits", json=body, headers=turn(client, pid, filer)).json()
    client.post(f"/internal/commits/{c2['id']}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead"))
    proj.db.x("DELETE FROM wake_queue")
    client.post(f"/api/p/{pid}/commits/{c2['id']}/decide", json={"decision": "reject", "note": "no"})
    assert sorted(w["role"] for w in proj.db.queued_wakes()) == ["backend", "lead"]


def test_diff_per_file_status_counts_and_totals(client, pid, repo):
    (repo / "old.py").write_text("a\nb\n")
    (repo / "same.py").write_text("s\n")
    (repo / "q.sql").write_text("select 1;\n-- sql comment\n")
    (repo / "logo.bin").write_bytes(b"\x00\x01" * 8)
    (repo / "latin.txt").write_text("cafe\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "more"], cwd=repo, check=True)
    (repo / "README.md").write_text("# demo\nline two\n")       # modified: +1
    (repo / "new.py").write_text("x = 1\ny = 2\n--z\n")          # added: +3
    (repo / "old.py").unlink()                                   # deleted: -2
    (repo / "q.sql").write_text("select 1;\n++x\n")              # in-hunk "--- sql comment" and "+++x": +1 -1
    (repo / "logo.bin").write_bytes(b"\x00\x02" * 8)             # modified binary
    (repo / "aiteams" / "static" / "assets").mkdir(parents=True)
    (repo / "aiteams" / "static" / "assets" / "index-X.js").write_text("var a=1;\n")
    (repo / "aiteams" / "static" / "index.html").write_text("<p>\n")
    (repo / "img.bin").write_bytes(b"\x00\x01\x02" * 10)         # new binary
    (repo / "my notes é.md").write_text("hi\n")                  # git quotes this path in headers
    (repo / "gone.py").write_text("g\n")                         # untracked, deleted after the request
    (repo / "ff.js").write_text("a = 1\x0c-x\ns = 'a -b'\n", encoding="utf-8")  # +2: \x0c / U+2028 are not line breaks
    (repo / "latin.txt").write_bytes("café\n".encode("latin-1"))  # tracked, now not UTF-8: replaced, no crash
    files = ["README.md", "new.py", "old.py", "q.sql", "logo.bin", "aiteams/static/assets/index-X.js",
             "aiteams/static/index.html", "img.bin", "my notes é.md", "same.py", "gone.py", "ff.js", "latin.txt"]
    lead = turn(client, pid, "lead")
    c = client.post("/internal/commits", json={"feature": None, "files": files, "message": "many"}, headers=lead).json()
    (repo / "gone.py").unlink()
    d = client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()
    by = {f["path"]: f for f in d["files"]}
    assert [f["path"] for f in d["files"]] == files
    assert [(by[p]["status"], by[p]["additions"], by[p]["deletions"]) for p in files] == [
        ("modified", 1, 0), ("added", 3, 0), ("deleted", 0, 2), ("modified", 1, 1), ("modified", 0, 0),
        ("added", 1, 0), ("added", 1, 0), ("added", 0, 0), ("added", 1, 0), ("unchanged", 0, 0), ("missing", 0, 0),
        ("added", 2, 0), ("modified", 1, 1)]
    assert "+caf�" in by["latin.txt"]["text"]
    assert [p for p in files if by[p]["binary"]] == ["logo.bin", "img.bin"]
    assert [p for p in files if by[p]["built"]] == ["aiteams/static/assets/index-X.js"]
    assert by["old.py"]["size"] is None and by["img.bin"]["size"] == 30 and by["new.py"]["size"] == 16
    assert by["gone.py"]["size"] is None and by["gone.py"]["text"] == ""
    assert by["same.py"]["text"] == "" and by["README.md"]["text"].startswith("diff --git a/README.md")
    assert d["diff"] == "".join(f["text"] for f in d["files"]) and d["stale"] == ["gone.py"]
    assert d["totals"] == {"files": 13, "additions": 11, "deletions": 4}
    assert d["commit"]["id"] == c["id"] and d["commit"]["feature"] is None and d["overlap"] == []
    assert client.get(f"/api/p/{pid}/commits/999/diff").status_code == 404


def test_diff_overlap_lists_only_pending_requests(client, pid, repo):
    proj = client.app.state.app.projects[pid]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    for n in "abc":
        (repo / f"{n}.py").write_text(n + "\n")
    lead, backend = turn(client, pid, "lead"), turn(client, pid, "backend")
    mine = client.post("/internal/commits", json={"files": ["a.py", "b.py"], "message": "mine"}, headers=lead).json()
    review = client.post("/internal/commits", json={"feature": "F-1", "files": ["b.py"], "message": "x"}, headers=backend).json()
    master = client.post("/internal/commits", json={"files": ["a.py", "c.py"], "message": "y"}, headers=lead).json()
    rejected = client.post("/internal/commits", json={"files": ["a.py"], "message": "z"}, headers=lead).json()
    client.post(f"/api/p/{pid}/commits/{rejected['id']}/decide", json={"decision": "reject"})
    other = client.post("/internal/commits", json={"files": ["c.py"], "message": "w"}, headers=lead).json()
    done = client.post("/internal/commits", json={"files": ["b.py"], "message": "v"}, headers=lead).json()
    proj.db.update_commit(done["id"], state="committed")
    failed = client.post("/internal/commits", json={"files": ["a.py"], "message": "u"}, headers=lead).json()
    proj.db.update_commit(failed["id"], state="failed")
    both = client.post("/internal/commits", json={"files": ["b.py", "a.py"], "message": "t"}, headers=backend).json()
    d = client.get(f"/api/p/{pid}/commits/{mine['id']}/diff").json()
    assert d["overlap"] == [
        {"path": "a.py", "commit_id": master["id"], "role": "lead", "state": "master_review", "feature": None},
        {"path": "a.py", "commit_id": both["id"], "role": "backend", "state": "lead_review", "feature": None},
        {"path": "b.py", "commit_id": review["id"], "role": "backend", "state": "lead_review", "feature": "F-1"},
        {"path": "b.py", "commit_id": both["id"], "role": "backend", "state": "lead_review", "feature": None}]
    assert other["id"] not in [o["commit_id"] for o in d["overlap"]]
    assert client.get(f"/api/p/{pid}/commits/{review['id']}/diff").json()["commit"]["feature"] == "F-1"


def test_refile_supersedes_covered_pending_requests(client, pid, repo):
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    for n in "abc":
        (repo / f"{n}.py").write_text(n + "\n")
    lead, backend, frontend = turn(client, pid, "lead"), turn(client, pid, "backend"), turn(client, pid, "frontend")
    post = lambda h, files, feature="F-1": client.post(  # noqa: E731
        "/internal/commits", json={"feature": feature, "files": files, "message": "m"}, headers=h).json()
    old = post(backend, ["a.py", "b.py"])
    client.post(f"/internal/commits/{old['id']}/review", json={"decision": "approve", "note": "ok"}, headers=lead)
    partial = post(backend, ["a.py"])          # covers only part of `old`: both stay
    other_role = post(frontend, ["a.py", "b.py", "c.py"])
    other_feat = post(backend, ["a.py", "b.py", "c.py"], feature=None)
    state = lambda c: client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()["commit"]["state"]  # noqa: E731
    assert [state(x) for x in (old, partial, other_role)] == ["master_review", "lead_review", "lead_review"]
    new = post(backend, ["b.py", "a.py", "c.py"])
    d = client.get(f"/api/p/{pid}/commits/{old['id']}/diff").json()["commit"]
    assert d["state"] == "rejected" and d["note"] == f"superseded by #{new['id']}"
    assert [state(x) for x in (partial, other_role, other_feat, new)] == ["rejected", "lead_review", "lead_review", "lead_review"]
    assert [x["id"] for x in client.get(f"/api/p/{pid}/inbox").json()["commits"]] == []
    texts = [m["text"] for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()]
    assert f"Commit #{old['id']} superseded by #{new['id']}." in texts


def test_gitignored_path_is_rejected(client, pid, repo):
    (repo / ".gitignore").write_text("aiteams/static/\n*.log\n")
    (repo / "aiteams" / "static").mkdir(parents=True)
    (repo / "aiteams" / "static" / "index.html").write_text("<p>\n")
    (repo / "debug.log").write_text("x\n")
    (repo / "ok.py").write_text("x = 1\n")
    lead = turn(client, pid, "lead")
    for path in ("aiteams/static/index.html", "debug.log", "aiteams/static"):
        r = client.post("/internal/commits", json={"files": ["ok.py", path], "message": "m"}, headers=lead)
        assert r.status_code == 400 and r.json()["detail"] == f"{path} is gitignored (build output is not committed)"
    assert client.post("/internal/commits", json={"files": ["ok.py", ".gitignore"], "message": "m"}, headers=lead).status_code == 200


# ---- F-36: commit requests may list files in other git repos -------------------------------------------------------
FAIL_HOOK = "#!/bin/sh\necho hook says no >&2\nexit 1\n"


def sibling(tmp_path, name="data-prep", files=None, hook=None, commit=True):
    """A second git repo next to the project (the real case: ~/Intellij/data-prep beside the AITeams project)."""
    r = tmp_path / name
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "Other Test"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "o@t"], cwd=r, check=True)
    for n, t in (files if files is not None else {"etl.py": "v1\n"}).items():
        (r / n).write_text(t)
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=r, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True)
    if hook:
        (r / ".git" / "hooks" / "pre-commit").write_text(hook)
        (r / ".git" / "hooks" / "pre-commit").chmod(0o755)
    return r


def ask(client, pid, role, files, message="feat: x\n\nbody", requires=()):
    return client.post("/internal/commits", json={"files": [str(f) for f in files], "message": message,
                                                  "requires": list(requires)}, headers=turn(client, pid, role))


def approve(client, pid, cid, **body):
    return client.post(f"/api/p/{pid}/commits/{cid}/decide", json={"decision": "approve", **body})


def status(repo):
    return git(repo, "status", "--porcelain")


def test_one_request_two_repos_makes_one_commit_per_repo(client, pid, repo, tmp_path):
    proj = client.app.state.app.projects[pid]
    sib = sibling(tmp_path)
    (repo / "api.py").write_text("api\n")
    (sib / "etl.py").write_text("v2\n")
    r = ask(client, pid, "backend", ["api.py", sib / "etl.py"])
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    real = os.path.realpath(sib / "etl.py")
    assert r.json()["files"] == ["api.py", real]  # project file relative, outside file its absolute realpath
    card = proj.db.message(proj.db.commit(cid)["message_id"])["text"]
    assert f"- {real}" in card
    assert f"`git diff HEAD -- api.py` and `git -C {os.path.realpath(sib)} diff HEAD -- etl.py`" in card  # one hint per repo
    client.post(f"/internal/commits/{cid}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead"))
    r = approve(client, pid, cid)
    assert r.status_code == 200 and r.json()["state"] == "committed", r.text
    sha = proj.db.commit(cid)["sha"]
    assert re.fullmatch(r"[0-9a-f]{7,} data-prep@[0-9a-f]{7,}", sha), sha
    project_sha, other = sha.split(" ")
    assert project_sha == git(repo, "rev-parse", "--short", "HEAD")
    assert other == "data-prep@" + git(sib, "rev-parse", "--short", "HEAD")
    assert git(repo, "log", "-1", "--format=%s") == git(sib, "log", "-1", "--format=%s") == "feat: x"
    assert git(sib, "log", "-1", "--format=%an") == "Other Test" and git(sib, "show", "--stat", "--format=", "HEAD").count("etl.py") == 1
    assert git(repo, "show", "--stat", "--format=", "HEAD").count("api.py") == 1
    assert status(repo) == "" and status(sib) == ""  # both working trees are clean
    assert (sib / "etl.py").read_text() == "v2\n"
    msg = proj.db.messages("dm-backend")[-1]
    assert msg["text"] == f"@lead Committed `{sha}`: feat: x" and msg["mentions"] == ["lead"]


def test_a_request_with_only_outside_files_commits_only_those_repos_and_the_sha_starts_with_a_name(client, pid, repo, tmp_path):
    proj = client.app.state.app.projects[pid]
    sib = sibling(tmp_path, "zeta", {"a.py": "1\n"})
    other = sibling(tmp_path, "alpha", {"b.py": "1\n"})
    (sib / "a.py").write_text("2\n")
    (other / "b.py").write_text("2\n")
    head = git(repo, "rev-parse", "HEAD")
    r = ask(client, pid, "lead", [sib / "a.py", other / "b.py"])  # a Lead's own request: straight to the Master
    assert r.status_code == 200 and r.json()["state"] == "master_review"
    assert approve(client, pid, r.json()["id"]).json()["state"] == "committed"
    sha = proj.db.commit(r.json()["id"])["sha"]
    assert re.fullmatch(r"alpha@[0-9a-f]{7,} zeta@[0-9a-f]{7,}", sha), sha  # no project file; the others sorted by path
    assert git(repo, "rev-parse", "HEAD") == head  # nothing committed in the project
    assert status(sib) == "" and status(other) == ""


def test_a_failing_second_repo_rolls_back_the_first_and_changes_no_working_tree(client, pid, repo, tmp_path):
    proj = client.app.state.app.projects[pid]
    sib = sibling(tmp_path, hook=FAIL_HOOK)
    (repo / "api.py").write_text("api\n")
    (sib / "etl.py").write_text("v2\n")
    head, sib_head = git(repo, "rev-parse", "HEAD"), git(sib, "rev-parse", "HEAD")
    cid = ask(client, pid, "lead", ["api.py", sib / "etl.py"]).json()["id"]
    r = approve(client, pid, cid)
    assert r.status_code == 200 and r.json()["state"] == "failed" and "hook says no" in r.json()["note"], r.text
    assert git(repo, "rev-parse", "HEAD") == head and git(sib, "rev-parse", "HEAD") == sib_head
    assert status(repo) == "?? api.py" and status(sib) == "M etl.py"  # index back where it started in both
    assert git(repo, "diff", "--cached", "--name-only") == "" and git(sib, "diff", "--cached", "--name-only") == ""
    assert (repo / "api.py").read_text() == "api\n" and (sib / "etl.py").read_text() == "v2\n"
    assert "failed" in proj.db.messages("dm-lead")[-1]["text"]
    assert not (repo / ".git" / "index.lock").exists() and not (sib / ".git" / "index.lock").exists()


def test_a_repo_with_no_commits_yet_is_rolled_back_too(client, pid, repo, tmp_path):
    first = sibling(tmp_path, "a-first", {"one.py": "1\n"}, commit=False)  # unborn: its first commit would be ours
    bad = sibling(tmp_path, "b-second", {"two.py": "1\n"}, hook=FAIL_HOOK, commit=False)
    (repo / "api.py").write_text("api\n")
    head = git(repo, "rev-parse", "HEAD")
    cid = ask(client, pid, "lead", ["api.py", first / "one.py", bad / "two.py"]).json()["id"]
    r = approve(client, pid, cid)
    assert r.json()["state"] == "failed" and "hook says no" in r.json()["note"]
    assert git(repo, "rev-parse", "HEAD") == head
    unborn = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=first, capture_output=True)
    assert unborn.returncode != 0  # a-first has no commit again
    assert status(first) == "?? one.py" and status(bad) == "?? two.py" and status(repo) == "?? api.py"
    # the same request lands once the hook stops failing: a first commit in an unborn repo works
    (bad / ".git" / "hooks" / "pre-commit").unlink()
    cid = ask(client, pid, "lead", ["api.py", first / "one.py", bad / "two.py"]).json()["id"]
    assert approve(client, pid, cid).json()["state"] == "committed"
    assert git(first, "log", "--format=%s") == "feat: x" and git(bad, "log", "--format=%s") == "feat: x"


def test_a_group_whose_second_member_fails_in_another_repo_rolls_back_the_first_member_everywhere(client, pid, repo, tmp_path):
    proj = client.app.state.app.projects[pid]
    hook = "#!/bin/sh\nif git diff --cached --name-only | grep -q '^s2.py$'; then echo second says no >&2; exit 1; fi\n"
    sib = sibling(tmp_path, hook=hook)
    for n in ("a.py", "b.py"):
        (repo / n).write_text(n + "\n")
    for n in ("s1.py", "s2.py"):
        (sib / n).write_text(n + "\n")
    base = ask(client, pid, "lead", ["a.py", sib / "s1.py"], "feat: one").json()
    dep = ask(client, pid, "lead", ["b.py", sib / "s2.py"], "feat: two", [base["id"]]).json()
    head, sib_head = git(repo, "rev-parse", "HEAD"), git(sib, "rev-parse", "HEAD")
    r = approve(client, pid, dep["id"], group=True)
    assert r.status_code == 200, r.text
    assert [proj.db.commit(x["id"])["state"] for x in (base, dep)] == ["master_review", "failed"]
    assert "second says no" in proj.db.commit(dep["id"])["note"]
    assert git(repo, "rev-parse", "HEAD") == head and git(sib, "rev-parse", "HEAD") == sib_head  # the first member is undone too
    assert status(repo) == "?? a.py\n?? b.py" and status(sib) == "?? s1.py\n?? s2.py"
    # with the hook gone the whole group lands: two commits in each repo, the project's sha first
    (sib / ".git" / "hooks" / "pre-commit").unlink()
    proj.db.update_commit(dep["id"], state="master_review")
    r = approve(client, pid, dep["id"], group=True)
    assert [x["state"] for x in r.json()["group"]] == ["committed", "committed"], r.text
    assert git(repo, "log", "-2", "--format=%s").splitlines() == ["feat: two", "feat: one"]
    assert git(sib, "log", "-2", "--format=%s").splitlines() == ["feat: two", "feat: one"]
    assert all(re.fullmatch(r"[0-9a-f]{7,} data-prep@[0-9a-f]{7,}", proj.db.commit(x["id"])["sha"]) for x in (base, dep))


def test_outside_refusals_name_the_reason(client, pid, repo, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.py").write_text("x\n")
    sib = sibling(tmp_path, files={"etl.py": "v1\n", ".gitignore": "*.log\n"})
    (sib / "debug.log").write_text("x\n")
    (repo / "ok.py").write_text("x\n")
    cases = [
        (plain / "x.py", "is not inside a git repo; only files in a git repo can be committed"),
        (plain, "is not inside a git repo"),
        (sib / ".git" / "config", "is inside a .git directory and cannot be committed"),
        (sib, "is a directory; list files one by one"),
        (sib / "nope.py", "does not exist and is not tracked"),
        (sib / "debug.log", "is gitignored (build output is not committed)"),
        (plain / "missing" / "y.py", "is not inside a git repo"),
    ]
    for path, why in cases:
        r = ask(client, pid, "lead", ["ok.py", path])
        assert r.status_code == 400 and why in r.json()["detail"], (path, r.text)
    assert ask(client, pid, "lead", ["ok.py", sib / ".gitignore"]).status_code == 200  # a file in a repo is fine
    assert len(client.app.state.app.projects[pid].db.commits("all")) == 1  # the refusals filed nothing


def test_a_file_deleted_in_another_repo_can_be_requested_and_committed(client, pid, repo, tmp_path):
    sib = sibling(tmp_path, files={"old.py": "x\n", "keep.py": "y\n"})
    (sib / "old.py").unlink()
    cid = ask(client, pid, "lead", [sib / "old.py"]).json()["id"]
    assert approve(client, pid, cid).json()["state"] == "committed"
    assert git(sib, "ls-files") == "keep.py" and status(sib) == ""


def test_diff_and_stale_work_on_an_outside_file_in_its_own_repo(client, pid, repo, tmp_path):
    sib = sibling(tmp_path)
    (sib / "etl.py").write_text("v1\nv2\n")
    (sib / "new.py").write_text("n\n")
    (repo / "api.py").write_text("api\n")
    cid = ask(client, pid, "lead", ["api.py", sib / "etl.py", sib / "new.py"]).json()["id"]
    d = client.get(f"/api/p/{pid}/commits/{cid}/diff").json()
    files = {f["path"]: f for f in d["files"]}
    etl, new = files[os.path.realpath(sib / "etl.py")], files[os.path.realpath(sib / "new.py")]
    assert (etl["status"], etl["additions"], etl["deletions"]) == ("modified", 1, 0) and "+++ b/etl.py" in etl["text"]
    assert (new["status"], new["additions"]) == ("added", 1) and "+++ b/new.py" in new["text"]  # untracked: against nothing
    assert files["api.py"]["status"] == "added" and d["stale"] == [] and d["totals"]["files"] == 3
    (sib / "etl.py").write_text("changed after the request\n")  # stale detection on the outside file
    assert client.get(f"/api/p/{pid}/commits/{cid}/diff").json()["stale"] == [os.path.realpath(sib / "etl.py")]
    r = approve(client, pid, cid)
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(cid): [os.path.realpath(sib / "etl.py")]}}  # F-45
    assert git(sib, "log", "--format=%s") == "init"  # nothing was committed anywhere
    assert client.app.state.app.projects[pid].db.commit(cid)["state"] == "lead_review"
    assert approve(client, pid, cid, force=True).status_code == 409  # not a state the Master can commit
    assert client.post(f"/internal/commits/{cid}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead")).status_code == 200
    assert approve(client, pid, cid).json()["state"] == "committed"  # the Lead approved the changed file
    assert git(sib, "show", "HEAD:etl.py") == "changed after the request"


def test_outside_files_have_no_locks_and_an_absolute_project_path_stays_relative(client, pid, repo, tmp_path):
    proj = client.app.state.app.projects[pid]
    sib = sibling(tmp_path)
    (sib / "etl.py").write_text("v2\n")
    (repo / "api.py").write_text("api\n")
    real = os.path.realpath(sib / "etl.py")
    proj.db.x("INSERT INTO locks(path, role, turn_id, created_at) VALUES(?,?,?,?)", real, "frontend", None, 1.0)
    r = ask(client, pid, "backend", [repo / "api.py", sib / "etl.py"])  # frontend "holds" the outside path: nobody checks
    assert r.status_code == 200 and r.json()["files"] == ["api.py", real], r.text


def test_a_second_repo_that_is_the_projects_own_repo_merges_into_one_commit(client, pid, repo, tmp_path):
    """A project that is a subdirectory of a repo: a file elsewhere in that repo is the same repo, so one commit."""
    sub = repo / "sub"
    sub.mkdir()
    (sub / "in.py").write_text("in\n")
    (repo / "top.py").write_text("top\n")
    groups = workflow._repo_groups(str(sub), ["in.py", str(repo / "top.py")])
    assert groups == [(os.path.realpath(repo), ["sub/in.py", "top.py"])]
    ok, sha = workflow._commit_sync(str(sub), ["in.py", str(repo / "top.py")], "feat: both")
    assert ok and re.fullmatch(r"[0-9a-f]{7,}", sha), sha  # the project's repo: a bare sha, one commit
    assert git(repo, "show", "--stat", "--format=%s", "HEAD").count(".py") == 2


def test_where_and_repo_groups_put_the_project_first_and_sort_the_rest(client, pid, repo, tmp_path):
    z, a = sibling(tmp_path, "zzz", {"f.py": "1\n"}), sibling(tmp_path, "aaa", {"g.py": "1\n"})
    (repo / "p.py").write_text("p\n")
    root = os.path.realpath(repo)
    assert workflow._where(str(repo), "p.py") == (str(repo), "p.py")
    assert workflow._where(str(repo), os.path.realpath(z / "f.py")) == (os.path.realpath(z), "f.py")
    assert workflow._repo_top(str(tmp_path / "nowhere" / "deep")) is None
    assert workflow._repo_top(str(a / "not-yet" / "dir")) == os.path.realpath(a)  # a deleted file's directory
    files = [os.path.realpath(z / "f.py"), "p.py", os.path.realpath(a / "g.py"), "p.py"]
    assert workflow._repo_groups(str(repo), files) == [(root, ["p.py"]), (os.path.realpath(a), ["g.py"]),
                                                       (os.path.realpath(z), ["f.py"])]
    assert workflow._repo_groups(str(repo), [os.path.realpath(z / "f.py")]) == [(os.path.realpath(z), ["f.py"])]
