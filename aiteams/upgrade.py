"""`aiteams upgrade`: git-archive snapshot of the source repo -> ~/.aiteams/runtime/<sha>/ -> non-editable tool install.

Pure functions over a `run(cmd, stream=False) -> CompletedProcess` parameter so tests can inject a fake.
"""
from __future__ import annotations

import io
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


def _last_commit_ts(src: str, sha: str, path: str, run=None) -> int:
    run = _use(run)
    out = _out(run(["git", "-C", src, "log", "-1", "--format=%ct", sha, "--", path]))
    return int(out) if out.isdigit() else 0


def ui_stale(src: str, sha: str, run=None) -> bool:
    """True when ui/src has a newer commit than aiteams/static at <sha> (the build was not committed)."""
    return _last_commit_ts(src, sha, "ui/src", run) > _last_commit_ts(src, sha, "aiteams/static", run)


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


def install(snap: Path, run=None) -> None:
    """Non-editable tool install from the snapshot; replaces the running venv, so import nothing lazily after it."""
    run = _use(run)
    _check(run(["uv", "tool", "install", "--reinstall", "--force", str(snap)], stream=True), "uv tool install")


def tool_bin(run=None) -> Path:
    run = _use(run)
    return Path(_out(_check(run(["uv", "tool", "dir", "--bin"]), "uv tool dir --bin")))


def runtime_record(sha: str, ref: str, source: str, version: str) -> dict:
    return {"sha": sha, "ref": ref, "source": source, "installed_at": time.time(), "version": version}
