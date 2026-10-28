"""F-39 B2: scripts/review_tree.py reviews a request on a feature's worktree on its branch tip, with the files copied from the tree."""
import subprocess

import pytest

from tests.test_review_tree import repo, rt, worktrees  # noqa: F401  (fixtures and a helper)


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def feature_tree(repo, tmp_path):
    """A worktree on feat/x with one commit ahead of main (treeonly.py) and uncommitted edits of a.py and other.py in it."""
    tree = tmp_path / "AITeams-worktrees" / "repo" / "f5"
    git(repo, "worktree", "add", "-q", "--no-track", "-b", "feat/x", str(tree), "HEAD")
    (tree / "treeonly.py").write_text("on the branch\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "branch work")
    (tree / "a.py").write_text("tree version\n")
    (tree / "other.py").write_text("tree WIP, not listed\n")
    return tree


def test_the_clean_tree_is_built_on_the_branch_tip_with_the_files_from_the_feature_tree(rt, repo, feature_tree, monkeypatch, capsys):
    seen = {}

    def fake_pytest(tree):
        seen.update({p: (tree / p).read_text() if (tree / p).exists() else None for p in ("a.py", "treeonly.py", "other.py")})
        seen["venv"] = (tree / ".venv").resolve() == (repo / ".venv").resolve()  # the tooling still comes from the main folder
        return True, "pytest: 1 passed"

    def fake_group(cid):
        rt.WHERE.update({"tree": str(feature_tree), "branch": "feat/x", "mixed": False})
        return [cid], ["a.py"], []
    monkeypatch.setattr(rt, "run_pytest", fake_pytest)
    monkeypatch.setattr(rt, "group_files", fake_group)
    assert rt.main(["5", "--repo", str(repo)]) == 0
    assert seen == {"a.py": "tree version\n", "treeonly.py": "on the branch\n", "other.py": "head\n", "venv": True}
    sha = git(feature_tree, "rev-parse", "--short", "HEAD")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"review    #5: 1 file(s) on feat/x@{sha} in ")
    assert len(worktrees(repo)) == 2  # the clean tree is gone again (main + the feature's tree)
    assert (feature_tree / "a.py").read_text() == "tree version\n" and (repo / "a.py").read_text() == "new\n"  # nothing touched


def test_a_group_across_places_is_taken_from_the_project_folder_and_says_so(rt, repo, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(rt, "run_pytest", lambda tree: (seen.update(a=(tree / "a.py").read_text()) or (True, "pytest: 1 passed")))

    def fake_group(cid):
        rt.WHERE.update({"tree": None, "branch": None, "mixed": True})
        return [cid], ["a.py"], []
    monkeypatch.setattr(rt, "group_files", fake_group)
    assert rt.main(["5", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("review    #5: 1 file(s) on HEAD in") and out[1].startswith("mixed: the group spans features")
    assert seen["a"] == "new\n"


def test_a_tree_that_is_not_on_this_machine_falls_back_with_a_line(rt, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rt, "run_pytest", lambda tree: (True, "pytest: 1 passed"))
    gone = tmp_path / "nowhere" / "f9"

    def fake_group(cid):
        rt.WHERE.update({"tree": str(gone), "branch": "feat/y", "mixed": False})
        return [cid], ["a.py"], []
    monkeypatch.setattr(rt, "group_files", fake_group)
    assert rt.main(["5", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "file(s) on HEAD in" in out and f"mixed: the feature's worktree {gone} is not on this machine" in out


def test_group_files_keeps_where_the_daemon_said_the_files_live(rt, monkeypatch):
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
    answer = {"ids": [5], "files": ["a.py"], "overlap": [], "tree": "/w/f5", "branch": "feat/x", "mixed": False}
    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda req, timeout: Resp(json.dumps(answer).encode()))
    assert rt.group_files(5) == ([5], ["a.py"], []) and rt.WHERE == {"tree": "/w/f5", "branch": "feat/x", "mixed": False}
    monkeypatch.setattr(rt.urllib.request, "urlopen", lambda req, timeout: Resp(b'{"ids": [5], "files": ["a.py"]}'))  # an older daemon
    assert rt.group_files(5) == ([5], ["a.py"], []) and rt.WHERE == {"tree": None, "branch": None, "mixed": None}
