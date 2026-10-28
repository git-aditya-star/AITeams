"""F-39: branches and worktrees per feature, confirmed by the Master.

Everything git-shaped for the Branch card lives here, so workflow.py only gains small hooks into it. A feature with a
branch works in one of three ways (`mode`): `new` (a fresh branch in its own worktree next to the project), `current`
(the branch the main folder has checked out), `shared` (another feature's worktree). The Lead proposes it with
`propose_branch`; the Master edits and approves the card; `apply_branch` then creates the worktree BEFORE the approval is
resolved, so a failure leaves the card pending. Nothing here ever refuses a "shared-looking" branch (main, dev, release...):
the card warns and the Master decides ("ask me each time", card #47)."""
from __future__ import annotations

import os
import re
import subprocess

SHARED_RE = re.compile(r"^(origin/)?(main|master|trunk|dev|develop.*|development.*|release.*|staging|stage|production|prod|"
                       r"hotfix.*)(/.*)?$", re.IGNORECASE)
MODES = ("new", "current", "shared")
EDITABLE = ("mode", "branch", "base", "shared_from")  # what the Master may change on the card
LINKED = (".venv", os.path.join("ui", "node_modules"))  # untracked tooling the tree borrows from the main folder
FETCH_TIMEOUT_S = 10


class BranchError(Exception):
    """A plain sentence the Master (or the Lead) can act on."""


def git(repo: str, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def current_branch(repo: str) -> str | None:
    """The branch checked out in `repo`, None for a detached HEAD."""
    r = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    return r.stdout.strip() or None if r.returncode == 0 else None


def resolves(repo: str, ref: str) -> bool:
    return bool(ref) and git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def fetch_origin(repo: str) -> None:
    """Best effort, so the base list is current: an offline or remote-less Master still gets a card with the local refs."""
    try:
        if git(repo, "remote", "get-url", "origin").returncode == 0:
            git(repo, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        pass


def tilde(path: str | None) -> str:
    home = os.path.expanduser("~")
    return ("~" + path[len(home):]) if path and (path == home or path.startswith(home + os.sep)) else (path or "")


def tree_path(project_path: str, n: int) -> str:
    """Where feature F-n's worktree goes: next to the project, `<parent>/AITeams-worktrees/<project dir>/f<n>`."""
    root = os.path.realpath(project_path)
    return os.path.join(os.path.dirname(root), "AITeams-worktrees", os.path.basename(root), f"f{n}")


def default_base(repo: str) -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        if resolves(repo, ref):
            return ref
    return current_branch(repo) or "HEAD"


def default_branch_name(feat: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (feat.get("title") or "").lower()).strip("-")[:30].strip("-")
    return f"feat/f{feat['id']}" + (f"-{slug}" if slug else "")


def list_branches(repo: str) -> dict:
    """Local and remote branches, newest commit first (for the card's base list): `branches` is [{name, remote}] in that
    order, and `local` / `remote` are the same names split by kind."""
    r = git(repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname)", "refs/heads", "refs/remotes")
    out = []
    for ref in (x.strip() for x in r.stdout.splitlines()):
        if ref.startswith("refs/heads/"):
            out.append({"name": ref[len("refs/heads/"):], "remote": False})
        elif ref.startswith("refs/remotes/") and "/" in ref[len("refs/remotes/"):] and not ref.endswith("/HEAD"):
            out.append({"name": ref[len("refs/remotes/"):], "remote": True})  # a remote's own HEAD isn't a branch
    return {"branches": out, "local": [b["name"] for b in out if not b["remote"]],
            "remote": [b["name"] for b in out if b["remote"]]}


def live_trees(db, exclude: int | None = None) -> list[dict]:
    """Other features' live worktrees, for the "existing worktree" choice."""
    out = []
    for row in db.feature_branches():
        if row["tree"] and row["tree_state"] == "live" and row["feature_id"] != exclude:
            out.append({"feature": f"F-{row['feature_id']}", "branch": row["branch"], "tree": row["tree"]})
    return out


def overview(proj) -> dict:
    """GET /branches: what the card offers."""
    repo = proj.path
    return {"current": current_branch(repo), "default_base": default_base(repo), **list_branches(repo),
            "worktrees": live_trees(proj.db)}


def _shared_warning(branch: str, where: str) -> str:
    return (f"{branch} looks like a shared branch (main, dev, release, ...): {where} would land on it directly. "
            "Nothing is refused; you decide.")


def check(repo: str, db, mode: str, branch: str | None = None, base: str | None = None, shared_from: str | None = None,
          fetch: bool = False) -> dict:
    """{warnings, errors} for one choice on the card. `errors` stop the request or the approval; `warnings` never do."""
    errors: list[str] = []
    warns: list[str] = []
    branch, base = (branch or "").strip(), (base or "").strip()
    if mode == "new":
        if not branch:
            errors.append("name the new branch")
        elif git(repo, "check-ref-format", "--branch", branch).returncode != 0:
            errors.append(f"'{branch}' is not a valid branch name")
        elif git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode == 0:
            errors.append(f"branch {branch} already exists")
        elif SHARED_RE.match(branch):
            warns.append(_shared_warning(branch, "the new branch's work"))
        if not base:
            errors.append("pick the base the branch starts from")
        else:
            if fetch:
                fetch_origin(repo)
            if not resolves(repo, base):
                errors.append(f"base {base} doesn't resolve to a commit")
    elif mode == "current":
        cur = current_branch(repo)
        if cur is None:
            errors.append("the main folder has a detached HEAD; check out a branch first")
        elif SHARED_RE.match(cur):
            warns.append(_shared_warning(cur, "work in the main folder"))
    elif mode == "shared":
        src = db.feature(shared_from) if shared_from else None
        row = db.feature_branch(src["id"]) if src else None
        if not (row and row["tree"] and row["tree_state"] == "live"):
            errors.append(f"{shared_from or 'that feature'} has no live worktree to share")
        elif SHARED_RE.match(row["branch"]):
            warns.append(_shared_warning(row["branch"], f"work in {src['key']}'s tree"))
    else:
        errors.append("mode must be new, current or shared")
    return {"warnings": warns, "errors": errors}


def _link_tooling(root: str, tree: str) -> None:
    """Link the main folder's untracked .venv and ui/node_modules into the tree so tests run there, and add them to the
    repo's info/exclude (a `.venv/` line in .gitignore matches directories only, not a symlink), so `git status` stays
    clean and `git worktree remove` doesn't refuse them."""
    names = []
    for rel in LINKED:
        src, dst = os.path.join(root, rel), os.path.join(tree, rel)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)
            names.append("/" + rel.replace(os.sep, "/"))
    if not names:
        return
    common = git(root, "rev-parse", "--git-common-dir").stdout.strip() or ".git"
    exclude = os.path.join(root if not os.path.isabs(common) else "", common, "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    have = open(exclude).read().splitlines() if os.path.exists(exclude) else []
    add = [n for n in names if n not in have]
    if add:
        with open(exclude, "a") as f:
            f.write(("" if not have or have[-1] == "" else "\n") + "\n".join(add) + "\n")


def apply_branch(proj, feat: dict, payload: dict) -> dict:
    """On approval (blocking, run in a thread): re-check the choice, create the worktree for `new`, and return the fields of
    the feature_branches row plus the recomputed `warnings`. Raises BranchError with git's own message when it can't; the
    approval then stays pending."""
    repo, db = proj.path, proj.db
    mode = payload.get("mode")
    res = check(repo, db, mode, payload.get("branch"), payload.get("base"), payload.get("shared_from"))
    if res["errors"]:
        raise BranchError("; ".join(res["errors"]))
    warns = res["warnings"]
    if mode == "current":
        return {"mode": "current", "branch": current_branch(repo), "base": None, "tree": None, "shared_from": None,
                "warnings": warns}
    if mode == "shared":
        src = db.feature(payload["shared_from"])
        row = db.feature_branch(src["id"])
        return {"mode": "shared", "branch": row["branch"], "base": None, "tree": row["tree"], "shared_from": src["id"],
                "warnings": warns}
    branch, base = payload["branch"].strip(), payload["base"].strip()
    tree = tree_path(repo, feat["id"])
    if os.path.exists(tree) and (not os.path.isdir(tree) or os.listdir(tree)):
        raise BranchError(f"{tilde(tree)} already exists and isn't empty; remove it or pick another branch")
    os.makedirs(os.path.dirname(tree), exist_ok=True)
    r = git(repo, "worktree", "add", "--no-track", "-b", branch, tree, base)
    if r.returncode != 0:
        raise BranchError((r.stderr or r.stdout).strip() or "git worktree add failed")
    _link_tooling(os.path.realpath(repo), tree)
    return {"mode": "new", "branch": branch, "base": base, "tree": tree, "shared_from": None, "warnings": warns}


def planned_tree(repo: str, db, feat: dict, mode: str, shared_from: str | None = None) -> str | None:
    """Where this choice's work would be edited: a `new` worktree's path (from tree_path), the source feature's tree for
    `shared`, None for `current` (the main folder). The card shows it before approval; the UI can't compute it."""
    if mode == "new":
        return tree_path(repo, feat["id"])
    src = db.feature(shared_from) if mode == "shared" and shared_from else None
    row = db.feature_branch(src["id"]) if src else None
    return row["tree"] if row else None


def approved_payload(proj, feat: dict, merged: dict, applied: dict) -> dict:
    """What a decided card shows: the choice that was approved (not the Lead's proposal), with `tree`, `warnings`,
    `current` and `worktrees` recomputed now. `shared_from` goes back to the `F-n` key the card works with."""
    shared = f"F-{applied['shared_from']}" if applied.get("shared_from") else None
    return {**merged, "mode": applied["mode"], "branch": applied["branch"], "base": applied["base"], "shared_from": shared,
            "tree": applied["tree"], "warnings": applied["warnings"], "current": current_branch(proj.path),
            "worktrees": live_trees(proj.db, feat["id"])}


def lands_on(key: str, info: dict) -> str:
    """The line the Decision to the Lead (and the card) says: where this feature's commits go."""
    where = f"in {tilde(info['tree'])}" if info.get("tree") else "in the main folder"
    return f"Work on {key} lands on {info['branch']} {where}."
