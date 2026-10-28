"""F-39: branches and worktrees per feature, confirmed by the Master.

Everything git-shaped for the Branch card lives here, so workflow.py only gains small hooks into it. A feature with a
branch works in one of three ways (`mode`): `new` (a fresh branch in its own worktree next to the project), `current`
(the branch the main folder has checked out), `shared` (another feature's worktree). The Lead proposes it with
`propose_branch`; the Master edits and approves the card; `apply_branch` then creates the worktree BEFORE the approval is
resolved, so a failure leaves the card pending. Nothing here ever refuses a "shared-looking" branch (main, dev, release...):
the card warns and the Master decides ("ask me each time", card #47)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

SHARED_RE = re.compile(r"^(origin/)?(main|master|trunk|dev|develop.*|development.*|release.*|staging|stage|production|prod|"
                       r"hotfix.*)(/.*)?$", re.IGNORECASE)
MODES = ("new", "current", "shared")
EDITABLE = ("mode", "branch", "base", "shared_from")  # what the Master may change on the card
LINKED = (".venv", os.path.join("ui", "node_modules"))  # untracked tooling the tree borrows from the main folder
FETCH_TIMEOUT_S = 10
PR_EDITABLE = ("destination", "title", "body")  # what the Master may change on a PR card
PR_COMMITS_MAX = 50
GH_TIMEOUT_S = 15  # a quick gh call: the auth check and the merge poll
PUSH_TIMEOUT_S = 120
PR_CREATE_TIMEOUT_S = 60
GITHUB_URL = re.compile(r"^(?:(?:https?|ssh|git)://(?:[^@/\s]+@)?|[^@/\s]+@)github\.com(?::|/|$)", re.IGNORECASE)


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


def worktrees_root(project_path: str) -> str:
    """The folder holding every feature's worktree: `<parent>/AITeams-worktrees/<project dir>` (a turn's AITEAMS_WORKTREES_ROOT)."""
    root = os.path.realpath(project_path)
    return os.path.join(os.path.dirname(root), "AITeams-worktrees", os.path.basename(root))


def tree_path(project_path: str, n: int) -> str:
    """Where feature F-n's worktree goes: next to the project, `<parent>/AITeams-worktrees/<project dir>/f<n>`."""
    return os.path.join(worktrees_root(project_path), f"f{n}")


def tree_for(db, feature_id: int | None) -> str | None:
    """F-39 B2: the directory a feature's work is edited, tested and committed in: its live worktree, if the folder is still
    there. None for no feature, no approved branch, `current` mode (the main folder) and a removed or kept-but-gone tree."""
    row = db.feature_branch(feature_id) if feature_id is not None else None
    if row and row["tree"] and row["tree_state"] == "live" and os.path.isdir(row["tree"]):
        return row["tree"]
    return None


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


# ---- F-39 B3: the pull request (push + gh pr create) and cleanup on merge ----------------------------------------------
def _cli_env() -> dict:
    """The daemon has no terminal: a credential prompt must fail with a message, never hang a worker thread."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}


def _run(cmd: list[str], cwd: str, timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=_cli_env())
    except subprocess.TimeoutExpired:
        raise BranchError(f"`{' '.join(cmd[:3])}` didn't answer in {int(timeout)} s; check the network and retry")
    except OSError as e:
        raise BranchError(f"`{cmd[0]}` could not run: {e}")


def gh(cwd: str, *args: str, timeout: float = GH_TIMEOUT_S) -> subprocess.CompletedProcess:
    return _run(["gh", *args], cwd, timeout)


def _tail(r: subprocess.CompletedProcess) -> str:
    text = ((r.stderr or "").strip() + "\n" + (r.stdout or "").strip()).strip()
    return text[-600:] if len(text) > 600 else text


def is_github_url(url: str | None) -> bool:
    return bool(GITHUB_URL.match((url or "").strip()))


def origin_urls(repo: str) -> list[str]:
    """origin's URL as written in the config and as git expands it (`insteadOf`); empty when there is no origin."""
    raw = git(repo, "config", "--get", "remote.origin.url").stdout.strip()
    expanded = git(repo, "remote", "get-url", "origin")
    out = [u for u in (raw, expanded.stdout.strip() if expanded.returncode == 0 else "") if u]
    return list(dict.fromkeys(out))


def pr_preflight(root: str) -> None:
    """The four things that must hold before AITeams pushes anything, each failing with a sentence the Master can act on."""
    urls = origin_urls(root)
    if not urls:
        raise BranchError("this repo has no `origin` remote; add one (`git remote add origin …`) and retry")
    if shutil.which("gh") is None:
        raise BranchError("the GitHub CLI `gh` isn't installed (`brew install gh`)")
    if gh(root, "auth", "status", "--hostname", "github.com").returncode != 0:
        raise BranchError("`gh` isn't logged in; run `gh auth login` and retry")
    if not any(is_github_url(u) for u in urls):
        raise BranchError("origin isn't a GitHub remote; only GitHub is supported")


def bare_branch(name: str | None) -> str:
    """A destination as `gh --base` wants it: no `origin/` prefix."""
    name = (name or "").strip()
    return name[len("origin/"):] if name.startswith("origin/") else name


def dest_ref(repo: str, dest: str) -> str | None:
    """The ref a PR into `dest` is measured against: origin's copy when there is one (what GitHub compares), else a local branch."""
    for ref in (f"origin/{dest}", dest):
        if resolves(repo, ref):
            return ref
    return None


def commits_ahead(repo: str, source: str, dest: str, limit: int = PR_COMMITS_MAX) -> list[dict]:
    """[{sha, subject}], newest first: what `source` has that `dest` lacks. Raises BranchError when the pair can't be a PR
    (the same branch, an unknown destination, nothing to propose)."""
    if source == dest:
        raise BranchError(f"{source} is both the branch and the destination; pick another destination")
    ref = dest_ref(repo, dest)
    if ref is None:
        raise BranchError(f"the destination {dest} doesn't exist (no origin/{dest} and no local {dest})")
    r = git(repo, "log", f"--max-count={limit}", "--format=%h%x09%s", f"{ref}..{source}")
    if r.returncode != 0:
        raise BranchError((r.stderr or r.stdout).strip() or f"git log {ref}..{source} failed")
    commits = []
    for line in r.stdout.splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            commits.append({"sha": sha, "subject": subject})
    if not commits:
        raise BranchError(f"{source} has no commits that {dest} lacks, so there is nothing to open a pull request for")
    return commits


def default_destination(repo: str, row: dict) -> str:
    """The branch a PR goes to unless told otherwise: the one the feature started from, else the repo's main branch."""
    for cand in (bare_branch(row.get("base")), bare_branch(default_base(repo))):
        if cand and cand != row["branch"] and dest_ref(repo, cand):
            return cand
    return bare_branch(default_base(repo)) or "main"


def work_dir(proj, feature_id: int) -> str:
    return tree_for(proj.db, feature_id) or proj.path


def pr_preview(proj, feat: dict, destination: str | None = None) -> dict:
    """GET /branches/pr-preview: what the PR card shows: `source`, the chosen `destination`, `branches` to pick from (`[{name, remote}]`
    as /branches, plain names: origin's branches, else local ones), the `commits` it would carry and `errors` (empty when a PR
    can be opened)."""
    row = proj.db.feature_branch(feat["id"])
    if row is None:
        raise BranchError(f"{feat['key']} has no confirmed branch yet; the Lead sends a Branch card (propose_branch) first")
    root = work_dir(proj, feat["id"])
    dest = bare_branch(destination) or default_destination(root, row)
    errors, commits = [], []
    try:
        commits = commits_ahead(root, row["branch"], dest)
    except BranchError as e:
        errors.append(str(e))
    lb = list_branches(root)
    remote = [n[len("origin/"):] for n in lb["remote"] if n.startswith("origin/")]
    names = [{"name": n, "remote": bool(remote)} for n in dict.fromkeys(remote or lb["local"]) if n != row["branch"]]
    if dest != row["branch"] and dest not in [b["name"] for b in names]:
        names.insert(0, {"name": dest, "remote": False})  # the chosen one is always offered, even if only local
    return {"source": row["branch"], "destination": dest, "branches": names, "commits": commits, "errors": errors,
            "pr_url": row["pr_url"], "pr_state": row["pr_state"]}


def _pr_url(text: str) -> str | None:
    for line in reversed([x.strip() for x in (text or "").splitlines() if x.strip()]):
        if re.match(r"^https?://\S+$", line):
            return line
    return None


def open_pr(proj, feat: dict, payload: dict) -> dict:
    """On approving a PR card (blocking, run in a thread): pre-checks, a push of the branch to origin, then `gh pr create`
    (an existing PR for the branch is reused). Returns {url, state, source, destination, title, body, commits, reused}.
    Raises BranchError with the reason on any failure; the card then stays pending so the Master can fix it and retry. The
    source is the feature's own branch, never the payload's."""
    row = proj.db.feature_branch(feat["id"])
    if row is None:
        raise BranchError(f"{feat['key']} has no confirmed branch, so there is nothing to push")
    source = row["branch"]
    dest = bare_branch(payload.get("destination"))
    title = " ".join((payload.get("title") or "").split())
    body = payload.get("body") or ""
    if not dest:
        raise BranchError("name the branch the pull request goes to")
    if not title:
        raise BranchError("the pull request needs a title")
    root = work_dir(proj, feat["id"])
    if git(root, "check-ref-format", "--branch", dest).returncode != 0:
        raise BranchError(f"'{dest}' is not a valid branch name")
    pr_preflight(root)
    commits = commits_ahead(root, source, dest)
    r = _run(["git", "push", "-u", "origin", source], root, PUSH_TIMEOUT_S)
    if r.returncode != 0:
        raise BranchError(f"pushing {source} to origin failed: {_tail(r) or 'no message'}")
    fd, body_file = tempfile.mkstemp(prefix="aiteams-pr-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        r = gh(root, "pr", "create", "--base", dest, "--head", source, "--title", title, "--body-file", body_file,
               timeout=PR_CREATE_TIMEOUT_S)
        reused = False
        url = _pr_url(r.stdout) if r.returncode == 0 else None
        if r.returncode == 0 and not url:
            raise BranchError(f"gh made the pull request but printed no URL: {_tail(r)}")
        if r.returncode != 0:
            if "already exists" not in _tail(r).lower():
                raise BranchError(f"gh pr create failed: {_tail(r) or 'no message'}")
            v = gh(root, "pr", "view", source, "--json", "url,state")
            info = {}
            if v.returncode == 0:
                try:
                    info = json.loads(v.stdout)
                except ValueError:
                    pass
            if not info.get("url") or str(info.get("state", "OPEN")).upper() != "OPEN":
                raise BranchError(f"gh says a pull request for {source} already exists, but it can't be reused: "
                                  f"{_tail(v) or _tail(r)}")
            url, reused = info["url"], True
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass
    return {"url": url, "state": "open", "source": source, "destination": dest, "title": title, "body": body,
            "commits": commits, "reused": reused}


def approved_pr_payload(merged: dict, done: dict) -> dict:
    """What a decided PR card shows: the edited choice that was opened, with its link (`pr_url`, `pr_state`, as on the feature)."""
    return {**merged, "source": done["source"], "destination": done["destination"], "title": done["title"],
            "body": done["body"], "commits": done["commits"], "pr_url": done["url"], "pr_state": done["state"]}


def pr_remote_state(repo: str, url: str) -> str:
    """OPEN | MERGED | CLOSED, from `gh pr view <url> --json state`. Raises BranchError when gh can't say."""
    if shutil.which("gh") is None:
        raise BranchError("the GitHub CLI `gh` isn't installed (`brew install gh`)")
    r = gh(repo, "pr", "view", url, "--json", "state", timeout=GH_TIMEOUT_S)
    if r.returncode != 0:
        raise BranchError(f"gh pr view failed: {_tail(r) or 'no message'}")
    try:
        state = str(json.loads(r.stdout).get("state", "")).upper()
    except ValueError:
        state = ""
    if state not in ("OPEN", "MERGED", "CLOSED"):
        raise BranchError(f"gh pr view gave no usable state: {_tail(r)}")
    return state


def remove_tree(repo: str, tree: str) -> tuple[str, list[str]]:
    """`git worktree remove` without --force, run from the main folder. ("removed", []) on success (a folder that is
    already gone is pruned); ("kept", why) when git refuses: the dirty files, or git's own message."""
    if not os.path.isdir(tree):
        git(repo, "worktree", "prune")
        return "removed", []
    r = git(repo, "worktree", "remove", tree)
    if r.returncode == 0:
        return "removed", []
    dirty = [x for x in git(tree, "status", "--porcelain").stdout.splitlines() if x.strip()]
    return "kept", dirty or [(r.stderr or r.stdout).strip() or "git worktree remove refused"]
