"""Commit requests: Lead review, Master approve (real git), stale/force, lead-direct, reject, lock conflicts."""
import subprocess

import pytest
from fastapi.testclient import TestClient

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
    assert last["author"] == "master" and last["type"] == "Decision" and last["mentions"] == ["lead"]
    assert last["text"] == f"@lead Committed `{r['sha']}`: F-1: add export (edited)"
    # F-20: a commit wakes only the Lead, who sequences the next step; backend sees the line in its next news
    assert "lead" in {w["role"] for w in proj.db.queued_wakes()}
    assert not any(w["role"] == "backend" and last["id"] in w["message_ids"] for w in proj.db.queued_wakes())
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
    client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=turn(client, pid, "lead"))
    proj.db.x("DELETE FROM wake_queue")
    if outcome == "failed":
        (repo / "q.py").unlink()  # git add of a vanished, untracked path fails (the sandbox's commit_failed path)
        r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve", "force": True}).json()
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
