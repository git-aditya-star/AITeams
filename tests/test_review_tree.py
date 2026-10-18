"""F-11 R2: scripts/review_tree.py builds HEAD + only the listed files in a throwaway worktree, runs the tests there,
and removes the worktree."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def rt():
    spec = importlib.util.spec_from_file_location("review_tree", ROOT / "scripts" / "review_tree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "ui").mkdir(parents=True)
    for name, text in (("a.py", "old\n"), ("gone.py", "x\n"), ("other.py", "head\n"), ("ui/x.ts", "1\n")):
        (r / name).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=r, check=True)
    (r / ".venv").mkdir()
    (r / "ui" / "node_modules").mkdir()
    (r / "a.py").write_text("new\n")         # listed: copied
    (r / "added.py").write_text("added\n")   # listed, untracked: copied
    (r / "gone.py").unlink()                 # listed, deleted: removed in the worktree
    (r / "other.py").write_text("WIP\n")     # not listed: the worktree keeps HEAD's version
    return r


def worktrees(repo):
    return subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout.splitlines()


def test_tree_is_head_plus_listed_files_and_is_removed(rt, repo, monkeypatch, capsys):
    seen = {}

    def fake_pytest(tree):
        seen.update({p: (tree / p).read_text() if (tree / p).exists() else None
                     for p in ("a.py", "added.py", "gone.py", "other.py")})
        seen["venv"] = (tree / ".venv").resolve() == (repo / ".venv").resolve()
        seen["node_modules"] = (tree / "ui" / "node_modules").is_symlink()
        seen["tree"] = tree
        return True, "pytest: 3 passed in 0.1s"
    monkeypatch.setattr(rt, "run_pytest", fake_pytest)
    monkeypatch.setattr(rt, "run_e2e", lambda tree: pytest.fail("no ui/ file listed: e2e must not run"))
    code = rt.main(["--files", "a.py", "added.py", "gone.py", "--repo", str(repo)])
    assert code == 0
    assert {k: seen[k] for k in ("a.py", "added.py", "gone.py", "other.py")} == {
        "a.py": "new\n", "added.py": "added\n", "gone.py": None, "other.py": "head\n"}
    assert seen["venv"] and seen["node_modules"]
    assert not seen["tree"].exists() and len(worktrees(repo)) == 1  # cleaned up
    out = capsys.readouterr().out
    assert "review    files: 3 file(s) on HEAD in" in out and "pytest: 3 passed in 0.1s" in out
    assert (repo / "other.py").read_text() == "WIP\n"  # the real working tree is untouched


def test_ui_file_runs_e2e_failures_set_the_exit_code_and_keep(rt, repo, monkeypatch, capsys):
    monkeypatch.setattr(rt, "run_pytest", lambda tree: (True, "pytest: 1 passed"))
    monkeypatch.setattr(rt, "run_e2e", lambda tree: (False, "e2e: 1 failed"))
    assert rt.main(["--files", "ui/x.ts", "--repo", str(repo)]) == 1
    assert "e2e: 1 failed" in capsys.readouterr().out
    assert rt.main(["--files", "ui/x.ts", "--no-e2e", "--repo", str(repo)]) == 0
    assert rt.main(["--files", "a.py", "--keep", "--repo", str(repo)]) == 0
    kept = Path(capsys.readouterr().out.split("kept      ")[1].strip())
    assert kept.is_dir() and (kept / "a.py").read_text() == "new\n"
    subprocess.run(["git", "worktree", "remove", "--force", str(kept)], cwd=repo, check=True)


def test_pytest_refuses_a_tree_that_imports_aiteams_from_elsewhere(rt, tmp_path):
    tree = tmp_path / "tree"
    (tree / ".venv" / "bin").mkdir(parents=True)
    (tree / ".venv" / "bin" / "python").symlink_to(Path(__import__("sys").executable))
    ok, line = rt.run_pytest(tree)  # no aiteams/ in this tree: the import resolves outside it
    assert not ok and line.startswith("pytest: ABORTED")


def test_daemon_errors_exit_with_a_sentence_not_a_traceback(rt, monkeypatch):
    import io
    import urllib.error
    for k, v in (("AITEAMS_DAEMON_URL", "http://127.0.0.1:1"), ("AITEAMS_PROJECT", "p"), ("AITEAMS_TURN", "1"),
                 ("AITEAMS_TURN_TOKEN", "t")):
        monkeypatch.setenv(k, v)

    def not_found(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"detail": "unknown commit"}'))
    monkeypatch.setattr(rt.urllib.request, "urlopen", not_found)
    with pytest.raises(SystemExit) as e:
        rt.main(["999"])
    assert str(e.value) == "review_tree: commit #999: daemon said 404: unknown commit"

    def down(req, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(rt.urllib.request, "urlopen", down)
    with pytest.raises(SystemExit) as e:
        rt.main(["7"])
    assert str(e.value).startswith("review_tree: daemon unreachable:")


def test_commit_id_needs_a_role_turn(rt, monkeypatch):
    for k in ("AITEAMS_DAEMON_URL", "AITEAMS_PROJECT", "AITEAMS_TURN", "AITEAMS_TURN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as e:
        rt.main(["7"])
    assert "role turn" in str(e.value)


def test_overlap_lines_name_the_other_request_its_feature_and_lead(rt):
    lines = rt.overlap_lines([
        {"commit_id": 81, "feature": "F-23", "lead_name": "Lead 2", "state": "lead_review", "files": ["aiteams/db.py"]},
        {"commit_id": 82, "feature": "F-9", "lead_name": "Lead 1", "state": "master_review",
         "files": ["aiteams/workflow.py", "tests/test_x.py"]},
        {"commit_id": 83, "feature": "F-24", "lead_name": None, "state": "lead_review", "files": ["a.py"]},
        {"commit_id": 84, "feature": None, "lead_name": None, "state": "lead_review", "files": ["b.py"]}])
    assert lines == [
        "overlap: aiteams/db.py also in #81 (F-23, Lead 2, lead_review)",
        "overlap: aiteams/workflow.py, tests/test_x.py also in #82 (F-9, Lead 1, master_review)",
        "overlap: a.py also in #83 (F-24, no Lead yet, lead_review)",
        "overlap: b.py also in #84 (no feature, lead_review)"]
    assert rt.overlap_lines([]) == []


def test_commit_id_form_prints_the_overlap_lines_first(rt, repo, monkeypatch, capsys):
    """The daemon's files answer carries the overlap; the script prints one line each before the test summaries.
    An answer without it (an older daemon) prints none."""
    monkeypatch.setattr(rt, "run_pytest", lambda tree: (True, "pytest: 1 passed"))
    ov = [{"commit_id": 81, "feature": "F-23", "lead_name": "Lead 2", "state": "lead_review", "files": ["a.py"]}]
    monkeypatch.setattr(rt, "group_files", lambda cid: ([cid], ["a.py"], ov))
    assert rt.main(["5", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("review    #5: 1 file(s) on HEAD in")
    assert out[1] == "overlap: a.py also in #81 (F-23, Lead 2, lead_review)" and out[2] == "pytest: 1 passed"
    monkeypatch.setattr(rt, "group_files", lambda cid: ([cid], ["a.py"], []))
    assert rt.main(["5", "--repo", str(repo)]) == 0
    assert "overlap:" not in capsys.readouterr().out


def test_group_files_reads_the_overlap_and_defaults_to_none(rt, monkeypatch):
    import io
    import json
    for k, v in (("AITEAMS_DAEMON_URL", "http://127.0.0.1:1"), ("AITEAMS_PROJECT", "p"), ("AITEAMS_TURN", "1"),
                 ("AITEAMS_TURN_TOKEN", "t")):
        monkeypatch.setenv(k, v)

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    answer = {"ids": [5], "files": ["a.py"], "overlap": [{"commit_id": 8, "files": ["a.py"], "state": "lead_review"}]}
    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda req, timeout: Resp(json.dumps(answer).encode()))
    assert rt.group_files(5) == ([5], ["a.py"], answer["overlap"])
    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda req, timeout: Resp(b'{"ids": [5], "files": ["a.py"]}'))
    assert rt.group_files(5) == ([5], ["a.py"], [])
