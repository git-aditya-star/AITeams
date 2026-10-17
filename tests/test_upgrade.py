"""`aiteams upgrade` pieces over a temp AITeams-shaped git repo and a fake `run` for uv."""
import json
import os
import subprocess
import time

import pytest

from aiteams import config, upgrade


@pytest.fixture
def src(tmp_path):
    r = tmp_path / "src"
    (r / "aiteams" / "static").mkdir(parents=True)
    (r / "ui" / "src").mkdir(parents=True)
    (r / "pyproject.toml").write_text('[project]\nname = "aiteams"\nversion = "0.9.0"\n')
    (r / "aiteams" / "__init__.py").write_text("")
    (r / "aiteams" / "static" / "index.html").write_text("<html>")
    (r / "ui" / "src" / "main.tsx").write_text("1")
    git = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                                    capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "one")
    return r, git


class FakeRun:
    """Real subprocess for git; recorded fakes for uv."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, stream=False, cwd=None):
        self.calls.append((list(cmd), stream))
        if cmd[0] == "uv":
            out = b"/fake/bin\n" if cmd[1:3] == ["tool", "dir"] else b""
            return subprocess.CompletedProcess(cmd, 0, out, b"")
        return subprocess.run(cmd, cwd=cwd, capture_output=True)


def test_is_source_repo(src, tmp_path):
    r, _ = src
    assert upgrade.is_source_repo(r) is True
    assert upgrade.is_source_repo(tmp_path) is False


def test_snapshot_extracts_ref_and_removes_older(src, tmp_path):
    r, git = src
    run = FakeRun()
    sha1 = upgrade.resolve_ref(str(r), "HEAD", run)
    (r / "aiteams" / "static" / "index.html").write_text("<html>v2")
    git("commit", "-qam", "two")
    sha2 = upgrade.resolve_ref(str(r), "HEAD", run)
    assert sha1 != sha2 and upgrade.resolve_ref(str(r), "HEAD~1", run) == sha1
    runtime = tmp_path / "runtime"
    old = runtime / sha1
    old.mkdir(parents=True)
    (old / "stale").write_text("x")
    (r / "untracked.txt").write_text("not in the snapshot")
    snap = upgrade.snapshot(str(r), sha2, runtime, run)
    assert snap == runtime / sha2 and (snap / "pyproject.toml").exists()
    assert (snap / "aiteams" / "static" / "index.html").read_text() == "<html>v2"
    assert not (snap / "untracked.txt").exists() and not old.exists()
    assert upgrade.snapshot_version(snap) == "0.9.0"
    assert upgrade.dirty(str(r), run) is True  # untracked file counts
    # rollback = snapshot of the older sha; the newer dir goes away
    back = upgrade.snapshot(str(r), sha1, runtime, run)
    assert (back / "aiteams" / "static" / "index.html").read_text() == "<html>" and not snap.exists()


def test_ui_stale(src):
    r, git = src
    run = FakeRun()
    sha = upgrade.resolve_ref(str(r), "HEAD", run)
    assert upgrade.ui_stale(str(r), sha, run) is False  # same commit
    time.sleep(1.1)  # %ct is in seconds
    (r / "ui" / "src" / "main.tsx").write_text("2")
    git("commit", "-qam", "ui only")
    sha = upgrade.resolve_ref(str(r), "HEAD", run)
    assert upgrade.ui_stale(str(r), sha, run) is True
    time.sleep(1.1)
    (r / "aiteams" / "static" / "index.html").write_text("<html>built")
    git("commit", "-qam", "build")
    sha = upgrade.resolve_ref(str(r), "HEAD", run)
    assert upgrade.ui_stale(str(r), sha, run) is False


def test_runtime_json_round_trips(home):
    rec = upgrade.runtime_record("abc123", "HEAD", "/src", "0.9.0")
    config.write_runtime_info(rec)
    info = config.runtime_info()
    assert info["sha"] == "abc123" and info["ref"] == "HEAD" and info["source"] == "/src" and info["version"] == "0.9.0"
    assert abs(info["installed_at"] - time.time()) < 5
    assert json.load(open(home / "runtime.json"))["sha"] == "abc123"


def test_cmd_upgrade_step_order(src, home, monkeypatch, capsys):
    """stop -> install -> record -> start, with the uv bin shim (not sys.executable) starting the daemon."""
    r, _ = src
    from aiteams import cli
    run = FakeRun()
    monkeypatch.setattr(upgrade, "run", run)
    order = []
    monkeypatch.setattr(cli, "stop_daemon", lambda: order.append("stop") or True)
    monkeypatch.setattr(upgrade, "install", lambda snap, run=None: order.append(("install", str(snap))))
    monkeypatch.setattr(cli, "start_daemon", lambda exe, port: order.append(("start", exe, port)) or "http://127.0.0.1:7700")
    monkeypatch.setattr(cli, "_health", lambda url: {"pid": 4242})
    monkeypatch.chdir(r)
    cli.main(["upgrade", "--yes"])
    sha = upgrade.resolve_ref(str(r), "HEAD", run)
    snap = str(config.runtime_dir() / sha)
    assert order == ["stop", ("install", snap), ("start", ["/fake/bin/aiteams"], 7700)]
    info = config.runtime_info()
    assert info["sha"] == sha and info["source"] == os.path.realpath(str(r)) and info["version"] == "0.9.0"
    out = capsys.readouterr().out
    assert f"upgraded to {sha[:7]} (0.9.0), daemon pid 4242" in out
    # a second run remembers the source from runtime.json
    monkeypatch.chdir(home)
    cli.main(["upgrade", "--yes"])
    assert order[-1] == ("start", ["/fake/bin/aiteams"], 7700)


def test_cmd_upgrade_needs_a_source_repo(home, tmp_path, monkeypatch, capsys):
    from aiteams import cli
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        cli.main(["upgrade"])
    assert e.value.code == 2 and "not an AITeams checkout" in capsys.readouterr().err
