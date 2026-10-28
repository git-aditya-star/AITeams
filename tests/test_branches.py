"""F-39 B1: the Branch card (propose_branch -> the Master edits and approves -> a worktree), the gate on commit requests,
the warnings, the feature's `branch`, the header line and the Master's /branches routes. All on temp repos."""
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from aiteams import branches, workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, BRIEF, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


def git(repo, *args):
    r = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def pid(client, repo):
    """A project repo on `main` with one commit, a .gitignore like the real one, and untracked tooling to link."""
    (repo / ".gitignore").write_text(".venv/\nui/node_modules/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "branch", "-M", "main")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "ui" / "node_modules").mkdir(parents=True)
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export orders", "starter": "product"})
    client.app.state.app.projects[pid].db.update_feature(1, lead="lead")
    return pid


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def hdr(client, pid, role):
    return running_turn(proj_of(client, pid).db, role, pid=pid)[1]


def propose(client, pid, role="lead", **body):
    body = {"feature": "F-1", "mode": "new", "why": "Keeps F-1 off the main folder.", **body}
    return client.post("/internal/branches", json=body, headers=hdr(client, pid, role))


def decide(client, pid, aid, decision="approve", **body):
    return client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": decision, **body})


def feature(client, pid, n=1):
    return next(f for f in client.get(f"/api/p/{pid}/features").json() if f["id"] == n)


def approve_new(client, pid, **payload):
    a = propose(client, pid).json()["id"]
    r = decide(client, pid, a, **({"payload": payload} if payload else {}))
    assert r.status_code == 200, r.text
    return a


# ---- the card ------------------------------------------------------------------------------------------------------------
def test_a_proposal_is_a_pending_branch_card_with_the_daemons_fill_in(client, pid, repo):
    proj = proj_of(client, pid)
    r = propose(client, pid)
    assert r.status_code == 200, r.text
    a = proj.db.approval(r.json()["id"])
    assert (a["title"], a["state"], a["role"], a["feature_id"]) == ("Branch: F-1", "pending", "lead", 1)
    assert a["payload"] == {"mode": "new", "branch": "feat/f1-export-orders", "base": "main", "shared_from": None,
                            "why": "Keeps F-1 off the main folder.", "current": "main", "worktrees": [], "warnings": [],
                            "tree": branches.tree_path(str(repo), 1)}  # where the new worktree would go: the UI can't compute it
    assert "a new branch feat/f1-export-orders from main" in a["summary"] and "Keeps F-1 off the main folder." in a["summary"]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [x["id"] for x in inbox["approvals"]] == [a["id"]] and inbox["approvals"][0]["payload"]["mode"] == "new"
    m = proj.db.message(a["message_id"])
    assert (m["type"], m["mentions"]) == ("Approval", ["master"]) and m["approval"]["payload"]["branch"] == "feat/f1-export-orders"
    assert feature(client, pid)["branch"]["state"] == "pending" and feature(client, pid)["branch"]["gated"] is True


def test_approving_creates_the_worktree_on_the_new_branch_and_tells_the_lead_where_it_lands(client, pid, repo):
    proj = proj_of(client, pid)
    events, orig = [], proj.hub.broadcast

    async def spy(event, data):
        events.append((event, data))
        await orig(event, data)
    proj.hub.broadcast = spy
    a = approve_new(client, pid)
    tree = branches.tree_path(str(repo), 1)
    assert tree == os.path.join(os.path.dirname(os.path.realpath(repo)), "AITeams-worktrees", "repo", "f1")
    assert git(tree, "symbolic-ref", "--short", "HEAD") == "feat/f1-export-orders" and git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(tree, "rev-parse", "HEAD") == git(repo, "rev-parse", "main")  # it starts at the base
    assert subprocess.run(["git", "config", "branch.feat/f1-export-orders.merge"], cwd=repo, capture_output=True).stdout == b""  # --no-track
    b = feature(client, pid)["branch"]
    assert (b["state"], b["mode"], b["branch"], b["base"], b["tree"], b["tree_state"]) == (
        "approved", "new", "feat/f1-export-orders", "main", tree, "live")
    dec = proj.db.messages("feature-1")[-1]
    assert dec["type"] == "Decision" and dec["mentions"] == ["lead"]
    assert dec["text"].endswith(f"Work on F-1 lands on feat/f1-export-orders in {tree}.")
    assert proj.db.approval(a)["state"] == "approved" and proj.db.approval(a)["payload"]["tree"] == tree  # what was approved
    assert [d["branch"]["state"] for e, d in events if e == "feature" and d["id"] == 1][-1] == "approved"  # the chip updates live


def test_the_tree_borrows_the_tooling_and_git_status_stays_clean(client, pid, repo):
    approve_new(client, pid)
    tree = branches.tree_path(str(repo), 1)
    for rel in (".venv", "ui/node_modules"):
        assert os.path.islink(os.path.join(tree, rel)) and os.path.realpath(os.path.join(tree, rel)) == os.path.realpath(repo / rel)
    assert git(tree, "status", "--porcelain") == ""  # the symlinks count as ignored (info/exclude)
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count("/.venv") == 1 and exclude.count("/ui/node_modules") == 1
    git(repo, "worktree", "remove", tree)  # no --force needed: nothing untracked is left in it
    assert not os.path.exists(tree)


def test_a_second_tree_does_not_duplicate_the_exclude_lines(client, pid, repo):
    approve_new(client, pid)
    client.post(f"/api/p/{pid}/features", json={"text": "Search", "starter": "product"})
    proj_of(client, pid).db.update_feature(2, lead="lead")
    a = propose(client, pid, feature="F-2").json()["id"]
    assert decide(client, pid, a).status_code == 200
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count("/.venv") == 1 and exclude.count("/ui/node_modules") == 1
    assert os.path.isdir(branches.tree_path(str(repo), 2))


def test_the_masters_edits_on_the_card_win(client, pid, repo):
    proj = proj_of(client, pid)
    git(repo, "branch", "dev-base")
    a = propose(client, pid).json()["id"]
    r = decide(client, pid, a, payload={"branch": "feat/edited", "base": "dev-base", "why": "ignored", "tree": "/etc"})
    assert r.status_code == 200, r.text
    tree = branches.tree_path(str(repo), 1)
    assert git(tree, "symbolic-ref", "--short", "HEAD") == "feat/edited"
    b = feature(client, pid)["branch"]
    assert (b["branch"], b["base"], b["tree"]) == ("feat/edited", "dev-base", tree)  # only mode/branch/base/shared_from are editable
    stored = proj.db.approval(a)["payload"]
    assert stored["branch"] == "feat/edited" and stored["why"] == "Keeps F-1 off the main folder." and stored["tree"] == tree


def test_current_mode_records_the_checked_out_branch_and_makes_no_tree(client, pid, repo):
    a = propose(client, pid, mode="current").json()["id"]
    assert proj_of(client, pid).db.approval(a)["payload"]["branch"] is None
    assert decide(client, pid, a).status_code == 200
    b = feature(client, pid)["branch"]
    assert (b["state"], b["mode"], b["branch"], b["tree"], b["base"]) == ("approved", "current", "main", None, None)
    assert proj_of(client, pid).db.messages("feature-1")[-1]["text"].endswith("Work on F-1 lands on main in the main folder.")
    assert not os.path.exists(os.path.join(os.path.dirname(os.path.realpath(repo)), "AITeams-worktrees"))


def test_shared_mode_uses_another_features_worktree_without_a_new_one(client, pid, repo):
    approve_new(client, pid)
    client.post(f"/api/p/{pid}/features", json={"text": "Search", "starter": "product"})
    proj_of(client, pid).db.update_feature(2, lead="lead")
    r = propose(client, pid, feature="F-2", mode="shared", shared_from="F-1")
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["worktrees"] == [{"feature": "F-1", "branch": "feat/f1-export-orders", "tree": branches.tree_path(str(repo), 1)}] \
        or r.json()["payload"]["shared_from"] == "F-1"
    assert decide(client, pid, r.json()["id"]).status_code == 200
    b1, b2 = feature(client, pid, 1)["branch"], feature(client, pid, 2)["branch"]
    assert (b2["mode"], b2["branch"], b2["tree"], b2["shared_from"]) == ("shared", b1["branch"], b1["tree"], 1)
    assert not os.path.exists(branches.tree_path(str(repo), 2))
    assert git(repo, "worktree", "list").count("\n") == 1  # main + F-1's tree only


# ---- validation ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("body,why", [
    ({"branch": "main"}, "branch main already exists"),
    ({"branch": "bad name"}, "'bad name' is not a valid branch name"),
    ({"branch": "a..b"}, "is not a valid branch name"),
    ({"base": "nope/missing"}, "base nope/missing doesn't resolve to a commit"),
    ({"mode": "wat"}, "mode must be new, current or shared"),
    ({"mode": "shared", "shared_from": "F-9"}, "F-9 has no live worktree to share"),
    ({"mode": "shared"}, "has no live worktree to share"),
])
def test_a_bad_proposal_is_a_400_with_a_plain_reason_and_files_nothing(client, pid, body, why):
    r = propose(client, pid, **body)
    assert r.status_code == 400 and why in r.json()["detail"], r.text
    assert proj_of(client, pid).db.approvals("all") == []


def test_only_the_features_lead_proposes_and_only_once(client, pid):
    for role in ("backend", "product", "manager"):
        assert propose(client, pid, role=role).status_code == 403
    r = propose(client, pid, role="lead-2")  # F-1 belongs to Lead 1
    assert r.status_code == 409 and "belongs to Lead 1" in r.json()["detail"]
    assert propose(client, pid, feature="F-99").status_code == 404
    a = propose(client, pid).json()["id"]
    r = propose(client, pid)
    assert r.status_code == 409 and "already waiting" in r.json()["detail"]
    assert decide(client, pid, a).status_code == 200
    r = propose(client, pid)
    assert r.status_code == 409 and r.json()["detail"] == "F-1 already works on feat/f1-export-orders"


def test_a_new_card_is_allowed_after_a_changes_request_or_a_withdrawal(client, pid):
    a = propose(client, pid).json()["id"]
    assert decide(client, pid, a, "changes", note="use main as the base").status_code == 200
    assert feature(client, pid)["branch"]["state"] == "changes"
    b = propose(client, pid, branch="feat/other").json()["id"]
    assert client.post("/internal/withdraw", json={"kind": "approval", "id": b, "reason": "wrong name"},
                       headers=hdr(client, pid, "lead")).status_code == 200
    assert feature(client, pid)["branch"]["state"] == "changes"  # the withdrawn one is skipped: the latest live card says so
    assert propose(client, pid, branch="feat/third").status_code == 200


# ---- warnings: shared-looking branches are warned about, never refused ------------------------------------------------------
@pytest.mark.parametrize("name,shared", [
    ("main", True), ("master", True), ("trunk", True), ("dev", True), ("develop", True), ("develop-x", True),
    ("development", True), ("development-pg", True), ("release", True), ("release/1.0", True), ("releases", True),
    ("staging", True), ("stage", True), ("production", True), ("prod", True), ("hotfix/urgent", True),
    ("origin/main", True), ("origin/release/2.1", True), ("MAIN", True), ("Release-1", True),
    ("feat/x", False), ("feature/dev-tools", False), ("my-main", False), ("devops", False), ("fix/release-notes", False),
    ("domain", False), ("feat/f39-worktrees", False),
])
def test_shared_re(name, shared):
    assert bool(branches.SHARED_RE.match(name)) is shared, name


def test_each_mode_warns_when_the_work_would_land_on_a_shared_looking_branch(client, pid, repo):
    db = proj_of(client, pid).db
    new = branches.check(str(repo), db, "new", "release/1.0", "main")
    assert new["errors"] == [] and len(new["warnings"]) == 1 and "release/1.0 looks like a shared branch" in new["warnings"][0]
    assert branches.check(str(repo), db, "new", "feat/x", "main") == {"warnings": [], "errors": []}
    cur = branches.check(str(repo), db, "current")  # the main folder is on main
    assert cur["errors"] == [] and "main looks like a shared branch" in cur["warnings"][0]
    git(repo, "checkout", "-q", "-b", "feat/side")
    assert branches.check(str(repo), db, "current") == {"warnings": [], "errors": []}
    git(repo, "checkout", "-q", "--detach")
    assert "detached HEAD" in branches.check(str(repo), db, "current")["errors"][0]
    git(repo, "checkout", "-q", "main")
    db.set_feature_branch(1, mode="new", branch="develop", base="main", tree="/x/f1")
    assert "develop looks like a shared branch" in branches.check(str(repo), db, "shared", shared_from="F-1")["warnings"][0]
    db.set_feature_branch(1, tree_state="removed")  # a removed tree can't be shared
    assert "no live worktree" in branches.check(str(repo), db, "shared", shared_from="F-1")["errors"][0]


def test_a_shared_looking_branch_is_warned_about_on_the_card_and_still_approved(client, pid, repo):
    r = propose(client, pid, branch="release/1.0")
    assert r.status_code == 200 and "release/1.0 looks like a shared branch" in r.json()["payload"]["warnings"][0]
    assert decide(client, pid, r.json()["id"]).status_code == 200  # never refused: the Master decides
    assert git(branches.tree_path(str(repo), 1), "symbolic-ref", "--short", "HEAD") == "release/1.0"


def test_the_check_route_recomputes_the_warnings_for_the_edited_values(client, pid, repo):
    def chk(**q):
        return client.get(f"/api/p/{pid}/branches/check", params=q).json()
    assert chk(mode="new", branch="feat/x", base="main") == {"warnings": [], "errors": []}
    r = chk(mode="new", branch="dev", base="main")
    assert r["errors"] == [] and "dev looks like a shared branch" in r["warnings"][0]
    assert chk(mode="new", branch="main", base="main")["errors"] == ["branch main already exists"]
    assert chk(mode="new", branch="feat/y", base="nope")["errors"] == ["base nope doesn't resolve to a commit"]
    assert "main looks like a shared branch" in chk(mode="current")["warnings"][0]
    assert client.get(f"/api/p/{pid}/branches/check").status_code == 200  # defaults: mode new, nothing named
    assert client.get("/api/p/nope/branches/check").status_code == 404


def test_the_branches_route_lists_what_the_card_offers(client, pid, repo, tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "branch", "feat/older")
    git(repo, "commit", "-q", "--allow-empty", "-m", "newer")
    git(repo, "branch", "feat/newest")
    o = client.get(f"/api/p/{pid}/branches").json()
    assert o["current"] == "main" and o["default_base"] == "origin/main" and o["worktrees"] == []
    assert o["local"][0] == "feat/newest" and set(o["local"]) == {"main", "feat/older", "feat/newest"}  # newest commit first
    assert o["remote"] == ["origin/main"]  # no origin/HEAD
    assert o["branches"][0] == {"name": "feat/newest", "remote": False}  # the card's list: [{name, remote}], newest first
    assert {(b["name"], b["remote"]) for b in o["branches"]} == {("main", False), ("feat/older", False), ("feat/newest", False),
                                                                ("origin/main", True)}
    approve_new(client, pid)
    assert client.get(f"/api/p/{pid}/branches").json()["worktrees"] == [
        {"feature": "F-1", "branch": "feat/f1-export-orders", "tree": branches.tree_path(str(repo), 1)}]
    assert propose(client, pid, feature="F-1").status_code == 409  # F-1 has its branch now
    assert client.get("/api/p/nope/branches").status_code == 404


def test_an_unreachable_origin_never_stops_a_proposal(client, pid, repo, tmp_path):
    git(repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    r = propose(client, pid)
    assert r.status_code == 200 and r.json()["payload"]["base"] == "main"  # origin/main doesn't resolve: the local main


def test_the_default_base_prefers_origin_main(client, pid, repo, tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    assert propose(client, pid).json()["payload"]["base"] == "origin/main"


# ---- a failure keeps the card pending -------------------------------------------------------------------------------------------
def test_a_failed_worktree_add_is_a_409_and_the_card_stays_pending(client, pid, repo):
    proj = proj_of(client, pid)
    a = propose(client, pid).json()["id"]
    tree = branches.tree_path(str(repo), 1)
    os.makedirs(tree)
    open(os.path.join(tree, "squatter.txt"), "w").write("x")
    r = decide(client, pid, a)
    assert r.status_code == 409 and "already exists and isn't empty" in r.json()["detail"]
    assert proj.db.approval(a)["state"] == "pending" and proj.db.feature_branch(1) is None
    assert [x["id"] for x in client.get(f"/api/p/{pid}/inbox").json()["approvals"]] == [a]
    os.remove(os.path.join(tree, "squatter.txt"))
    assert decide(client, pid, a).status_code == 200 and proj.db.approval(a)["state"] == "approved"  # retry works


def test_a_base_or_name_that_went_bad_after_the_request_is_a_409_too(client, pid, repo):
    proj = proj_of(client, pid)
    git(repo, "branch", "temp-base")
    a = propose(client, pid, base="temp-base").json()["id"]
    git(repo, "branch", "-D", "temp-base")
    r = decide(client, pid, a)
    assert r.status_code == 409 and "base temp-base doesn't resolve" in r.json()["detail"]
    git(repo, "branch", "feat/export-orders-taken")
    r = decide(client, pid, a, payload={"branch": "feat/export-orders-taken", "base": "main"})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    r = decide(client, pid, a, payload={"mode": "shared", "shared_from": "F-5"})
    assert r.status_code == 409 and "no live worktree" in r.json()["detail"]
    assert proj.db.approval(a)["state"] == "pending" and not os.path.exists(branches.tree_path(str(repo), 1))


def test_changes_and_reject_create_nothing_and_a_payload_is_only_for_branch_and_pr_cards(client, pid, repo):
    a = propose(client, pid).json()["id"]
    assert decide(client, pid, a, "changes", note="pick another base", payload={"branch": "feat/z"}).status_code == 200
    assert not os.path.exists(branches.tree_path(str(repo), 1)) and proj_of(client, pid).db.feature_branch(1) is None
    b = propose(client, pid).json()["id"]
    assert decide(client, pid, b, "reject").status_code == 200
    assert feature(client, pid)["branch"]["state"] == "rejected" and proj_of(client, pid).db.feature_branch(1) is None
    plain = client.post("/internal/approvals", json={"feature": "F-1", "title": "Scope lock: Export", **BRIEF},
                        headers=hdr(client, pid, "lead")).json()["id"]
    r = decide(client, pid, plain, payload={"branch": "x"})
    assert r.status_code == 400 and "only accepted on Branch: and PR: approvals" in r.json()["detail"]
    assert proj_of(client, pid).db.approval(plain)["state"] == "pending"
    pr = client.post("/internal/approvals", json={"feature": "F-1", "title": "PR: Export", **BRIEF},
                     headers=hdr(client, pid, "lead")).json()["id"]
    assert decide(client, pid, pr, payload={"destination": "main"}).status_code == 200  # accepted (B3 gives it meaning)


# ---- the gate --------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def gated(client, pid):
    """Open the gate now (conftest keeps it shut): F-1, created before, stays ungated; features created from now on are gated."""
    proj_of(client, pid).db.set_kv("branch_gate_since", time.time())


def file_commit(client, pid, repo, name, feature="F-1", role="lead"):
    (repo / name).write_text(name + "\n")
    return client.post("/internal/commits", json={"feature": feature, "files": [name], "message": f"add {name}"},
                       headers=hdr(client, pid, role))


def test_the_gate_refuses_commit_requests_until_the_branch_is_approved(client, pid, repo, gated):
    db = proj_of(client, pid).db
    assert db.feature(1)["branch"]["gated"] is False  # created before the gate: keeps working in the main folder
    assert file_commit(client, pid, repo, "old.py").status_code == 200
    client.post(f"/api/p/{pid}/features", json={"text": "Search", "starter": "product"})
    db.update_feature(2, lead="lead")
    assert db.feature(2)["branch"]["gated"] is True and db.feature(2)["branch"]["state"] == "none"
    want = "F-2 has no confirmed branch yet; the Lead sends a Branch card (propose_branch) first"
    r = file_commit(client, pid, repo, "new.py", "F-2")
    assert r.status_code == 409 and r.json()["detail"] == want
    a = propose(client, pid, feature="F-2").json()["id"]
    assert file_commit(client, pid, repo, "new.py", "F-2").json()["detail"] == want  # a pending card isn't enough
    decide(client, pid, a, "reject")
    assert file_commit(client, pid, repo, "new.py", "F-2").status_code == 409
    b = propose(client, pid, feature="F-2", branch="feat/search").json()["id"]
    assert decide(client, pid, b).status_code == 200
    assert file_commit(client, pid, repo, "new.py", "F-2").status_code == 200  # open
    assert file_commit(client, pid, repo, "old2.py", "F-1").status_code == 200  # still not gated
    assert file_commit(client, pid, repo, "nofeature.py", None).status_code == 200  # a request with no feature


def test_an_ungated_feature_that_gets_a_branch_card_is_gated_until_it_is_approved(client, pid, repo, gated):
    db = proj_of(client, pid).db
    a = propose(client, pid).json()["id"]
    assert db.feature(1)["branch"]["gated"] is True
    assert file_commit(client, pid, repo, "x.py").status_code == 409
    decide(client, pid, a)
    assert file_commit(client, pid, repo, "x.py").status_code == 200


def test_a_withdrawn_card_keeps_the_feature_gated(client, pid, repo, gated):
    a = propose(client, pid).json()["id"]
    client.post("/internal/withdraw", json={"kind": "approval", "id": a, "reason": "wrong base"}, headers=hdr(client, pid, "lead"))
    b = proj_of(client, pid).db.feature(1)["branch"]
    assert (b["state"], b["gated"], b["approval_id"]) == ("none", True, None)
    assert file_commit(client, pid, repo, "x.py").status_code == 409


# ---- the prompt's header line --------------------------------------------------------------------------------------------------------
def test_the_feature_header_says_where_the_work_lands(client, pid, repo, gated):
    proj = proj_of(client, pid)
    db = proj.db

    def line(n=1):
        h = workflow.feature_header(db, str(repo), db.feature(n)).splitlines()
        return [x for x in h if x.startswith("- branch:")]
    assert line() == []  # an older feature with no branch: no line at all
    a = propose(client, pid).json()["id"]
    assert line() == ["- branch: not confirmed (no code until the Master approves a Branch card)"]
    decide(client, pid, a)
    tree = branches.tree_path(str(repo), 1)
    assert line() == [f"- branch: feat/f1-export-orders from main · tree {tree} (edit and test there; docs stay in .aiteams/features/F-1/)"]
    client.post(f"/api/p/{pid}/features", json={"text": "Search", "starter": "product"})
    db.update_feature(2, lead="lead")
    assert line(2) == ["- branch: not confirmed (no code until the Master approves a Branch card)"]  # gated, nothing yet
    c = propose(client, pid, feature="F-2", mode="current").json()["id"]
    decide(client, pid, c)
    assert line(2) == ["- branch: current folder (main)"]
    client.post(f"/api/p/{pid}/features", json={"text": "Reports", "starter": "product"})
    db.update_feature(3, lead="lead")
    d = propose(client, pid, feature="F-3", mode="shared", shared_from="F-1").json()["id"]
    decide(client, pid, d)
    assert line(3) == [f"- branch: feat/f1-export-orders (shared with F-1) · tree {tree} "
                       "(edit and test there; docs stay in .aiteams/features/F-3/)"]


def test_a_branch_card_carries_its_payload_through_the_message_join_and_the_lists(client, pid):
    a = propose(client, pid).json()["id"]
    listed = client.get(f"/api/p/{pid}/approvals").json()
    assert listed[0]["id"] == a and listed[0]["payload"]["branch"] == "feat/f1-export-orders"
    plain = client.post("/internal/approvals", json={"feature": "F-1", "title": "Scope lock: x", **BRIEF},
                        headers=hdr(client, pid, "lead")).json()["id"]
    assert next(x for x in client.get(f"/api/p/{pid}/approvals").json() if x["id"] == plain)["payload"] is None


def test_a_branch_started_from_a_remote_base_does_not_track_it(client, pid, repo, tmp_path):
    """Without --no-track, `worktree add -b x origin/main` makes main x's upstream, and a later plain push would go to main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    a = propose(client, pid).json()
    assert a["payload"]["base"] == "origin/main"
    assert decide(client, pid, a["id"]).status_code == 200
    b = feature(client, pid)["branch"]
    assert b["base"] == "origin/main"
    for key in ("remote", "merge"):
        r = subprocess.run(["git", "config", f"branch.{b['branch']}.{key}"], cwd=repo, capture_output=True, text=True)
        assert r.stdout.strip() == "", key


def test_a_decided_card_shows_what_was_approved_not_the_proposal(client, pid, repo):
    """Approve writes the approved choice back into the approval's payload: mode, branch, base, shared_from, and the
    recomputed tree, warnings, current and worktrees (the Lead's why stays)."""
    proj = proj_of(client, pid)
    git(repo, "branch", "dev-base")
    a = propose(client, pid).json()
    assert a["payload"]["warnings"] == [] and a["payload"]["branch"] == "feat/f1-export-orders"
    r = decide(client, pid, a["id"], payload={"branch": "release/2.0", "base": "dev-base"})
    assert r.status_code == 200, r.text
    tree = branches.tree_path(str(repo), 1)
    stored = proj.db.approval(a["id"])["payload"]
    assert (stored["mode"], stored["branch"], stored["base"], stored["shared_from"], stored["tree"]) == (
        "new", "release/2.0", "dev-base", None, tree)
    assert len(stored["warnings"]) == 1 and "release/2.0 looks like a shared branch" in stored["warnings"][0]  # recomputed
    assert stored["why"] == "Keeps F-1 off the main folder." and stored["current"] == "main" and stored["worktrees"] == []
    assert [x for x in client.get(f"/api/p/{pid}/approvals", params={"state": "approved"}).json()][0]["payload"] == stored
    m = proj.db.message(proj.db.approval(a["id"])["message_id"])
    assert m["approval"]["payload"]["branch"] == "release/2.0" and m["approval"]["state"] == "approved"
    # current mode: the proposal had no branch; the approved card names the checked-out one and no tree
    client.post(f"/api/p/{pid}/features", json={"text": "Search", "starter": "product"})
    proj.db.update_feature(2, lead="lead")
    c = propose(client, pid, feature="F-2", mode="current").json()
    assert c["payload"]["tree"] is None and c["payload"]["branch"] is None
    decide(client, pid, c["id"])
    st = proj.db.approval(c["id"])["payload"]
    assert (st["mode"], st["branch"], st["tree"], st["base"]) == ("current", "main", None, None)
    assert [w["feature"] for w in st["worktrees"]] == ["F-1"] and "main looks like a shared branch" in st["warnings"][0]
    # shared mode: the source's tree is on the proposal and the approved card, and shared_from is the F-n key again
    client.post(f"/api/p/{pid}/features", json={"text": "Reports", "starter": "product"})
    proj.db.update_feature(3, lead="lead")
    d = propose(client, pid, feature="F-3", mode="shared", shared_from="F-1").json()
    assert d["payload"]["tree"] == tree and d["payload"]["shared_from"] == "F-1"
    decide(client, pid, d["id"])
    sd = proj.db.approval(d["id"])["payload"]
    assert (sd["mode"], sd["shared_from"], sd["tree"], sd["branch"]) == ("shared", "F-1", tree, "release/2.0")


def test_a_card_that_fails_to_approve_keeps_the_lead_proposal_in_its_payload(client, pid, repo):
    proj = proj_of(client, pid)
    a = propose(client, pid).json()
    before = proj.db.approval(a["id"])["payload"]
    os.makedirs(branches.tree_path(str(repo), 1))
    open(os.path.join(branches.tree_path(str(repo), 1), "x"), "w").write("x")
    assert decide(client, pid, a["id"], payload={"branch": "feat/other"}).status_code == 409
    assert proj.db.approval(a["id"])["payload"] == before  # nothing was written back
