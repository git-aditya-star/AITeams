"""`aiteams upgrade` pieces over a temp AITeams-shaped git repo and a fake `run` for uv."""
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

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
    """Real subprocess for git; recorded fakes for uv, node and npm (`npm run build` writes static/index.html)."""

    def __init__(self, node="v24.11.0", npm="11.6.2", build_writes=True):
        self.calls = []
        self.node, self.npm, self.build_writes = node, npm, build_writes

    def __call__(self, cmd, stream=False, cwd=None):
        self.calls.append((list(cmd), stream, cwd))
        if cmd[0] == "uv":
            out = b"/fake/bin\n" if cmd[1:3] == ["tool", "dir"] else b""
            return subprocess.CompletedProcess(cmd, 0, out, b"")
        if cmd[0] in ("node", "npm"):
            if cmd[1] == "--version":
                ver = self.node if cmd[0] == "node" else self.npm
                if ver is None:
                    raise FileNotFoundError(cmd[0])
                return subprocess.CompletedProcess(cmd, 0, ver.encode() + b"\n", b"")
            if cmd[1] == "ci":
                (Path(cwd) / "node_modules" / "x").mkdir(parents=True)
            if cmd[1:] == ["run", "build"] and self.build_writes:
                static = Path(cwd).parent / "aiteams" / "static"
                static.mkdir(parents=True, exist_ok=True)
                (static / "index.html").write_text("<html>built")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.run(cmd, cwd=cwd, capture_output=True)

    def npm_calls(self):
        return [(c, cwd) for c, _, cwd in self.calls if c[0] == "npm" and c[1] != "--version"]


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


def test_runtime_json_round_trips(home):
    rec = upgrade.runtime_record("abc123", "HEAD", "/src", "0.9.0")
    config.write_runtime_info(rec)
    info = config.runtime_info()
    assert info["sha"] == "abc123" and info["ref"] == "HEAD" and info["source"] == "/src" and info["version"] == "0.9.0"
    assert abs(info["installed_at"] - time.time()) < 5
    assert json.load(open(home / "runtime.json"))["sha"] == "abc123"


@pytest.fixture
def m2c_src(src):
    """The source repo after M2c: aiteams/static is gitignored and not committed."""
    r, git = src
    git("rm", "-rq", "--cached", "aiteams/static")
    (r / ".gitignore").write_text("aiteams/static/\n")
    (r / "ui" / "package-lock.json").write_text("{}")
    git("add", "-A")
    git("commit", "-qm", "static leaves git")
    return r, git


def _fake_cli(monkeypatch, run):
    from aiteams import cli
    monkeypatch.setattr(upgrade, "run", run)
    order = []
    monkeypatch.setattr(cli, "stop_daemon", lambda: order.append("stop") or True)
    monkeypatch.setattr(upgrade, "install", lambda snap, run=None: order.append(("install", str(snap))))
    monkeypatch.setattr(cli, "start_daemon", lambda exe, port: order.append(("start", exe, port)) or "http://127.0.0.1:7700")
    monkeypatch.setattr(cli, "_health", lambda url: {"pid": 4242})
    return cli, order


def test_cmd_upgrade_step_order(m2c_src, home, monkeypatch, capsys):
    """build -> stop -> install -> record -> start, with the uv bin shim (not sys.executable) starting the daemon."""
    r, _ = m2c_src
    run = FakeRun()
    cli, order = _fake_cli(monkeypatch, run)
    real_build = upgrade.build_ui
    monkeypatch.setattr(upgrade, "build_ui", lambda snap, run=None: order.append("build") or real_build(snap, run))
    monkeypatch.chdir(r)
    cli.main(["upgrade"])
    sha = upgrade.resolve_ref(str(r), "HEAD", run)
    snap = str(config.runtime_dir() / sha)
    assert order == ["build", "stop", ("install", snap), ("start", ["/fake/bin/aiteams"], 7700)]
    assert (Path(snap) / "aiteams" / "static" / "index.html").exists()
    info = config.runtime_info()
    assert info["sha"] == sha and info["source"] == os.path.realpath(str(r)) and info["version"] == "0.9.0"
    out = capsys.readouterr().out
    assert "ui        built with node v24.11.0 in " in out
    assert f"upgraded to {sha[:7]} (0.9.0), daemon pid 4242" in out
    # a second run remembers the source from runtime.json
    monkeypatch.chdir(home)
    cli.main(["upgrade"])
    assert order[-1] == ("start", ["/fake/bin/aiteams"], 7700)


def test_cmd_upgrade_has_no_yes_flag(m2c_src, home, monkeypatch):
    cli, _ = _fake_cli(monkeypatch, FakeRun())
    monkeypatch.chdir(m2c_src[0])
    with pytest.raises(SystemExit) as e:
        cli.main(["upgrade", "--yes"])
    assert e.value.code == 2


@pytest.mark.parametrize("node,npm", [(None, "11.6.2"), ("v20.11.0", "10.2.4"), ("v22.11.0", "10.9.0"), ("v24.11.0", None)])
def test_cmd_upgrade_aborts_before_stop_without_node(m2c_src, home, monkeypatch, capsys, node, npm):
    r, _ = m2c_src
    run = FakeRun(node=node, npm=npm)
    cli, order = _fake_cli(monkeypatch, run)
    monkeypatch.chdir(r)
    with pytest.raises(SystemExit) as e:
        cli.main(["upgrade"])
    assert e.value.code == 1 and order == [] and run.npm_calls() == []
    err = capsys.readouterr().err
    assert "Node >= 22.12 and npm are needed to build the UI (found: " in err and "brew install node@24" in err
    assert (f"node {node}" if node else "no node") in err
    assert config.runtime_info() == {}


def test_build_ui_runs_npm_in_the_snapshot(tmp_path):
    snap = tmp_path / "snap"
    (snap / "ui").mkdir(parents=True)
    run = FakeRun(node="v22.12.0")
    line = upgrade.build_ui(snap, run)
    ui = str(snap / "ui")
    assert run.npm_calls() == [(["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund"], ui), (["npm", "run", "build"], ui)]
    assert all(stream for c, stream, _ in run.calls if c[0] == "npm" and c[1] != "--version")
    assert (snap / "aiteams" / "static" / "index.html").exists() and not (snap / "ui" / "node_modules").exists()
    assert line.startswith("ui        built with node v22.12.0 in ")


def test_build_ui_prebuilt_snapshot_skips_npm(src, tmp_path):
    r, _ = src
    run = FakeRun(node=None, npm=None)
    snap = upgrade.snapshot(str(r), upgrade.resolve_ref(str(r), "HEAD", run), tmp_path / "runtime", run)
    assert upgrade.build_ui(snap, run) == "ui        prebuilt in snapshot"
    assert [c for c, _, _ in run.calls if c[0] in ("node", "npm")] == []


def test_build_ui_without_index_html_fails(tmp_path):
    snap = tmp_path / "snap"
    (snap / "ui").mkdir(parents=True)
    run = FakeRun(build_writes=False)
    with pytest.raises(upgrade.UpgradeError, match="did not write"):
        upgrade.build_ui(snap, run)
    assert not (snap / "ui" / "node_modules").exists()


def test_build_ui_npm_failure_is_an_upgrade_error(tmp_path, monkeypatch):
    snap = tmp_path / "snap"
    (snap / "ui").mkdir(parents=True)
    base = FakeRun()

    def run(cmd, stream=False, cwd=None):
        if cmd[:3] == ["npm", "run", "build"]:
            return subprocess.CompletedProcess(cmd, 2, None, None)
        return base(cmd, stream, cwd)
    with pytest.raises(upgrade.UpgradeError, match=r"npm run build failed \(2\)"):
        upgrade.build_ui(snap, run)


def test_wheel_includes_gitignored_static(tmp_path):
    """hatchling honours .gitignore; the `artifacts` entry in pyproject must put the built UI back into the wheel."""
    from hatchling.builders.wheel import WheelBuilder
    root = Path(__file__).resolve().parent.parent
    proj = tmp_path / "proj"
    (proj / "aiteams" / "static" / "assets").mkdir(parents=True)
    for f in ("pyproject.toml", "LICENSE", "NOTICE"):
        (proj / f).write_bytes((root / f).read_bytes())
    (proj / "README.md").write_text("x")
    (proj / ".gitignore").write_text((root / ".gitignore").read_text())
    assert "aiteams/static/" in (proj / ".gitignore").read_text()
    (proj / "aiteams" / "__init__.py").write_text("")
    (proj / "aiteams" / "static" / "index.html").write_text("<html>")
    (proj / "aiteams" / "static" / "assets" / "index-X.js").write_text("1")
    out = tmp_path / "dist"
    wheel = next(iter(WheelBuilder(str(proj)).build(directory=str(out), versions=["standard"])))
    names = zipfile.ZipFile(wheel).namelist()
    assert "aiteams/static/index.html" in names and "aiteams/static/assets/index-X.js" in names


def _group_db(path: Path, rows: list[tuple]) -> None:
    """A project DB (current schema) under <path>/.aiteams with commits (id, role, feature_id, state, note, requires)."""
    from aiteams.db import ProjectDB
    db = ProjectDB(str(path))
    for cid, role, fid, state, note, requires in rows:
        db.x("INSERT INTO commits(id, feature_id, role, files, message, hashes, state, note, requires, created_at,"
             " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", cid, fid, role, "[]", "m", "{}", state, note,
             json.dumps(requires) if requires else None, 0, 0)
    db.close()


def test_upgrade_warns_about_half_committed_groups(tmp_path):
    """F-11: only a committed request whose `requires` isn't committed is warned about. A committed base with a pending
    dependent is the safe order and gives nothing, as do rejected dependents, superseded requirements and singles;
    a missing DB or a DB from before commit groups gives nothing."""
    assert upgrade.half_committed_groups(tmp_path) == []
    _group_db(tmp_path, [
        (1, "backend", 9, "committed", None, None), (2, "frontend", 9, "master_review", None, [1]),  # base first: no
        (3, "backend", 9, "committed", None, None), (4, "frontend", 9, "rejected", "no", [3]),     # rejected: no
        (5, "backend", None, "committed", None, None), (6, "qa", None, "lead_review", None, None),  # singles: no
        (7, "backend", 2, "committed", None, None), (8, "frontend", 2, "committed", None, [7]),     # whole: no
        (10, "backend", 3, "rejected", "superseded by #11", None), (11, "backend", 3, "committed", None, None),
        (12, "frontend", 3, "committed", None, [10]),                                                # superseded: no
        (13, "backend", 4, "master_review", None, None), (14, "frontend", 4, "committed", None, [13]),  # warn
        (15, "backend", None, "rejected", "no", None), (16, "frontend", None, "committed", None, [15, 7]),  # warn
    ])
    assert upgrade.half_committed_groups(tmp_path) == [
        "warning: #14 (F-4, frontend) is committed but needs #13, which is master_review",
        "warning: #16 (frontend) is committed but needs #15, which is rejected"]
    import sqlite3
    conn = sqlite3.connect(tmp_path / ".aiteams" / "state.db")
    conn.execute("ALTER TABLE commits DROP COLUMN requires")
    conn.commit()
    conn.close()
    assert upgrade.half_committed_groups(tmp_path) == []


def test_cmd_upgrade_prints_the_group_warning(m2c_src, home, monkeypatch, capsys):
    r, _ = m2c_src
    _group_db(r, [(1, "backend", 9, "master_review", None, None), (2, "frontend", 9, "committed", None, [1])])
    cli, order = _fake_cli(monkeypatch, FakeRun())
    monkeypatch.chdir(r)
    cli.main(["upgrade"])
    out = capsys.readouterr().out
    assert "warning: #2 (F-9, frontend) is committed but needs #1, which is master_review" in out
    assert order[-1][0] == "start"  # a warning, never a block


def test_cmd_upgrade_needs_a_source_repo(home, tmp_path, monkeypatch, capsys):
    from aiteams import cli
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        cli.main(["upgrade"])
    assert e.value.code == 2 and "not an AITeams checkout" in capsys.readouterr().err
