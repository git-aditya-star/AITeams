"""F-39 B2, the hook half: a file in a feature's worktree is locked by its absolute realpath; with the turn's tree set, an edit
of the main folder's code is denied and points at the tree; docs, notes and today's turns (no tree env) are unchanged."""
import os

import pytest

from tests.test_hook import _Stub, denied, run_hook_env, stub  # noqa: F401  (stub is a fixture)


@pytest.fixture
def trees(repo, tmp_path):
    """Two features' worktrees next to the project, like branches.tree_path puts them, each with src/app.py."""
    root = os.path.realpath(tmp_path / "AITeams-worktrees" / repo.name)
    out = {}
    for n in (39, 40):
        d = os.path.join(root, f"f{n}", "src")
        os.makedirs(d)
        open(os.path.join(d, "app.py"), "w").write("x")
        out[n] = os.path.join(root, f"f{n}")
    return root, out


def env_for(stub, root, tree=None, feature="F-39"):
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok",
           "AITEAMS_WORKTREES_ROOT": root}
    if tree:
        env.update(AITEAMS_TREE=tree, AITEAMS_TREE_FEATURE=feature)
    return env


def test_a_worktree_file_is_locked_by_its_absolute_realpath(repo, home, stub, trees):
    root, t = trees
    env = env_for(stub, root, t[39])
    f39, f40 = os.path.join(t[39], "src", "app.py"), os.path.join(t[40], "src", "app.py")
    assert run_hook_env(repo, home, "Edit", {"file_path": f39}, env) is None
    assert _Stub.calls[-1][0] == "/internal/locks/acquire" and _Stub.calls[-1][2] == {"path": f39}
    # the "same" file of another feature's tree is a different key (no collision), and so is a symlinked spelling of the path
    assert run_hook_env(repo, home, "Write", {"file_path": f40}, env) is None and _Stub.calls[-1][2] == {"path": f40}
    link = repo.parent / "alias"
    os.symlink(t[39], link)
    assert run_hook_env(repo, home, "Edit", {"file_path": str(link / "src" / "app.py")}, env) is None
    assert _Stub.calls[-1][2] == {"path": f39}
    # another role holds it: denied, naming the absolute path
    _Stub.response = {"ok": False, "holder": "frontend", "since": 1_700_000_000}
    res = run_hook_env(repo, home, "Edit", {"file_path": f39}, env)
    assert denied(res) and f"{f39} is locked by frontend since" in res["permissionDecisionReason"]


def test_without_a_tree_a_project_file_keeps_its_relative_key(repo, home, stub, trees):
    root, _ = trees
    for env in (env_for(stub, root), env_for(stub, root, None)):
        assert run_hook_env(repo, home, "Edit", {"file_path": str(repo / "src" / "app.py")}, env) is None
        assert _Stub.calls[-1][2] == {"path": "src/app.py"}


def test_with_the_tree_set_a_main_folder_code_edit_is_denied_and_points_at_the_tree(repo, home, stub, trees):
    root, t = trees
    env = env_for(stub, root, t[39])
    n = len(_Stub.calls)
    for tool, p in (("Edit", str(repo / "src" / "app.py")), ("Write", "src/new.py"), ("MultiEdit", str(repo / "README.md")),
                    ("NotebookEdit", str(repo / "n.ipynb"))):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        res = run_hook_env(repo, home, tool, {key: p}, env)
        rel = os.path.relpath(os.path.realpath(p if os.path.isabs(p) else repo / p), os.path.realpath(repo))
        assert denied(res), (tool, p)
        assert res["permissionDecisionReason"].endswith(f"F-39 works in {t[39]}; edit {os.path.join(t[39], rel)} instead.")
    assert len(_Stub.calls) == n  # denied before any lock call


def test_docs_notes_and_the_roles_folder_stay_editable_in_the_main_folder(repo, home, stub, trees):
    root, t = trees
    env = env_for(stub, root, t[39])
    for rel in (".aiteams/features/F-39/design.md", ".aiteams/roles/lead.md"):
        assert run_hook_env(repo, home, "Write", {"file_path": str(repo / rel)}, env) is None, rel
        assert _Stub.calls[-1][2] == {"path": rel}  # locked like before
    n = len(_Stub.calls)
    assert run_hook_env(repo, home, "Write", {"file_path": str(repo / ".project-notes" / "m1a.md")}, env) is None
    assert len(_Stub.calls) == n  # notes are never locked
    # .aiteams outside roles/ and features/ is still denied, by the old rule, not the tree rule
    res = run_hook_env(repo, home, "Write", {"file_path": str(repo / ".aiteams" / "state.db")}, env)
    assert denied(res) and "only allowed in .aiteams/roles/" in res["permissionDecisionReason"]


def test_outside_both_folders_is_still_free(repo, home, stub, trees):
    root, t = trees
    n = len(_Stub.calls)
    assert run_hook_env(repo, home, "Write", {"file_path": "/tmp/anything.txt"}, env_for(stub, root, t[39])) is None
    assert len(_Stub.calls) == n


def test_an_older_turn_without_the_tree_env_is_today_behaviour(repo, home, stub, trees):
    """No AITEAMS_WORKTREES_ROOT / AITEAMS_TREE: a worktree is just another folder outside the project (no lock), and the
    project's own files are locked by their relative path."""
    _, t = trees
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok"}
    n = len(_Stub.calls)
    assert run_hook_env(repo, home, "Edit", {"file_path": os.path.join(t[39], "src", "app.py")}, env) is None
    assert len(_Stub.calls) == n
    assert run_hook_env(repo, home, "Edit", {"file_path": str(repo / "src" / "app.py")}, env) is None
    assert _Stub.calls[-1][2] == {"path": "src/app.py"}
