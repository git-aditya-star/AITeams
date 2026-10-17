#!/usr/bin/env python3
"""F-11 B: test a commit request on exactly what would land: a throwaway git worktree at HEAD plus only the
request's (or its open commit group's) files, then pytest, plus `npm run e2e` when a file is under ui/.

    python scripts/review_tree.py <commit id> [--no-e2e] [--keep]     # in a role turn: asks the daemon for the files
    python scripts/review_tree.py --files a.py ui/x.ts [--no-e2e] [--keep]   # no daemon

The commit-id form uses the role turn's own environment (the same per-turn token the MCP server uses). A listed file
that is missing from the working tree is deleted in the worktree. `.venv` and `ui/node_modules` are symlinked from
the repo, so nothing is installed. The worktree is removed afterwards unless --keep. Exit code 0 only if every run
passed. Prints `pytest: …` and `e2e: …` summary lines to quote in a review note.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def group_files(cid: int) -> tuple[list[int], list[str]]:
    env = os.environ
    need = ("AITEAMS_DAEMON_URL", "AITEAMS_PROJECT", "AITEAMS_TURN", "AITEAMS_TURN_TOKEN")
    if not all(env.get(k) for k in need):
        sys.exit("review_tree: a commit id needs a role turn's environment; outside a turn use --files")
    req = urllib.request.Request(env["AITEAMS_DAEMON_URL"].rstrip("/") + f"/internal/commits/{cid}/files", headers={
        "X-AITeams-Project": env["AITEAMS_PROJECT"], "X-AITeams-Turn": env["AITEAMS_TURN"],
        "X-AITeams-Token": env["AITEAMS_TURN_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode())
    return res["ids"], res["files"]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def last_line(text: str, needle: str) -> str:
    lines = [l.strip() for l in text.splitlines() if needle in l]
    return lines[-1] if lines else (text.strip().splitlines() or ["(no output)"])[-1]


def prepare(repo: Path, tree: Path, files: list[str]) -> None:
    git(repo, "worktree", "add", "--detach", str(tree), "HEAD")
    for f in files:
        src, dst = repo / f, tree / f
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists():
            git(tree, "rm", "-q", "--", f)
    for rel in (".venv", "ui/node_modules"):
        if (repo / rel).exists() and not (tree / rel).exists():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).symlink_to(repo / rel)


def run_pytest(tree: Path) -> tuple[bool, str]:
    py = str(tree / ".venv" / "bin" / "python")
    where = subprocess.run([py, "-B", "-c", "import aiteams; print(aiteams.__file__)"], cwd=tree,
                           capture_output=True, text=True)
    if not where.stdout.strip().startswith(str(tree.resolve())) and not where.stdout.strip().startswith(str(tree)):
        return False, f"pytest: ABORTED, aiteams imports from {where.stdout.strip() or where.stderr.strip()}, not the worktree"
    r = subprocess.run([py, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=tree, capture_output=True,
                       text=True)
    return r.returncode == 0, "pytest: " + last_line(r.stdout, " in ")


def run_e2e(tree: Path) -> tuple[bool, str]:
    r = subprocess.run(["npm", "run", "e2e"], cwd=tree / "ui", capture_output=True, text=True)
    out = r.stdout + r.stderr
    return r.returncode == 0, "e2e: " + last_line(out, "passed" if "passed" in out else "failed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="review_tree", description=__doc__.split("\n\n")[0])
    ap.add_argument("commit", nargs="?", type=int)
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--no-e2e", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave the worktree in place (its path is printed)")
    ap.add_argument("--repo", default=str(ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if (args.commit is None) == (args.files is None):
        ap.error("give a commit id or --files")
    ids, files = group_files(args.commit) if args.commit is not None else ([], args.files)
    repo = Path(args.repo)
    tree = Path(tempfile.mkdtemp(prefix="aiteams-review-")) / "tree"
    print(f"review    {'#' + ' + #'.join(map(str, ids)) if ids else 'files'}: {len(files)} file(s) on HEAD in {tree}")
    ok = True
    try:
        prepare(repo, tree, files)
        passed, line = run_pytest(tree)
        ok &= passed
        print(line)
        if any(f.startswith("ui/") for f in files) and not args.no_e2e:
            passed, line = run_e2e(tree)
            ok &= passed
            print(line)
    finally:
        if args.keep:
            print(f"kept      {tree}")
        else:
            git(repo, "worktree", "remove", "--force", str(tree), check=False)
            shutil.rmtree(tree.parent, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
