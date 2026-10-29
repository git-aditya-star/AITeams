"""F-39 B2, the commit half: a feature with a worktree resolves, checks, diffs and commits its request paths in the tree, on its
branch; a tree on another branch fails the commit; `current` mode commits in the main folder after the same branch check."""
import hashlib
import os
import shutil
import subprocess

import pytest

from tests.test_branches import approve_new, client, decide, feature, git, hdr, pid, propose, proj_of  # noqa: F401 (fixtures)


@pytest.fixture(autouse=True)
def no_guard(monkeypatch):
    """The F-16 refuse-once guard is tested in test_new_messages.py; these tests drive many steps through fresh turns."""
    from aiteams import workflow

    async def off(*a, **k):
        return None
    monkeypatch.setattr(workflow, "guard", off)


@pytest.fixture
def tree(client, pid, repo):
    """F-1 on a new branch in its own worktree; returns (tree path, branch). The repo can commit (the daemon runs git)."""
    git(repo, "config", "user.name", "Aditya Test")
    git(repo, "config", "user.email", "t@t")
    approve_new(client, pid)
    b = feature(client, pid)["branch"]
    return b["tree"], b["branch"]


def req(client, pid, files, message="feat: x", role="backend", feature="F-1", **extra):
    return client.post("/internal/commits", json={"feature": feature, "files": files, "message": message, **extra},
                       headers=hdr(client, pid, role))


def land(client, pid, cid, **body):
    """Lead approves, then the Master commits."""
    assert client.post(f"/internal/commits/{cid}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead")).status_code == 200
    return client.post(f"/api/p/{pid}/commits/{cid}/decide", json={"decision": "approve", **body})


def last_text(client, pid):
    return client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]["text"]


def test_a_request_resolves_in_the_tree_and_lands_on_the_features_branch_not_main(client, pid, repo, tree):
    tdir, branch = tree
    (open(os.path.join(tdir, "api.py"), "w")).write("def hello():\n    return 'hi'\n")
    (repo / "api.py").write_text("main folder copy\n")  # same name in the main folder: must not be what is committed
    main_head = git(repo, "rev-parse", "HEAD")
    r = req(client, pid, ["api.py", os.path.join(tdir, "api.py")])  # relative, and absolute inside the tree: one file
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["files"] == ["api.py"]
    proj = proj_of(client, pid)
    row = proj.db.commit(c["id"])
    assert row["hashes"]["api.py"] == __import__("hashlib").sha256(b"def hello():\n    return 'hi'\n").hexdigest()  # the tree's file
    card = next(m for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()
                if m["ref"] == f"commit:{c['id']}")
    assert f"`git -C {tdir} diff HEAD -- api.py`" in card["text"]  # the Lead's cwd is the project: the hint names the tree
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead")).status_code == 200
    d = client.get(f"/api/p/{pid}/commits/{c['id']}/diff").json()
    assert "+def hello():" in d["diff"] and d["stale"] == [] and d["files"][0]["status"] == "added"
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["state"] == "committed", r.text
    sha = r.json()["sha"]
    assert git(tdir, "rev-parse", "--abbrev-ref", "HEAD") == branch and git(tdir, "log", "-1", "--format=%s") == "feat: x"
    assert git(tdir, "show", "--name-only", "--format=", "HEAD") == "api.py" and git(tdir, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == main_head  # main did not move
    assert git(repo, "branch", "--show-current") == "main" and (repo / "api.py").read_text() == "main folder copy\n"
    assert last_text(client, pid) == f"@lead Committed `{sha}` on {branch}: feat: x"


def test_a_main_folder_path_on_a_treed_feature_is_refused_with_the_way_out(client, pid, repo, tree):
    tdir, _ = tree
    (repo / "foo.py").write_text("x")
    r = req(client, pid, [str(repo / "foo.py")])
    assert r.status_code == 400
    assert f"is in the main folder, but this feature commits from its worktree {tdir}: list {tdir}/foo.py or foo.py" in r.json()["detail"]
    r = req(client, pid, ["foo.py"])  # relative means relative to the tree, where it doesn't exist
    assert r.status_code == 400 and "does not exist and is not tracked" in r.json()["detail"]
    assert proj_of(client, pid).db.commits("lead_review") == []


def test_other_repos_keep_f36s_rule(client, pid, repo, tree, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    (other / "n.txt").write_text("n")
    tdir, _ = tree
    open(os.path.join(tdir, "t.txt"), "w").write("t")
    r = req(client, pid, ["t.txt", str(other / "n.txt")])
    assert r.status_code == 200, r.text
    assert r.json()["files"] == ["t.txt", os.path.realpath(other / "n.txt")]


def test_a_tree_files_lock_is_its_absolute_realpath(client, pid, repo, tree):
    tdir, _ = tree
    open(os.path.join(tdir, "api.py"), "w").write("x")
    db = proj_of(client, pid).db
    t = hdr(client, pid, "frontend")  # a running turn of another role
    turn_id = db.one("SELECT id FROM turns WHERE role='frontend' ORDER BY id DESC")["id"]
    db.acquire_lock("api.py", "frontend", turn_id)  # the project's api.py is another file: no conflict
    assert req(client, pid, ["api.py"]).status_code == 200
    db.acquire_lock(os.path.realpath(os.path.join(tdir, "api.py")), "frontend", turn_id)
    r = req(client, pid, ["api.py"])
    assert r.status_code == 409 and "api.py is locked by frontend" in r.json()["detail"]
    assert t  # (the turn header is only there to make frontend's turn exist)


def test_the_lock_routes_key_a_worktree_file_by_absolute_path(client, pid, repo, tree):
    tdir, _ = tree
    f = os.path.realpath(os.path.join(tdir, "src.py"))
    h = hdr(client, pid, "backend")
    r = client.post("/internal/locks/acquire", json={"path": f}, headers=h)
    assert r.status_code == 200 and r.json() == {"ok": True, "path": f}
    assert [l["path"] for l in client.get("/internal/locks", headers=h).json()] == [f]
    r = client.post("/internal/locks/acquire", json={"path": "docs.md"}, headers=h)
    assert r.json()["path"] == "docs.md"  # a project file keeps its relative key
    other = client.post("/internal/locks/acquire", json={"path": "/etc/hosts"}, headers=h)
    assert other.status_code == 400 and "outside the project" in other.json()["detail"]
    r = client.post("/internal/locks/release", json={"path": f}, headers=h)
    assert r.json() == {"ok": True, "path": f, "released": True}


def test_stale_is_judged_on_the_trees_file(client, pid, repo, tree):
    tdir, _ = tree
    p = os.path.join(tdir, "api.py")
    open(p, "w").write("one\n")
    (repo / "api.py").write_text("main\n")
    c = req(client, pid, ["api.py"]).json()["id"]
    client.post(f"/internal/commits/{c}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead"))
    (repo / "api.py").write_text("main changed\n")  # the project's file moving is irrelevant
    assert client.get(f"/api/p/{pid}/commits/{c}/diff").json()["stale"] == []
    open(p, "w").write("two\n")  # the tree's file moving is
    assert client.get(f"/api/p/{pid}/commits/{c}/diff").json()["stale"] == ["api.py"]
    head = git(tdir, "rev-parse", "HEAD")
    r = client.post(f"/api/p/{pid}/commits/{c}/decide", json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(c): ["api.py"]}}  # F-45
    assert git(tdir, "rev-parse", "HEAD") == head and proj_of(client, pid).db.commit(c)["state"] == "lead_review"


def test_the_hashes_at_approval_are_taken_in_the_tree(client, pid, repo, tree):
    """F-45: the Lead's approval re-hashes the tree's file (not the main folder's copy of the same path), so an edit before
    the approval is reviewed, an edit of the main folder's copy never trips the guard, and one in the tree afterwards does."""
    tdir, _ = tree
    p = os.path.join(tdir, "api.py")
    open(p, "w").write("one\n")
    (repo / "api.py").write_text("main\n")
    c = req(client, pid, ["api.py"]).json()["id"]
    open(p, "w").write("two\n")  # between filing and the Lead's approval: the Lead reviews this
    assert client.post(f"/internal/commits/{c}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead")).status_code == 200
    db = proj_of(client, pid).db
    assert db.commit(c)["hashes"] == {"api.py": hashlib.sha256(b"two\n").hexdigest()}
    (repo / "api.py").write_text("main again\n")  # the main folder's copy of the path
    open(p, "w").write("three\n")  # the tree's file, after the approval
    r = client.post(f"/api/p/{pid}/commits/{c}/decide", json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == {"sent_back": {str(c): ["api.py"]}}
    open(p, "w").write("two\n")  # back to what was approved: not a change
    r = client.post(f"/api/p/{pid}/commits/{c}/decide", json={"decision": "approve"})
    assert r.status_code == 409  # it is in lead_review now, so the Master cannot commit it
    assert client.post(f"/internal/commits/{c}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead")).status_code == 200
    r = client.post(f"/api/p/{pid}/commits/{c}/decide", json={"decision": "approve"})
    assert r.status_code == 200 and git(tdir, "show", "HEAD:api.py") == "two"
    assert (repo / "api.py").read_text() == "main again\n"


def test_a_tree_on_another_branch_fails_the_commit_and_commits_nothing(client, pid, repo, tree):
    tdir, branch = tree
    open(os.path.join(tdir, "api.py"), "w").write("x")
    c = req(client, pid, ["api.py"]).json()["id"]
    git(tdir, "checkout", "-q", "-b", "wrong")  # a role ran `git checkout` in the tree
    before = git(tdir, "rev-parse", "HEAD")
    r = land(client, pid, c)
    assert r.status_code == 200 and r.json()["state"] == "failed"
    assert r.json()["note"] == f"the tree is on wrong, F-1's branch is {branch}"
    assert git(tdir, "rev-parse", "HEAD") == before and git(tdir, "diff", "--cached", "--name-only") == ""
    assert f"the tree is on wrong, F-1's branch is {branch}" in last_text(client, pid)
    git(tdir, "checkout", "-q", "--detach")  # a detached HEAD says so
    c2 = req(client, pid, ["api.py"]).json()["id"]
    r = land(client, pid, c2)
    assert r.json()["state"] == "failed" and r.json()["note"] == f"the tree is on a detached HEAD, F-1's branch is {branch}"


def test_a_group_checks_every_branch_first_and_commits_nothing_when_one_is_off(client, pid, repo, tree):
    tdir, branch = tree
    open(os.path.join(tdir, "a.py"), "w").write("a")
    open(os.path.join(tdir, "b.py"), "w").write("b")
    a = req(client, pid, ["a.py"], "feat: a").json()["id"]
    b = req(client, pid, ["b.py"], "feat: b", role="frontend", requires=[a]).json()["id"]
    for cid, role in ((a, "lead"), (b, "lead")):
        client.post(f"/internal/commits/{cid}/review", json={"decision": "approve"}, headers=hdr(client, pid, role))
    git(tdir, "checkout", "-q", "-b", "wrong")
    before = git(tdir, "rev-parse", "HEAD")
    r = client.post(f"/api/p/{pid}/commits/{a}/decide", json={"decision": "approve", "group": True})
    assert r.status_code == 200, r.text
    assert git(tdir, "rev-parse", "HEAD") == before
    assert proj_of(client, pid).db.commit(a)["state"] == "failed"
    assert f"the tree is on wrong, F-1's branch is {branch}" in last_text(client, pid)
    git(tdir, "checkout", "-q", branch)  # back on the branch: a fresh group of two lands, both naming the branch
    for n in ("c", "d"):
        open(os.path.join(tdir, f"{n}.py"), "w").write(n)
    c = req(client, pid, ["c.py"], "feat: c").json()["id"]
    d = req(client, pid, ["d.py"], "feat: d", role="frontend", requires=[c]).json()["id"]
    for cid in (c, d):
        client.post(f"/internal/commits/{cid}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead"))
    r = client.post(f"/api/p/{pid}/commits/{c}/decide", json={"decision": "approve", "group": True})
    assert r.status_code == 200, r.text
    assert git(tdir, "log", "--format=%s", "-2") == "feat: d\nfeat: c"
    texts = [m["text"] for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()
             if m["author"] == "master"]
    assert any(t.endswith(f"on {branch}: feat: c") for t in texts) and any(t.endswith(f"on {branch}: feat: d") for t in texts)


def test_current_mode_commits_in_the_main_folder_after_the_branch_check(client, pid, repo):
    git(repo, "config", "user.name", "Aditya Test")
    git(repo, "config", "user.email", "t@t")
    a = propose(client, pid, mode="current").json()["id"]
    assert decide(client, pid, a).status_code == 200
    assert feature(client, pid)["branch"]["tree"] is None
    (repo / "cur.py").write_text("x")
    c = req(client, pid, ["cur.py"]).json()["id"]
    r = land(client, pid, c)
    assert r.json()["state"] == "committed" and git(repo, "log", "-1", "--format=%s") == "feat: x"
    assert last_text(client, pid) == f"@lead Committed `{r.json()['sha']}` on main: feat: x"
    (repo / "cur2.py").write_text("y")
    c2 = req(client, pid, ["cur2.py"]).json()["id"]
    git(repo, "checkout", "-q", "-b", "elsewhere")
    before = git(repo, "rev-parse", "HEAD")
    r = land(client, pid, c2)
    assert r.json()["state"] == "failed" and r.json()["note"] == "the main folder is on elsewhere, F-1's branch is main"
    assert git(repo, "rev-parse", "HEAD") == before


def test_a_feature_without_a_branch_row_is_unchanged(client, pid, repo):
    git(repo, "config", "user.name", "Aditya Test")
    git(repo, "config", "user.email", "t@t")
    (repo / "plain.py").write_text("x")
    c = req(client, pid, ["plain.py"]).json()["id"]  # F-1 is ungated in the tests and has no branch
    r = land(client, pid, c)
    assert r.json()["state"] == "committed"
    assert last_text(client, pid) == f"@lead Committed `{r.json()['sha']}`: feat: x"  # no "on <branch>"


def test_a_tree_that_vanished_stops_a_new_request(client, pid, repo, tree):
    tdir, _ = tree
    shutil.rmtree(tdir)
    (repo / "x.py").write_text("x")
    r = req(client, pid, ["x.py"])
    assert r.status_code == 409 and f"works in {tdir}, but that folder is gone" in r.json()["detail"]


def test_the_review_script_gets_the_tree_and_branch(client, pid, repo, tree):
    tdir, branch = tree
    open(os.path.join(tdir, "api.py"), "w").write("x")
    c = req(client, pid, ["api.py"]).json()["id"]
    got = client.get(f"/internal/commits/{c}/files", headers=hdr(client, pid, "lead")).json()
    assert got["files"] == ["api.py"] and got["tree"] == tdir and got["branch"] == branch and got["mixed"] is False
    # a request on no feature has none
    n = client.post("/internal/commits", json={"files": ["README.md"], "message": "docs: x"}, headers=hdr(client, pid, "backend")).json()["id"]
    got = client.get(f"/internal/commits/{n}/files", headers=hdr(client, pid, "lead")).json()
    assert got["tree"] is None and got["branch"] is None and got["mixed"] is False
    # a group across a treed feature and no feature is mixed
    m = req(client, pid, ["api.py"], role="frontend", requires=[n]).json()["id"]
    got = client.get(f"/internal/commits/{m}/files", headers=hdr(client, pid, "lead")).json()
    assert got["tree"] is None and got["mixed"] is True


def test_diff_text_for_a_tracked_change_in_the_tree(client, pid, repo, tree):
    tdir, _ = tree
    p = os.path.join(tdir, "README.md")  # tracked in the tree (it is in the repo)
    open(p, "a").write("tree edit\n")
    c = req(client, pid, ["README.md"]).json()["id"]
    d = client.get(f"/api/p/{pid}/commits/{c}/diff").json()
    assert "+tree edit" in d["diff"] and d["files"][0]["status"] == "modified"
    assert subprocess.run(["git", "diff", "--name-only"], cwd=repo, capture_output=True, text=True).stdout.strip() == ""  # main clean
