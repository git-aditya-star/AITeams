"""F-39 B2, the turn half: a turn whose focus feature has a live worktree gets `--add-dir <tree>` and the AITEAMS_TREE env; every
turn gets AITEAMS_WORKTREES_ROOT; the turn's cwd stays the main folder; a turn with no feature or no tree is today's turn."""
import asyncio
import os

import pytest

from aiteams import branches
from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.claude_proc import TREE_ENV, ClaudeProc, build_cmd, child_env, display_argv
from aiteams.orchestrator.turn import TurnRunner
from aiteams.runtime import AppState, Project
from tests.conftest import FakeHub
from tests.test_claude_proc import ASSISTANT, INIT, RESULT, fake_cmd, fake_env

URL = "http://127.0.0.1:7700"


def env_for(**kw):
    return child_env(URL, "p1", "/tmp/p", "backend", 7, 600, "tok", **kw)


def test_worktrees_root_and_tree_path_agree(tmp_path):
    project = tmp_path / "work" / "AITeams"
    project.mkdir(parents=True)
    root = branches.worktrees_root(str(project))
    assert root == os.path.join(os.path.realpath(tmp_path / "work"), "AITeams-worktrees", "AITeams")
    assert branches.tree_path(str(project), 39) == os.path.join(root, "f39")


def test_child_env_carries_the_tree_and_the_root_and_never_inherits_them(monkeypatch):
    for k in TREE_ENV:
        monkeypatch.setenv(k, "/inherited")
    plain = env_for()
    assert not any(k in plain for k in TREE_ENV)  # a turn on no tree is exactly today's turn, whatever the daemon's env held
    rooted = env_for(worktrees_root="/w/AITeams-worktrees/AITeams")
    assert rooted["AITEAMS_WORKTREES_ROOT"] == "/w/AITeams-worktrees/AITeams" and "AITEAMS_TREE" not in rooted
    treed = env_for(tree="/w/AITeams-worktrees/AITeams/f39", tree_feature="F-39", worktrees_root="/w/AITeams-worktrees/AITeams")
    assert treed["AITEAMS_TREE"] == "/w/AITeams-worktrees/AITeams/f39" and treed["AITEAMS_TREE_FEATURE"] == "F-39"
    assert treed["AITEAMS_WORKTREES_ROOT"] == "/w/AITeams-worktrees/AITeams"
    assert treed["AITEAMS_PROJECT_PATH"] == "/tmp/p"  # the project stays the main folder


def test_build_cmd_adds_the_tree_only_when_the_env_names_one():
    treed = build_cmd({"max_turns": 40}, "backend", env_for(tree="/w/f39", tree_feature="F-39"), "/tmp/sys.md", "s1", False, "opus")
    i = treed.index("--add-dir")
    assert treed[i + 1] == "/w/f39" and treed[i + 2] == "--model"  # its own flag: it ends the --disallowedTools list
    assert treed[i - 1] == "ArtifactData" and treed.count("--add-dir") == 1
    plain = build_cmd({"max_turns": 40}, "backend", env_for(worktrees_root="/w"), "/tmp/sys.md", "s1", False, "opus")
    assert "--add-dir" not in plain
    assert [a for a in treed if a != "--add-dir" and a != "/w/f39"] == plain  # nothing else differs


@pytest.fixture
def setup(db, home, monkeypatch):
    """A project with F-1 (a live tree on disk) and F-2 (no branch), a fake claude, and spies on the env/cmd/cwd it was given."""
    st = AppState(7799)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    seen = {}
    real_env, real_cmd = turn_mod.child_env, turn_mod.build_cmd

    def env_spy(*a, **k):
        seen["env"] = real_env(*a, **k)
        return fake_env([INIT, ASSISTANT, RESULT])

    def cmd_spy(settings, role, env, *rest, **k):
        seen["cmd"] = real_cmd(settings, role, seen["env"], *rest, **k)
        return fake_cmd()

    class SpyProc(ClaudeProc):
        def __init__(self, cmd, cwd, env):
            seen["cwd"] = cwd
            super().__init__(cmd, cwd, env)

    monkeypatch.setattr(turn_mod, "child_env", env_spy)
    monkeypatch.setattr(turn_mod, "build_cmd", cmd_spy)
    monkeypatch.setattr(turn_mod, "ClaudeProc", SpyProc)
    d = proj.db
    f1, f2 = d.create_feature("treed"), d.create_feature("plain")
    tree = os.path.join(branches.worktrees_root(db.project_path), "f1")
    os.makedirs(tree)
    d.set_feature_branch(f1["id"], mode="new", branch="feat/f1-treed", base="main", tree=tree)
    return st, proj, seen, f1, f2, tree


def run_turn(st, proj, conv, author="master", extra=()):
    ids = [proj.db.add_message(c, a, "Message", "hi", [], None)["id"] for c, a in [(conv, author), *extra]]
    wake = proj.db.add_wake("backend", conv, ids, "master")
    for i in ids:
        proj.db.set_receipt(i, "backend", "queued")
    t = asyncio.run(TurnRunner(st, proj, wake).run())
    assert t["state"] == "done", t
    return t


def test_a_turn_on_a_treed_feature_gets_the_tree_in_its_command_and_env_and_keeps_the_main_cwd(setup):
    st, proj, seen, f1, _, tree = setup
    run_turn(st, proj, f1["conversation_id"])
    assert seen["env"]["AITEAMS_TREE"] == tree and seen["env"]["AITEAMS_TREE_FEATURE"] == "F-1"
    assert seen["env"]["AITEAMS_WORKTREES_ROOT"] == branches.worktrees_root(proj.path)
    assert seen["cmd"][seen["cmd"].index("--add-dir") + 1] == tree
    assert seen["cwd"] == proj.path  # on purpose: a changed cwd would lose the resumed session
    shown = display_argv(seen["cmd"])
    assert shown[shown.index("--add-dir") + 1] == tree  # the aiteams_cmd event (display_argv) still shows it


def test_a_turn_with_no_feature_or_a_feature_without_a_tree_is_todays_turn(setup):
    st, proj, seen, f1, f2, tree = setup
    for conv in ("dm-backend", f2["conversation_id"]):
        run_turn(st, proj, conv)
        assert not any(k in seen["env"] for k in ("AITEAMS_TREE", "AITEAMS_TREE_FEATURE"))
        assert "--add-dir" not in seen["cmd"] and seen["cwd"] == proj.path
        assert seen["env"]["AITEAMS_WORKTREES_ROOT"] == branches.worktrees_root(proj.path)  # every turn knows the root


@pytest.mark.parametrize("fields", [
    {"mode": "current", "branch": "main", "tree": None},            # the main folder: nothing to add
    {"tree_state": "removed"},                                      # the Master's PR merged and the tree is gone
    {"tree": "/nowhere/f1"},                                        # the folder vanished
])
def test_no_live_tree_no_add_dir(setup, fields):
    st, proj, seen, f1, _, _ = setup
    proj.db.set_feature_branch(f1["id"], **fields)
    run_turn(st, proj, f1["conversation_id"])
    assert "AITEAMS_TREE" not in seen["env"] and "--add-dir" not in seen["cmd"]


def test_the_focus_is_the_prompts_newest_master_message_conversation(setup):
    st, proj, seen, f1, f2, tree = setup
    # the Master wrote in the treed feature, a role wrote later in the plain one: the prompt focuses on F-1
    run_turn(st, proj, f1["conversation_id"], "master", extra=[(f2["conversation_id"], "lead")])
    assert seen["env"]["AITEAMS_TREE"] == tree
    # the Master's newest message is in the plain feature: no tree, even though the treed feature has messages too
    run_turn(st, proj, f1["conversation_id"], "lead", extra=[(f2["conversation_id"], "master")])
    assert "AITEAMS_TREE" not in seen["env"]


def test_only_a_normal_wake_has_a_tree(setup):
    st, proj, _, f1, _, tree = setup
    mid = proj.db.add_message(f1["conversation_id"], "master", "Message", "hi", [], None)["id"]
    runner = TurnRunner(st, proj, proj.db.add_wake("backend", f1["conversation_id"], [mid], "master"))
    assert runner._tree("normal", [mid]) == ("F-1", tree)
    for kind in ("standup", "standup_compile", "brainstorm", "brainstorm_compile", "handoff"):
        assert runner._tree(kind, [mid]) == (None, None)  # even with a message of the treed feature in hand


def test_tree_for(db):
    f = db.create_feature("x")
    assert branches.tree_for(db, f["id"]) is None and branches.tree_for(db, None) is None  # no branch yet
    real = os.path.join(db.project_path, "t")
    os.makedirs(real)
    db.set_feature_branch(f["id"], mode="new", branch="feat/x", base="main", tree=real)
    assert branches.tree_for(db, f["id"]) == real
    db.set_feature_branch(f["id"], tree_state="kept")
    assert branches.tree_for(db, f["id"]) is None  # only a live tree counts
