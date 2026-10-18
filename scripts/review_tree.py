#!/usr/bin/env python3
"""F-11 B: test a commit request on exactly what would land: a throwaway git worktree at HEAD plus only the
request's (or its open commit group's) files, then pytest, plus `npm run e2e` when a file is under ui/.

    python scripts/review_tree.py <commit id> [--no-e2e] [--keep]     # in a role turn: asks the daemon for the files
    python scripts/review_tree.py --files a.py ui/x.ts [--no-e2e] [--keep]   # no daemon

The commit-id form uses the role turn's own environment (the same per-turn token the MCP server uses). A listed file
that is missing from the working tree is deleted in the worktree. `.venv` and `ui/node_modules` are symlinked from
the repo, so nothing is installed. The worktree is removed afterwards unless --keep. Exit code 0 only if every run
passed. Prints `pytest: …` and `e2e: …` summary lines to quote in a review note, one `pytest-failed: <test id>` /
`e2e-failed: <title>` line per failing test right after its summary line (so a flake names itself), and (commit-id form)
one `overlap: …` line per other open request that shares a file with the request or group.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def group_files(cid: int) -> tuple[list[int], list[str], list[dict]]:
    env = os.environ
    need = ("AITEAMS_DAEMON_URL", "AITEAMS_PROJECT", "AITEAMS_TURN", "AITEAMS_TURN_TOKEN")
    if not all(env.get(k) for k in need):
        sys.exit("review_tree: a commit id needs a role turn's environment; outside a turn use --files")
    req = urllib.request.Request(env["AITEAMS_DAEMON_URL"].rstrip("/") + f"/internal/commits/{cid}/files", headers={
        "X-AITeams-Project": env["AITEAMS_PROJECT"], "X-AITeams-Turn": env["AITEAMS_TURN"],
        "X-AITeams-Token": env["AITEAMS_TURN_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail")
        except (ValueError, AttributeError):
            detail = e.reason
        sys.exit(f"review_tree: commit #{cid}: daemon said {e.code}: {detail}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"review_tree: daemon unreachable: {e}")
    return res["ids"], res["files"], res.get("overlap") or []


def overlap_lines(overlap: list[dict]) -> list[str]:
    """F-22: one line per other open request that shares a file with this one, naming its feature and Lead, for the
    Lead to quote in the review note: `overlap: aiteams/db.py also in #81 (F-23, Lead 2, lead_review)`."""
    out = []
    for o in overlap:
        who = (f"{o['feature']}, {o.get('lead_name') or 'no Lead yet'}" if o.get("feature") else "no feature")
        out.append(f"overlap: {', '.join(o['files'])} also in #{o['commit_id']} ({who}, {o['state']})")
    return out


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def last_line(text: str, needle: str) -> str:
    lines = [l.strip() for l in text.splitlines() if needle in l]
    return lines[-1] if lines else (text.strip().splitlines() or ["(no output)"])[-1]


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def pytest_failures(out: str) -> list[str]:
    """F-26: test ids from pytest's `short test summary info` section (`-rfE`): `FAILED tests/x.py::test_y[p] - msg` and
    `ERROR tests/x.py::...` (a setup error or a collection error). In order, once each. Only that section counts, so a
    line a test printed itself can't pass for a failure."""
    ids: list[str] = []
    in_summary = False
    for line in ANSI_RE.sub("", out).splitlines():
        if line.startswith("=") and "short test summary info" in line:
            in_summary = True
        elif line.startswith("="):
            in_summary = False
        m = in_summary and re.match(r"(?:FAILED|ERROR) (\S.*)", line)
        nodeid = _before_message(m.group(1)) if m else None
        if nodeid and nodeid not in ids:
            ids.append(nodeid)
    return ids


def _before_message(rest: str) -> str:
    """`tests/x.py::t[a - b] - msg` -> `tests/x.py::t[a - b]`: cut at the first " - " outside square brackets (a
    parametrize id may contain one)."""
    depth = 0
    for i, ch in enumerate(rest):
        depth += (ch == "[") - (ch == "]" and depth > 0)
        if depth == 0 and rest.startswith(" - ", i):
            return rest[:i].strip()
    return rest.strip()


def e2e_failures(out: str) -> list[str]:
    """F-26: titles of the failing Playwright tests from the list reporter's `✘` lines (`e2e/x.spec.ts:10:3 › title`),
    without the running index, the `[chromium]` project tag, the timing or a retry suffix. In order, once each."""
    titles: list[str] = []
    for line in ANSI_RE.sub("", out).splitlines():
        m = re.match(r"\s*[✘✗x]\s+\d+\s+(?:\[[^\]]*\]\s+›\s+)?(.*?)(?:\s+\(retry #\d+\))?\s*(?:\([\d.]+m?s\))?\s*$", line)
        if m and m.group(1) and m.group(1) not in titles:
            titles.append(m.group(1))
    return titles


def with_failures(summary: str, failed: list[str], tag: str) -> str:
    """The summary line, then one `<tag>: <id>` line per failure."""
    return "\n".join([summary, *(f"{tag}: {f}" for f in failed)])


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
    r = subprocess.run([py, "-B", "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"], cwd=tree, capture_output=True,
                       text=True)
    failed = pytest_failures(r.stdout) if r.returncode != 0 else []
    return r.returncode == 0, with_failures("pytest: " + last_line(r.stdout, " in "), failed, "pytest-failed")


def run_e2e(tree: Path) -> tuple[bool, str]:
    r = subprocess.run(["npm", "run", "e2e"], cwd=tree / "ui", capture_output=True, text=True)
    out = r.stdout + r.stderr
    failed = e2e_failures(out) if r.returncode != 0 else []
    return r.returncode == 0, with_failures("e2e: " + last_line(out, "passed" if "passed" in out else "failed"),
                                            failed, "e2e-failed")


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
    ids, files, overlap = group_files(args.commit) if args.commit is not None else ([], args.files, [])
    repo = Path(args.repo)
    tree = Path(tempfile.mkdtemp(prefix="aiteams-review-")) / "tree"
    print(f"review    {'#' + ' + #'.join(map(str, ids)) if ids else 'files'}: {len(files)} file(s) on HEAD in {tree}")
    for line in overlap_lines(overlap):
        print(line)
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
