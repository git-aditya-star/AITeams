"""F-41: one heavy check at a time on the machine. A review run (scripts/review_tree.py) and the Playwright e2e each take a
machine-wide file lock, so six parallel turns never run two of them side by side (that is what made the machine load spike).

    python scripts/heavy.py -- npm run e2e      # waits for the lock, runs the command, passes its exit code on

The lock is `fcntl.flock` on ~/.aiteams/heavy.lock (AITEAMS_HOME moves it): the OS drops it when the process ends, however it
ends, so a crashed run never blocks the next one. A run started by a run that already holds it (review_tree -> pytest ->
review_tree tests) is let through: the holder marks its children with AITEAMS_HEAVY_HELD."""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HELD = "AITEAMS_HEAVY_HELD"


def lock_path() -> Path:
    return Path(os.environ.get("AITEAMS_HOME") or Path.home() / ".aiteams") / "heavy.lock"


@contextmanager
def heavy_lock(say=lambda text: print(text, flush=True)):
    """Hold the machine's heavy-run lock for the block; `say` is told once if it has to wait."""
    if os.environ.get(HELD):
        yield  # an outer run already holds it
        return
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            say("heavy: another review or e2e run holds the machine's heavy lock; waiting for it")
            t0 = time.time()
            fcntl.flock(fd, fcntl.LOCK_EX)
            say(f"heavy: got the lock after {int(time.time() - t0)}s")
        os.environ[HELD] = "1"
        try:
            yield
        finally:
            os.environ.pop(HELD, None)
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print("usage: python scripts/heavy.py -- <command> [args...]", file=sys.stderr)
        return 2
    with heavy_lock():
        return subprocess.call(argv)


if __name__ == "__main__":
    sys.exit(main())
