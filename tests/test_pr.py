"""F-39 B3: the PR card (request_pr -> the Master edits and approves -> push + `gh pr create`), its pre-checks and failures,
the poll of open pull requests and the cleanup on merge. All on temp repos: a bare repo is `origin` (the GitHub URL is
rewritten onto it with `insteadOf`) and a fake `gh` on PATH records its calls and plays the scenario in `gh.json`."""
import asyncio
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteams import branches, lanes, workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn

GITHUB = "https://github.com/acme/app.git"
PR_URL = "https://github.com/acme/app/pull/7"
OLD_URL = "https://github.com/acme/app/pull/3"
FAKE_GH = """#!{python}
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(d, "gh.json")))
argv = sys.argv[1:]
rec = {{"argv": argv, "cwd": os.getcwd()}}
if "--body-file" in argv:
    rec["body"] = open(argv[argv.index("--body-file") + 1]).read()
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(rec) + "\\n")
if argv[:2] == ["auth", "status"]:
    if not cfg.get("auth", True):
        print("You are not logged into any GitHub hosts.", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if argv[:2] == ["pr", "create"]:
    mode = cfg.get("create", "ok")
    if mode == "ok":
        print("Creating pull request for the branch")
        print("{pr_url}")
        sys.exit(0)
    if mode == "exists":
        print('a pull request for branch "x" into branch "main" already exists:\\n{old_url}', file=sys.stderr)
        sys.exit(1)
    if mode == "fail":
        print("GraphQL: boom", file=sys.stderr)
        sys.exit(1)
    print("done")  # made, but no URL
    sys.exit(0)
if argv[:2] == ["pr", "view"]:
    if cfg.get("view_fail"):
        print("HTTP 502: bad gateway", file=sys.stderr)
        sys.exit(1)
    if "url,state" in argv:
        print(json.dumps(cfg.get("view", {{"url": "{old_url}", "state": "OPEN"}})))
    else:
        print(json.dumps({{"state": cfg.get("pr_state", "OPEN")}}))
    sys.exit(0)
sys.exit(2)
"""


class Gh:
    """The fake `gh`: `set(...)` changes the scenario, `calls()` is what it was asked, `of(*prefix)` filters by subcommand."""
    def __init__(self, bin_dir: Path):
        self.dir = bin_dir
        self.set()

    def set(self, **cfg):
        (self.dir / "gh.json").write_text(json.dumps(cfg))

    def calls(self):
        f = self.dir / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def of(self, *prefix):
        return [c for c in self.calls() if c["argv"][:len(prefix)] == list(prefix)]


@pytest.fixture
def gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text(FAKE_GH.format(python=sys.executable, pr_url=PR_URL, old_url=OLD_URL))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return Gh(bin_dir)


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
def bare(tmp_path):
    return tmp_path / "origin.git"


@pytest.fixture
def pid(client, repo, bare, gh, tmp_path):
    """A project on `main` with a GitHub-looking origin that really is a local bare repo, and feature F-1 led by `lead`."""
    (repo / ".gitignore").write_text(".venv/\nui/node_modules/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "branch", "-M", "main")
    git(tmp_path, "init", "--bare", "-q", str(bare))
    git(repo, "remote", "add", "origin", GITHUB)
    git(repo, "config", f"url.{bare}.insteadOf", GITHUB)
    git(repo, "push", "-q", "origin", "main")
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export orders", "starter": "product"})
    proj_of(client, pid).db.update_feature(1, lead="lead")
    return pid


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def hdr(client, pid, role):
    return running_turn(proj_of(client, pid).db, role, pid=pid)[1]


def decide(client, pid, aid, decision="approve", **body):
    return client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": decision, **body})


def feature(client, pid, n=1):
    return next(f for f in client.get(f"/api/p/{pid}/features").json() if f["id"] == n)


def new_feature(client, pid, text, n):
    client.post(f"/api/p/{pid}/features", json={"text": text, "starter": "product"})
    proj_of(client, pid).db.update_feature(n, lead="lead")


def branch_card(client, pid, n=1, **body):
    r = client.post("/internal/branches", json={"feature": f"F-{n}", "why": "x", **body}, headers=hdr(client, pid, "lead"))
    assert r.status_code == 200, r.text
    assert decide(client, pid, r.json()["id"]).status_code == 200
    return proj_of(client, pid).db.feature_branch(n)


def commit_in(tree, name, text="x\n"):
    Path(tree, name).write_text(text)
    git(tree, "add", name)
    git(tree, "commit", "-qm", f"feat: add {name}")


@pytest.fixture
def tree(client, pid, repo):
    """F-1 on its own worktree, two commits ahead of main."""
    row = branch_card(client, pid, mode="new")
    commit_in(row["tree"], "export.py")
    commit_in(row["tree"], "export_test.py")
    return row["tree"]


def ask(client, pid, role="lead", **body):
    return client.post("/internal/prs", json={"feature": "F-1", "title": "Export orders to CSV", "body": "Adds the export.",
                                              **body}, headers=hdr(client, pid, role))


def pr_card(client, pid, **body):
    r = ask(client, pid, **body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def opened(client, pid):
    """F-1's PR card approved: the PR is open at PR_URL."""
    a = pr_card(client, pid)
    assert decide(client, pid, a).status_code == 200
    return a


def refresh(client, pid, n=1):
    return client.post(f"/api/p/{pid}/features/{n}/pr/refresh")


def texts(client, pid, conv="feature-1"):
    return [(m["author"], m["type"], m["text"], m["mentions"]) for m in proj_of(client, pid).db.messages(conv)]


# ---- request_pr ------------------------------------------------------------------------------------------------------------
def test_request_pr_files_a_pr_card_with_the_commits_the_destination_lacks(client, pid, tree):
    r = ask(client, pid)
    assert r.status_code == 200, r.text
    a = proj_of(client, pid).db.approval(r.json()["id"])
    assert (a["title"], a["state"], a["role"], a["feature_id"]) == ("PR: Export orders to CSV", "pending", "lead", 1)
    p = a["payload"]
    assert (p["source"], p["destination"], p["title"], p["body"]) == ("feat/f1-export-orders", "main", "Export orders to CSV",
                                                                       "Adds the export.")
    assert [c["subject"] for c in p["commits"]] == ["feat: add export_test.py", "feat: add export.py"]  # newest first
    assert all(len(c["sha"]) >= 7 for c in p["commits"])
    assert "feat/f1-export-orders → main (2 commits)" in a["summary"] and "Adds the export." in a["summary"]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [x["id"] for x in inbox["approvals"]] == [a["id"]] and inbox["approvals"][0]["payload"]["destination"] == "main"
    m = proj_of(client, pid).db.message(a["message_id"])
    assert (m["type"], m["mentions"]) == ("Approval", ["master"])


def test_request_pr_defaults_the_destination_to_the_base_the_feature_started_from(client, pid, tree, repo):
    git(repo, "branch", "dev-base", "main")
    p = proj_of(client, pid)
    p.db.set_feature_branch(1, base="origin/main")  # a remote base: the PR goes to the bare branch name
    a = pr_card(client, pid, destination=None)
    assert p.db.approval(a)["payload"]["destination"] == "main"
    assert decide(client, pid, a, "changes").status_code == 200
    p.db.set_feature_branch(1, base="dev-base")  # a feature that builds on another branch goes back to it
    assert p.db.approval(pr_card(client, pid, destination=None))["payload"]["destination"] == "dev-base"


def test_request_pr_is_refused_without_a_branch_without_commits_and_for_the_same_branch(client, pid, repo):
    assert ask(client, pid).status_code == 400 and "no confirmed branch yet" in ask(client, pid).json()["detail"]
    new_feature(client, pid, "Search", 2)
    branch_card(client, pid, 2, mode="new")  # a worktree with no commit of its own
    r = ask(client, pid, feature="F-2")
    assert r.status_code == 400 and "feat/f2-search has no commits that main lacks" in r.json()["detail"]
    new_feature(client, pid, "Tidy", 3)
    branch_card(client, pid, 3, mode="current")  # the main folder's own branch
    r = ask(client, pid, feature="F-3")
    assert r.status_code == 400 and r.json()["detail"] == "main is both the branch and the destination; pick another destination"
    assert not proj_of(client, pid).db.approvals("pending")


def test_request_pr_refuses_an_unknown_destination_and_a_blank_title(client, pid, tree):
    r = ask(client, pid, destination="nope")
    assert r.status_code == 400 and r.json()["detail"] == "the destination nope doesn't exist (no origin/nope and no local nope)"
    assert ask(client, pid, title="  ").status_code in (400, 422)


def test_only_the_feature_lead_requests_a_pr_and_only_one_card_waits(client, pid, tree):
    assert ask(client, pid, role="backend").status_code == 403
    proj_of(client, pid).db.update_feature(1, lead="lead-2")
    r = ask(client, pid)
    assert r.status_code == 409 and "belongs to" in r.json()["detail"]
    proj_of(client, pid).db.update_feature(1, lead="lead")
    pr_card(client, pid)
    r = ask(client, pid)
    assert r.status_code == 409 and "already waiting for the Master" in r.json()["detail"]


def test_pr_preview_lists_the_commits_and_the_destinations_and_says_why_it_cant(client, pid, tree, repo):
    git(repo, "branch", "dev", "main")
    r = client.get(f"/api/p/{pid}/branches/pr-preview", params={"feature": "F-1"}).json()
    assert (r["source"], r["destination"], r["errors"]) == ("feat/f1-export-orders", "main", [])
    assert [c["subject"] for c in r["commits"]] == ["feat: add export_test.py", "feat: add export.py"]
    assert r["branches"] == [{"name": "main", "remote": True}]  # origin's branches as plain names, never the feature's own
    r = client.get(f"/api/p/{pid}/branches/pr-preview", params={"feature": "1", "destination": "origin/dev"}).json()
    assert r["destination"] == "dev" and r["branches"][0] == {"name": "dev", "remote": False} and len(r["commits"]) == 2  # local only: still offered
    r = client.get(f"/api/p/{pid}/branches/pr-preview", params={"feature": "F-1", "destination": "nope"}).json()
    assert r["commits"] == [] and r["errors"] == ["the destination nope doesn't exist (no origin/nope and no local nope)"]
    new_feature(client, pid, "Search", 2)
    assert client.get(f"/api/p/{pid}/branches/pr-preview", params={"feature": "F-2"}).status_code == 400
    assert client.get(f"/api/p/{pid}/branches/pr-preview", params={"feature": "F-9"}).status_code == 404


# ---- approving: push + gh pr create ----------------------------------------------------------------------------------------
def test_approving_pushes_the_branch_opens_the_pr_and_tells_the_lead(client, pid, tree, repo, bare, gh):
    p = proj_of(client, pid)
    events, orig = [], p.hub.broadcast

    async def spy(event, data):
        events.append((event, data))
        await orig(event, data)
    p.hub.broadcast = spy
    a = pr_card(client, pid)
    assert decide(client, pid, a).status_code == 200
    assert git(bare, "rev-parse", "refs/heads/feat/f1-export-orders") == git(tree, "rev-parse", "HEAD")  # pushed
    assert git(tree, "config", "branch.feat/f1-export-orders.remote") == "origin"  # -u: tracking is set
    create = gh.of("pr", "create")
    assert len(create) == 1
    argv = create[0]["argv"]
    assert argv[:9] == ["pr", "create", "--base", "main", "--head", "feat/f1-export-orders", "--title", "Export orders to CSV",
                        "--body-file"]
    assert create[0]["body"] == "Adds the export." and os.path.realpath(create[0]["cwd"]) == os.path.realpath(tree)
    assert not os.path.exists(argv[9])  # the body file is cleaned up
    b = feature(client, pid)["branch"]
    assert (b["pr_url"], b["pr_state"], b["pr_destination"], b["tree_state"]) == (PR_URL, "open", "main", "live")  # URL = last stdout line
    assert p.db.approval(a)["state"] == "approved"
    stored = p.db.approval(a)["payload"]
    assert (stored["pr_url"], stored["pr_state"], stored["source"]) == (PR_URL, "open", "feat/f1-export-orders")
    dec = p.db.messages("feature-1")[-1]
    assert dec["type"] == "Decision" and dec["mentions"] == ["lead"]
    assert dec["text"].endswith(f"Pull request opened: {PR_URL} (feat/f1-export-orders → main)")
    assert [d["branch"]["pr_state"] for e, d in events if e == "feature" and d["id"] == 1][-1] == "open"  # the chip updates live


def test_the_masters_edits_on_the_card_win_but_the_source_is_the_features_own_branch(client, pid, tree, repo, bare, gh):
    git(repo, "branch", "dev", "main")
    a = pr_card(client, pid)
    p = proj_of(client, pid)
    p.db.set_approval_payload(a, {**p.db.approval(a)["payload"], "source": "evil"})  # even a stored source is not trusted
    r = decide(client, pid, a, payload={"destination": "origin/dev", "title": "  Better   title ", "body": "Edited body",
                                        "source": "evil", "url": "x", "commits": []})
    assert r.status_code == 200, r.text
    argv = gh.of("pr", "create")[0]["argv"]
    assert argv[2:8] == ["--base", "dev", "--head", "feat/f1-export-orders", "--title", "Better title"]
    assert gh.of("pr", "create")[0]["body"] == "Edited body"
    stored = proj_of(client, pid).db.approval(a)["payload"]
    assert (stored["destination"], stored["title"], stored["body"], stored["source"], stored["pr_url"]) == (
        "dev", "Better title", "Edited body", "feat/f1-export-orders", PR_URL)
    assert len(stored["commits"]) == 2
    assert feature(client, pid)["branch"]["pr_destination"] == "dev"


def test_an_existing_pr_for_the_branch_is_reused(client, pid, tree, gh):
    gh.set(create="exists")
    a = pr_card(client, pid)
    assert decide(client, pid, a).status_code == 200
    assert gh.of("pr", "view", "feat/f1-export-orders", "--json", "url,state")
    b = feature(client, pid)["branch"]
    assert (b["pr_url"], b["pr_state"]) == (OLD_URL, "open")
    assert proj_of(client, pid).db.messages("feature-1")[-1]["text"].endswith(
        f"Pull request already open: {OLD_URL} (feat/f1-export-orders → main)")
    # ... but a merged one on that head can't be reused
    new_feature(client, pid, "Other", 2)
    branch_card(client, pid, 2, mode="new")
    commit_in(proj_of(client, pid).db.feature_branch(2)["tree"], "o.py")
    gh.set(create="exists", view={"url": OLD_URL, "state": "MERGED"})
    a2 = pr_card(client, pid, feature="F-2")
    r = decide(client, pid, a2)
    assert r.status_code == 409 and "already exists, but it can't be reused" in r.json()["detail"]


def test_a_pr_without_a_url_from_gh_is_a_409(client, pid, tree, gh):
    gh.set(create="nourl")
    a = pr_card(client, pid)
    r = decide(client, pid, a)
    assert r.status_code == 409 and r.json()["detail"].startswith("gh made the pull request but printed no URL")
    assert proj_of(client, pid).db.approval(a)["state"] == "pending"


def pending_and_untouched(client, pid, aid, bare):
    p = proj_of(client, pid)
    assert p.db.approval(aid)["state"] == "pending"
    b = feature(client, pid)["branch"]
    assert (b["pr_url"], b["pr_state"], b["pr_destination"]) == (None, None, None)
    assert git(bare, "for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/main"  # nothing was pushed
    assert not any("Pull request opened" in m[2] for m in texts(client, pid))


def test_no_origin_is_a_409_with_the_way_out_and_the_card_stays_pending(client, pid, tree, repo, bare, gh):
    a = pr_card(client, pid)
    git(repo, "remote", "remove", "origin")
    r = decide(client, pid, a)
    assert r.status_code == 409
    assert r.json()["detail"] == "this repo has no `origin` remote; add one (`git remote add origin …`) and retry"
    pending_and_untouched(client, pid, a, bare)
    assert gh.calls() == []


def test_gh_missing_is_a_409(client, pid, tree, bare, gh, monkeypatch):
    a = pr_card(client, pid)
    real = branches.shutil.which
    monkeypatch.setattr(branches.shutil, "which", lambda name, *x, **k: None if name == "gh" else real(name, *x, **k))
    r = decide(client, pid, a)
    assert r.status_code == 409 and r.json()["detail"] == "the GitHub CLI `gh` isn't installed (`brew install gh`)"
    pending_and_untouched(client, pid, a, bare)


def test_gh_not_logged_in_is_a_409(client, pid, tree, bare, gh):
    a = pr_card(client, pid)
    gh.set(auth=False)
    r = decide(client, pid, a)
    assert r.status_code == 409 and r.json()["detail"] == "`gh` isn't logged in; run `gh auth login` and retry"
    assert gh.of("auth", "status", "--hostname", "github.com") and not gh.of("pr", "create")
    pending_and_untouched(client, pid, a, bare)


def test_an_origin_that_isnt_github_is_a_409_and_the_checks_run_in_order(client, pid, tree, repo, bare, gh):
    a = pr_card(client, pid)
    git(repo, "config", "--unset", f"url.{bare}.insteadOf")
    git(repo, "remote", "set-url", "origin", str(bare))  # a local path: pushable, but not GitHub
    r = decide(client, pid, a)
    assert r.status_code == 409 and r.json()["detail"] == "origin isn't a GitHub remote; only GitHub is supported"
    pending_and_untouched(client, pid, a, bare)
    gh.set(auth=False)  # the login check comes before the GitHub check
    assert "isn't logged in" in decide(client, pid, a).json()["detail"]


def test_the_github_url_forms_and_the_insteadOf_rewrite():
    for ok in ("https://github.com/o/r.git", "https://user@github.com/o/r", "git@github.com:o/r.git",
               "ssh://git@github.com/o/r.git", "ssh://git@github.com:22/o/r.git", "git://github.com/o/r", "HTTPS://GitHub.com/o/r"):
        assert branches.is_github_url(ok), ok
    for bad in ("", None, "/tmp/origin.git", "file:///tmp/o.git", "https://gitlab.com/o/r.git", "https://github.com.evil.com/o/r",
                "https://notgithub.com/o/r", "git@bitbucket.org:o/r.git"):
        assert not branches.is_github_url(bad), bad


def test_a_failed_push_keeps_the_card_pending_and_a_retry_works(client, pid, tree, bare, gh, tmp_path):
    a = pr_card(client, pid)
    bare.rename(tmp_path / "gone.git")
    r = decide(client, pid, a)
    assert r.status_code == 409 and r.json()["detail"].startswith("pushing feat/f1-export-orders to origin failed:")
    assert proj_of(client, pid).db.approval(a)["state"] == "pending" and not gh.of("pr", "create")
    (tmp_path / "gone.git").rename(bare)
    assert decide(client, pid, a).status_code == 200
    assert feature(client, pid)["branch"]["pr_state"] == "open"


def test_a_failed_gh_create_keeps_the_card_pending_and_a_retry_works(client, pid, tree, bare, gh):
    gh.set(create="fail")
    a = pr_card(client, pid)
    r = decide(client, pid, a)
    assert r.status_code == 409 and r.json()["detail"] == "gh pr create failed: GraphQL: boom"
    assert proj_of(client, pid).db.approval(a)["state"] == "pending" and feature(client, pid)["branch"]["pr_state"] is None
    assert git(bare, "rev-parse", "refs/heads/feat/f1-export-orders")  # the push already happened; retrying it is harmless
    gh.set()
    assert decide(client, pid, a).status_code == 200 and feature(client, pid)["branch"]["pr_url"] == PR_URL


def test_changes_and_reject_open_nothing(client, pid, tree, gh):
    a = pr_card(client, pid)
    assert decide(client, pid, a, "changes", note="rename it").status_code == 200
    assert not gh.calls() and feature(client, pid)["branch"]["pr_state"] is None
    assert pr_card(client, pid)  # a new card may follow
    p = proj_of(client, pid)
    assert decide(client, pid, p.db.approvals("pending", 1)[0]["id"], "reject").status_code == 200
    assert not gh.calls()


def test_a_pr_approval_without_a_branch_or_a_payload_is_a_409_not_a_crash(client, pid, gh):
    r = client.post("/internal/approvals", json={"feature": "F-1", "title": "PR: by hand", "what": "x", "if_approved": "y",
                                                  "tradeoffs": ["none"]}, headers=hdr(client, pid, "lead"))
    assert r.status_code == 200, r.text
    d = decide(client, pid, r.json()["id"])
    assert d.status_code == 409 and "has no confirmed branch" in d.json()["detail"]
    assert not gh.calls()


def test_a_new_pr_is_refused_while_one_is_open_or_merged_and_allowed_after_a_close(client, pid, tree, gh):
    opened(client, pid)
    r = ask(client, pid)
    assert r.status_code == 409 and r.json()["detail"] == f"F-1 already has a pull request (open): {PR_URL}"
    gh.set(pr_state="CLOSED")
    assert refresh(client, pid).status_code == 200 and feature(client, pid)["branch"]["pr_state"] == "closed"
    assert ask(client, pid).status_code == 200  # a closed PR doesn't block a new one
    p = proj_of(client, pid)
    p.db.set_feature_branch(1, pr_state="merged")
    assert ask(client, pid).status_code == 409


# ---- the poll and the cleanup on merge ---------------------------------------------------------------------------------------
def test_a_merged_pr_removes_the_worktree_keeps_the_branch_and_the_feature_leaves_its_lane(client, pid, tree, repo, gh):
    p = proj_of(client, pid)
    opened(client, pid)
    assert lanes.lane_for_feature(p.db, 1) == "F-1"
    p.db.new_session("backend", "backend-f1", "first turn", "F-1")  # F-41: a lane session on the feature
    w = p.db.add_wake("backend", "feature-1", [], "master", lane="F-1")
    row = next(r for r in client.get(f"/api/p/{pid}/team").json()["roles"] if r["role"] == "backend")
    assert [x["lane"] for x in row["lanes"]] == ["F-1"]
    gh.set(pr_state="MERGED")
    r = refresh(client, pid)
    assert r.status_code == 200, r.text
    b = r.json()["branch"]
    assert (b["pr_state"], b["tree_state"]) == ("merged", "removed")
    assert not os.path.exists(tree) and tree not in git(repo, "worktree", "list")
    assert git(repo, "branch", "--list", "feat/f1-export-orders")  # the branch is never deleted
    view = gh.of("pr", "view", PR_URL, "--json", "state")
    assert len(view) == 1 and os.path.realpath(view[0]["cwd"]) == os.path.realpath(repo)
    last = [m for m in texts(client, pid) if m[0] == "system"][-1]
    assert last[1] == "System" and last[3] == [] and f"Pull request merged: {PR_URL}" in last[2] and "removed" in last[2]
    # no tree of its own any more: the lane closed, its queued work and every new wake go to the main session
    assert lanes.lane_for_feature(p.db, 1) == "" and lanes.lane_for(p.db, "feature-1") == ""
    assert p.db.active_session("backend", "F-1") is None and p.db.wake(w["id"])["lane"] == ""
    row = next(r for r in client.get(f"/api/p/{pid}/team").json()["roles"] if r["role"] == "backend")
    assert row["lanes"] == []
    assert refresh(client, pid).status_code == 200  # a second refresh changes and posts nothing
    assert len([m for m in texts(client, pid) if "Pull request merged" in m[2]]) == 1


def test_a_dirty_tree_is_kept_and_the_lead_gets_a_finding_naming_the_files(client, pid, tree, repo, gh):
    opened(client, pid)
    Path(tree, "scratch.txt").write_text("wip\n")
    commit_in(tree, "tracked.py")
    Path(tree, "tracked.py").write_text("changed\n")
    gh.set(pr_state="MERGED")
    b = refresh(client, pid).json()["branch"]
    assert (b["pr_state"], b["tree_state"]) == ("merged", "kept")
    assert os.path.isdir(tree) and tree in git(repo, "worktree", "list")  # never --force
    finding = [m for m in texts(client, pid) if m[1] == "Finding"][-1]
    assert finding[0] == "system" and finding[3] == ["lead"]
    assert "scratch.txt" in finding[2] and "tracked.py" in finding[2] and "worktree remove" in finding[2]
    p = proj_of(client, pid)
    assert "kept" in [m for m in texts(client, pid) if "Pull request merged" in m[2]][-1][2]
    assert lanes.lane_for_feature(p.db, 1) == ""  # a kept tree is not the feature's tree either: it leaves its lane


def test_a_closed_pr_tells_the_lead_and_keeps_the_tree(client, pid, tree, gh):
    opened(client, pid)
    gh.set(pr_state="CLOSED")
    b = refresh(client, pid).json()["branch"]
    assert (b["pr_state"], b["tree_state"]) == ("closed", "live") and os.path.isdir(tree)
    last = [m for m in texts(client, pid) if m[0] == "system"][-1]
    assert last[3] == ["lead"] and last[2] == f"Pull request closed without merging: {PR_URL}. The worktree stays."


def test_an_open_pr_changes_nothing(client, pid, tree, gh):
    opened(client, pid)
    before = texts(client, pid)
    assert refresh(client, pid).json()["branch"]["pr_state"] == "open"
    assert texts(client, pid) == before and os.path.isdir(tree)


def test_a_tree_another_open_feature_shares_is_not_removed(client, pid, tree, repo, gh):
    p = proj_of(client, pid)
    new_feature(client, pid, "Search", 2)
    row = branch_card(client, pid, 2, mode="shared", shared_from="F-1")
    assert row["tree"] == tree
    opened(client, pid)
    p.db.new_session("backend", "backend-f1", "first turn", "F-1")
    gh.set(pr_state="MERGED")
    b = refresh(client, pid).json()["branch"]
    assert (b["pr_state"], b["tree_state"]) == ("merged", "live") and os.path.isdir(tree)
    assert "stays: F-2 still work in it" in [m for m in texts(client, pid) if "Pull request merged" in m[2]][-1][2]
    assert lanes.lane_for_feature(p.db, 1) == "F-1"  # the tree is still its own: nobody leaves a lane that still works
    assert p.db.active_session("backend", "F-1") == "backend-f1"
    # the sharer is done: the next merge of a feature on this tree may take it away
    p.db.update_feature(2, phase="done")
    p.db.set_feature_branch(1, pr_state="open")
    assert refresh(client, pid).status_code == 200
    assert not os.path.exists(tree) and p.db.feature_branch(2)["tree_state"] == "removed"


def test_a_gh_failure_is_a_409_on_refresh_and_never_posted_by_the_poll(client, pid, tree, gh, monkeypatch):
    p = proj_of(client, pid)
    opened(client, pid)
    before = texts(client, pid)
    gh.set(view_fail=True)
    r = refresh(client, pid)
    assert r.status_code == 409 and r.json()["detail"] == "gh pr view failed: HTTP 502: bad gateway"
    client.portal.call(workflow.poll_prs_once, client.app.state.app)  # logged, swallowed
    assert texts(client, pid) == before and p.db.feature_branch(1)["pr_state"] == "open"
    real = branches.shutil.which
    monkeypatch.setattr(branches.shutil, "which", lambda name, *x, **k: None if name == "gh" else real(name, *x, **k))
    assert "isn't installed" in refresh(client, pid).json()["detail"]
    client.portal.call(workflow.poll_prs_once, client.app.state.app)
    assert texts(client, pid) == before


def test_refresh_needs_a_pr(client, pid, tree):
    r = refresh(client, pid)
    assert r.status_code == 400 and r.json()["detail"] == "F-1 has no pull request to refresh"
    assert refresh(client, pid, 9).status_code == 404


def test_the_poll_looks_at_every_open_pr_and_only_those(client, pid, tree, repo, gh):
    p = proj_of(client, pid)
    opened(client, pid)
    new_feature(client, pid, "Search", 2)
    branch_card(client, pid, 2, mode="new")
    p.db.set_feature_branch(2, pr_url=OLD_URL, pr_state="closed")  # not polled
    gh.set(pr_state="MERGED")
    client.portal.call(workflow.poll_prs_once, client.app.state.app)
    assert p.db.feature_branch(1)["pr_state"] == "merged" and not os.path.exists(tree)
    assert [c["argv"][2] for c in gh.of("pr", "view")] == [PR_URL]


def test_the_daemon_polls_once_at_startup_then_every_round_and_stops_with_it(home, monkeypatch):
    calls = []

    async def fake_once(st):
        calls.append(time.time())
    monkeypatch.setattr(workflow, "poll_prs_once", fake_once)
    monkeypatch.setattr(workflow, "PR_POLL_S", 0.05)
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH):
        deadline = time.time() + 3
        while len(calls) < 3 and time.time() < deadline:
            time.sleep(0.02)
    n = len(calls)
    assert n >= 3  # right away, then again each round
    time.sleep(0.2)
    assert len(calls) == n  # cancelled with the app
