"""`aiteams upgrade`: git-archive snapshot -> ~/.aiteams/runtime/<sha>/ -> UI build (npm) -> non-editable tool install.

Pure functions over a `run(cmd, stream=False) -> CompletedProcess` parameter so tests can inject a fake.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tarfile
import time
import tomllib
from pathlib import Path


class UpgradeError(Exception):
    pass


def run(cmd: list[str], stream: bool = False, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Default runner: capture stdout/stderr as bytes, or stream them to the terminal when `stream`."""
    return subprocess.run(cmd, cwd=cwd, capture_output=not stream)


def _use(run):
    # Resolved at call time so tests can monkeypatch `upgrade.run` (a default argument would bind the original).
    return run if run is not None else globals()["run"]


def _out(cp: subprocess.CompletedProcess) -> str:
    out = cp.stdout or b""
    return (out.decode() if isinstance(out, bytes) else str(out)).strip()


def _check(cp: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if cp.returncode != 0:
        err = cp.stderr or b""
        err = err.decode(errors="replace") if isinstance(err, bytes) else str(err)
        raise UpgradeError(f"{what} failed ({cp.returncode}): {err.strip()[:500]}")
    return cp


def is_source_repo(path: str | Path) -> bool:
    """A git checkout whose pyproject.toml is the aiteams project."""
    p = Path(path)
    try:
        if not (p / ".git").exists():
            return False
        with open(p / "pyproject.toml", "rb") as f:
            return tomllib.load(f).get("project", {}).get("name") == "aiteams"
    except (OSError, tomllib.TOMLDecodeError):
        return False


def resolve_ref(src: str, ref: str, run=None) -> str:
    run = _use(run)
    return _out(_check(run(["git", "-C", src, "rev-parse", f"{ref}^{{commit}}"]), f"git rev-parse {ref}"))


def dirty(src: str, run=None) -> bool:
    run = _use(run)
    return bool(_out(_check(run(["git", "-C", src, "status", "--porcelain"]), "git status")))


def snapshot(src: str, sha: str, runtime_dir: Path, run=None) -> Path:
    """Extract `git archive <sha>` into runtime_dir/<sha>/ (fresh) and remove every other snapshot."""
    run = _use(run)
    dest = Path(runtime_dir) / sha
    cp = _check(run(["git", "-C", src, "archive", "--format=tar", sha]), "git archive")
    data = cp.stdout if isinstance(cp.stdout, bytes) else str(cp.stdout).encode()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    for other in Path(runtime_dir).iterdir():
        if other.is_dir() and other != dest:
            shutil.rmtree(other, ignore_errors=True)
    return dest


def snapshot_version(snap: Path) -> str:
    with open(Path(snap) / "pyproject.toml", "rb") as f:
        return str(tomllib.load(f).get("project", {}).get("version") or "0")


NODE_MIN = (22, 12)
NODE_HINT = "install Node 24 LTS, e.g. `brew install node@24`"


def _tool_version(cmd: str, run) -> str | None:
    try:
        cp = run([cmd, "--version"])
    except FileNotFoundError:
        return None
    return (_out(cp) or None) if cp.returncode == 0 else None


def node_version(run=None) -> str | None:
    """`node --version` as printed (e.g. `v24.11.0`), or None when node is not on PATH."""
    return _tool_version("node", _use(run))


def _vtuple(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", ver)[:3])


def build_ui(snap: Path, run=None) -> str:
    """`npm ci` + `npm run build` in <snap>/ui (writes <snap>/aiteams/static); returns the status line.

    A snapshot that already has aiteams/static/index.html (a pre-M2c ref, i.e. a rollback) is used as is.
    """
    run = _use(run)
    snap = Path(snap)
    index = snap / "aiteams" / "static" / "index.html"
    if index.exists():
        return "ui        prebuilt in snapshot"
    node, npm = node_version(run), _tool_version("npm", run)
    if not node or _vtuple(node) < NODE_MIN or not npm:
        found = ", ".join([f"node {node}" if node else "no node", f"npm {npm}" if npm else "no npm"])
        raise UpgradeError(f"Node >= 22.12 and npm are needed to build the UI (found: {found}); {NODE_HINT}")
    ui = snap / "ui"
    t0 = time.monotonic()
    try:
        _check(run(["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund"], stream=True, cwd=str(ui)), "npm ci")
        _check(run(["npm", "run", "build"], stream=True, cwd=str(ui)), "npm run build")
        if not index.exists():
            raise UpgradeError(f"npm run build did not write {index}")
    finally:
        shutil.rmtree(ui / "node_modules", ignore_errors=True)
    return f"ui        built with node {node} in {time.monotonic() - t0:.0f}s"


def install(snap: Path, run=None) -> None:
    """Non-editable tool install from the snapshot; replaces the running venv, so import nothing lazily after it."""
    run = _use(run)
    _check(run(["uv", "tool", "install", "--reinstall", "--force", str(snap)], stream=True), "uv tool install")


def tool_bin(run=None) -> Path:
    run = _use(run)
    return Path(_out(_check(run(["uv", "tool", "dir", "--bin"]), "uv tool dir --bin")))


def runtime_record(sha: str, ref: str, source: str, version: str) -> dict:
    return {"sha": sha, "ref": ref, "source": source, "installed_at": time.time(), "version": version}
