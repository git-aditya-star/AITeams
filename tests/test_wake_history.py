"""F-42 cut 1 (a resumed session's wake carries only what it hasn't seen), the trim pointer, and the unfiled-edits list that a
fresh session and a handoff turn carry. Prompts are read from the turns a fake claude ran."""
import asyncio
import json
import os
import re
import subprocess
import sys

import pytest

import aiteams.mcp_server as mcp
from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.prompt import LAST_N, TRIM_LINES, build_handoff_prompt, build_wake_prompt
from aiteams.orchestrator.turn import TurnRunner
from aiteams.runtime import AppState, Project
from tests.conftest import FakeHub
from tests.test_claude_proc import ASSISTANT, INIT, RESULT, fake_cmd, fake_env

POINTER = re.compile(r"get_messages\(([^,\s]+), before=(\d+), limit=1\)")


@pytest.fixture
def env(db, home, monkeypatch):
    """A project with a feature F-1 and a chat, a fake claude for every turn, and the spawn flags each turn was given."""
    spawned = []
    real_cmd = turn_mod.build_cmd
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: (spawned.append(real_cmd(*a, **k)), fake_cmd())[1])
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env([INIT, ASSISTANT, RESULT]))
    st = AppState(7799)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    feat = proj.db.create_feature("Export")
    return st, proj, feat["conversation_id"], spawned


def say(db, conv, author, text, mentions=(), type_="Message"):
    return db.add_message(conv, author, type_, text, list(mentions), None)


def wake(st, proj, conv, new, role="backend"):
    """Run one normal turn of `role` whose wake carries the message ids `new`; returns the finished turn row."""
    w = proj.db.add_wake(role, conv, list(new), "master")
    for i in new:
        proj.db.set_receipt(i, role, "queued")
    t = asyncio.run(TurnRunner(st, proj, w).run())
    assert t["state"] == "done" and t["error"] is None, t
    return proj.db.turn(t["id"])


def seen_in(prompt):
    return {int(n) for n in re.findall(r"^\[\d\d:\d\d\] #(\d+) ", prompt, re.M)}


def block(prompt, mid):
    """The prompt lines of message #mid (its header line up to the next message or section)."""
    return re.search(rf"^\[\d\d:\d\d\] #{mid} .*?(?=^\[\d\d:\d\d\] #|^#|\Z)", prompt, re.M | re.S).group(0)


def long_text(n, tag="line"):
    return "\n".join(f"{tag} {i}" for i in range(n))


def test_the_first_wake_in_a_conversation_gets_the_full_last_20_and_records_its_watermark(env):
    st, proj, conv, _ = env
    old = [say(proj.db, conv, "lead", f"old {i}") for i in range(25)]
    new = say(proj.db, conv, "master", "@backend start", ["backend"])
    t = wake(st, proj, conv, [new["id"]])
    assert f"last {LAST_N} messages" in t["prompt"] and "Also in" not in t["prompt"]
    assert "old 24" in t["prompt"] and "old 5" not in t["prompt"]  # the usual 20 newest
    assert t["prompt_conv"] == conv and t["prompt_max_id"] >= new["id"]  # what its prompt showed, for the next wake


def test_a_resumed_wake_has_only_what_came_after_and_the_feature_header(env):
    st, proj, conv, _ = env
    for i in range(10):
        say(proj.db, conv, "lead", f"old {i}")
    first = say(proj.db, conv, "master", "@backend one", ["backend"])
    t1 = wake(st, proj, conv, [first["id"]])
    between = say(proj.db, conv, "frontend", "frontend says hi")
    new = say(proj.db, conv, "lead", "@backend two", ["backend"])
    t2 = wake(st, proj, conv, [new["id"]])
    assert t2["session_id"] == t1["session_id"]
    p = t2["prompt"]
    assert f"last {LAST_N} messages" not in p and "# Also in" in p
    assert seen_in(p) == {between["id"], new["id"]}  # the new one and what came between, nothing the session already has
    assert "old " not in p and f"#{first['id']} " not in p
    assert "# Feature F-1: Export" in p  # the feature header is kept (it changes)
    assert t2["prompt_max_id"] >= new["id"]


def test_a_new_message_is_not_listed_twice(env):
    st, proj, conv, _ = env
    first = say(proj.db, conv, "master", "@backend one", ["backend"])
    wake(st, proj, conv, [first["id"]])
    new = say(proj.db, conv, "lead", "@backend two", ["backend"])
    p = wake(st, proj, conv, [new["id"]])["prompt"]
    assert p.count(f"#{new['id']} lead") == 1 and f"#{first['id']} master" not in p
    nothing = say(proj.db, conv, "lead", "@backend three", ["backend"])
    p = wake(st, proj, conv, [nothing["id"]])["prompt"]
    assert "(nothing else; everything earlier is in your session)" in p


@pytest.mark.parametrize("why", ["fresh", "other_conv", "null", "full", "failed_turn", "other_session"])
def test_every_other_case_gets_the_full_last_20(env, why):
    st, proj, conv, _ = env
    db = proj.db
    if why == "full":
        st.settings["wake_history"] = "full"
    for i in range(6):
        say(db, conv, "lead", f"old {i}")
    other = db.create_feature("Other")["conversation_id"]
    first = say(db, other if why == "other_conv" else conv, "master", "@backend one", ["backend"])
    t1 = wake(st, proj, other if why == "other_conv" else conv, [first["id"]])
    if why == "null":
        db.update_turn(t1["id"], prompt_max_id=None, prompt_conv=None)  # an older row
    if why == "failed_turn":
        db.update_turn(t1["id"], state="failed")  # the first turn never ran its prompt: its watermark isn't trusted
    if why == "other_session":
        db.update_turn(t1["id"], session_id="another-session")
    if why == "fresh":
        db.set_role_state("backend", rotate_reason="context 152k tokens (limit 150k)", rotate_handoff=0)
    new = say(db, conv, "lead", "@backend two", ["backend"])
    t2 = wake(st, proj, conv, [new["id"]])
    assert f"# Conversation: F-1 Export (feature" in t2["prompt"] and f"last {LAST_N} messages" in t2["prompt"], why
    assert "Also in" not in t2["prompt"] and "old 5" in t2["prompt"], why


def test_a_capped_turn_ran_so_its_watermark_counts(env):
    st, proj, conv, _ = env
    first = say(proj.db, conv, "master", "@backend one", ["backend"])
    t1 = wake(st, proj, conv, [first["id"]])
    proj.db.update_turn(t1["id"], error="max turns reached")
    p = wake(st, proj, conv, [say(proj.db, conv, "lead", "@backend two", ["backend"])["id"]])["prompt"]
    assert "# Also in" in p
    proj.db.update_turn(t1["id"], error="no assistant output (num_turns=0); the wake may not have been processed")
    p = wake(st, proj, conv, [say(proj.db, conv, "lead", "@backend three", ["backend"])["id"]])["prompt"]
    assert "# Also in" in p  # the second turn (which ran) is the watermark now
    for t in proj.db.turns("backend"):
        proj.db.update_turn(t["id"], error="no assistant output (num_turns=0); the wake may not have been processed")
    p = wake(st, proj, conv, [say(proj.db, conv, "lead", "@backend four", ["backend"])["id"]])["prompt"]
    assert f"last {LAST_N} messages" in p and "# Also in" not in p  # no turn processed its prompt: the full history


def test_a_watermark_is_per_conversation_and_per_session(env):
    st, proj, conv, _ = env
    db = proj.db
    other = db.create_feature("Other")["conversation_id"]
    a = say(db, conv, "master", "@backend a", ["backend"])
    wake(st, proj, conv, [a["id"]])
    b = say(db, other, "master", "@backend b", ["backend"])
    tb = wake(st, proj, other, [b["id"]])
    assert "# Also in" not in tb["prompt"]  # first wake in `other`, though the session is resumed
    c = say(db, other, "lead", "@backend c", ["backend"])
    assert "# Also in" in wake(st, proj, other, [c["id"]])["prompt"]
    d = say(db, conv, "lead", "@backend d", ["backend"])
    pd = wake(st, proj, conv, [d["id"]])["prompt"]
    assert "# Also in" in pd and f"#{a['id']} " not in pd  # conv's watermark is from its own earlier turn, not other's


def test_a_failed_resume_gets_the_full_history_not_the_trimmed_one(env, monkeypatch):
    st, proj, conv, spawned = env
    db = proj.db
    for i in range(8):
        say(db, conv, "lead", f"old {i}")
    first = say(db, conv, "master", "@backend one", ["backend"])
    wake(st, proj, conv, [first["id"]])
    new = say(db, conv, "lead", "@backend two", ["backend"])
    sends, calls = [], []
    real_proc = turn_mod.ClaudeProc

    class Spy(real_proc):
        def send_user(self, text):
            sends.append(text)
            super().send_user(text)
    monkeypatch.setattr(turn_mod, "ClaudeProc", Spy)
    cannot_resume = [sys.executable, "-B", "-c",
                     "import sys; sys.stdin.readline(); sys.stderr.write('No conversation found with session ID x\\n'); sys.exit(1)"]
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: (calls.append(1), cannot_resume if len(calls) == 1 else fake_cmd())[1])
    t = wake(st, proj, conv, [new["id"]])
    assert len(sends) == 2
    assert "# Also in" in sends[0] and "# Also in" not in sends[1]  # the retry is a fresh session: the whole history
    assert f"last {LAST_N} messages" in sends[1] and "old 7" in sends[1]
    assert db.turn(t["id"])["prompt"] == sends[1] and db.session_info("backend")["reason"] == "resume failed"


# ---- the 15-line trim -----------------------------------------------------------------------------------------------
def test_long_bodies_are_cut_with_the_pointer_but_not_the_roles_mentions_decisions_or_the_masters(env):
    st, proj, conv, _ = env
    db = proj.db
    first = say(db, conv, "master", "@backend one", ["backend"])
    wake(st, proj, conv, [first["id"]])
    big = long_text(TRIM_LINES + 10, "plain")
    plain = say(db, conv, "frontend", big)  # long, to nobody: cut
    short = say(db, conv, "frontend", long_text(TRIM_LINES, "short"))  # exactly at the limit: whole
    to_me = say(db, conv, "lead", long_text(30, "mention"), ["backend"])  # @mentions the role
    decision = say(db, conv, "lead", long_text(30, "decision"), ["backend"], "Decision")
    to_other = say(db, conv, "lead", long_text(30, "other"), ["qa"], "Decision")  # a Decision to someone else: cut
    master = say(db, conv, "master", long_text(30, "master"))  # the Master's words: whole
    new = say(db, conv, "lead", "@backend go", ["backend"])
    p = wake(st, proj, conv, [new["id"]])["prompt"]
    assert "plain 14" in p and "plain 15" not in p
    assert f"… (10 more lines — get_messages({conv}, before={plain['id'] + 1}, limit=1))" in p
    assert "short 14" in p and "more lines" not in block(p, short["id"])  # exactly at the limit: whole
    assert "mention 29" in p and "decision 29" in p and "master 29" in p
    assert "other 14" in p and "other 15" not in p
    assert len(POINTER.findall(p)) == 2  # the plain one and the other Decision


def test_the_full_history_path_never_trims(env):
    st, proj, conv, _ = env
    say(proj.db, conv, "frontend", long_text(40, "plain"))
    new = say(proj.db, conv, "lead", "@backend go", ["backend"])
    p = wake(st, proj, conv, [new["id"]])["prompt"]  # a first wake: today's prompt, whole
    assert "plain 39" in p and "more lines" not in p


def test_following_the_pointer_returns_that_one_message_whole(env, monkeypatch):
    st, proj, conv, _ = env
    db = proj.db
    wake(st, proj, conv, [say(db, conv, "master", "@backend one", ["backend"])["id"]])
    big = say(db, conv, "frontend", long_text(60, "body"))
    say(db, conv, "frontend", "a later message")
    p = wake(st, proj, conv, [say(db, conv, "lead", "@backend go", ["backend"])["id"]])["prompt"]
    (pconv, before), = [(c, b) for c, b in POINTER.findall(p)]
    assert pconv == conv and int(before) == big["id"] + 1
    # call the real tool with exactly the arguments the prompt prints, against the daemon's real route
    from fastapi.testclient import TestClient
    from aiteams.daemon import create_app
    from tests.conftest import AUTH, running_turn
    monkeypatch.setattr(mcp, "http", lambda method, path, body=None, timeout=30.0: [
        {k: m[k] for k in ("id", "author", "type", "text", "mentions", "created_at", "reactions")}
        for m in db.messages(*([pconv, int(before), 1] if "before=" in path else [pconv]), after=0)])
    got = json.loads(mcp.tool_get_messages({"conversation": pconv, "before": int(before), "limit": 1}))
    assert [m["id"] for m in got] == [big["id"]] and got[0]["text"] == big["text"] and got[0]["text"].count("\n") == 59


def test_the_pointer_works_through_the_internal_route(home, repo):
    """The same arguments against the daemon's /internal/messages (a real turn token), end to end."""
    from fastapi.testclient import TestClient
    from aiteams.daemon import create_app
    from tests.conftest import AUTH, running_turn
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH) as c:
        pid = c.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
        c.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
        proj = c.app.state.app.projects[pid]
        big = say(proj.db, "feature-1", "frontend", long_text(60, "body"))
        say(proj.db, "feature-1", "frontend", "later")
        h = running_turn(proj.db, "backend", pid=pid)[1]
        msg = proj.db.message(big["id"])
        before = big["id"] + 1
        r = c.get("/internal/messages", params={"conversation": "feature-1", "before": before, "limit": 1}, headers=h).json()
        assert [m["id"] for m in r] == [big["id"]] and r[0]["text"] == msg["text"]


# ---- unfiled edits --------------------------------------------------------------------------------------------------
def git(repo, *args):
    r = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def dirty(env, repo):
    """backend edited a tracked file (modified), a new file, and a file it then committed; docs are gitignored."""
    st, proj, conv, _ = env
    (repo / ".gitignore").write_text(".aiteams/\n")
    (repo / "tracked.py").write_text("v1\n")
    (repo / "clean.py").write_text("c\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    (repo / "tracked.py").write_text("v2\n")  # modified
    (repo / "new.py").write_text("n\n")  # untracked
    docs = repo / ".aiteams" / "features" / "F-1"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "design.md").write_text("d")  # edited but gitignored: never listed
    for p in ("tracked.py", "new.py", "clean.py", ".aiteams/features/F-1/design.md"):
        proj.db.touch_edit(p, "backend")
    proj.db.touch_edit("other.py", "frontend")  # somebody else's
    (repo / "other.py").write_text("o")
    return st, proj, conv, repo


def test_the_handoff_prompt_lists_the_roles_dirty_unfiled_files(dirty):
    st, proj, conv, repo = dirty
    p = build_handoff_prompt(proj.db, "backend", "context 152k tokens (limit 150k)")
    assert "You have unfiled edits" in p and "- tracked.py" in p and "- new.py" in p
    assert "clean.py" not in p and "design.md" not in p and "other.py" not in p  # clean, gitignored, someone else's
    assert "Call `write_handoff(text)` ONCE" in p
    assert "unfiled" not in build_handoff_prompt(proj.db, "qa", "x")  # a role with none gets today's prompt


def test_a_file_in_an_open_commit_request_is_not_listed(dirty):
    st, proj, conv, repo = dirty
    c = proj.db.create_commit(None, "backend", None, ["new.py"], "feat: x", {}, "lead_review", [])
    p = build_handoff_prompt(proj.db, "backend", "x")
    assert "- tracked.py" in p and "new.py" not in p
    proj.db.update_commit(c["id"], state="master_review")
    assert "new.py" not in build_handoff_prompt(proj.db, "backend", "x")
    proj.db.update_commit(c["id"], state="withdrawn")  # no longer open: listed again
    assert "- new.py" in build_handoff_prompt(proj.db, "backend", "x")
    proj.db.update_commit(c["id"], state="committed")
    assert "- new.py" in build_handoff_prompt(proj.db, "backend", "x")


def test_the_fresh_first_wake_lists_them_with_the_handoff_and_the_last_20(dirty):
    st, proj, conv, repo = dirty
    db = proj.db
    for i in range(5):
        say(db, conv, "lead", f"history {i}")
    first = say(db, conv, "master", "@backend one", ["backend"])
    wake(st, proj, conv, [first["id"]])
    hpath = repo / ".aiteams" / "handoffs" / "backend" / "h1.md"
    hpath.parent.mkdir(parents=True, exist_ok=True)
    hpath.write_text("I was on the export button.\n")
    hid = say(db, "dm-backend", "backend", "handoff written")["id"]
    db.add_handoff("backend", db.active_session("backend"), 1, str(hpath), hid, "context 152k tokens (limit 150k)", 1.0)
    db.set_role_state("backend", rotate_reason="context 152k tokens (limit 150k)", rotate_handoff=0)
    new = say(db, conv, "lead", "@backend two", ["backend"])
    t = wake(st, proj, conv, [new["id"]])
    p = t["prompt"]
    assert "# Fresh session #2 — handoff from your previous session" in p and "I was on the export button." in p
    assert "# You have unfiled edits" in p and "- tracked.py" in p and "- new.py" in p and "clean.py" not in p
    assert f"last {LAST_N} messages" in p and "history 0" in p and "# Also in" not in p  # the last 20, as today


def test_a_first_turn_ever_has_no_unfiled_section_and_a_resumed_wake_none_either(dirty):
    st, proj, conv, repo = dirty
    first = say(proj.db, conv, "master", "@backend one", ["backend"])
    p1 = wake(st, proj, conv, [first["id"]])["prompt"]
    p2 = wake(st, proj, conv, [say(proj.db, conv, "lead", "@backend two", ["backend"])["id"]])["prompt"]
    assert "unfiled edits" not in p1 and "unfiled edits" not in p2


def test_the_list_is_capped_and_a_worktree_file_is_listed_by_its_absolute_path(dirty, tmp_path):
    st, proj, conv, repo = dirty
    from aiteams import branches
    db = proj.db
    for i in range(20):
        (repo / f"bulk{i}.py").write_text("x")
        db.touch_edit(f"bulk{i}.py", "backend")
    tree = branches.tree_path(str(repo), 1)
    git(repo, "worktree", "add", "-q", "--no-track", "-b", "feat/x", tree, "HEAD")
    wt_file = os.path.join(os.path.realpath(tree), "in_tree.py")
    open(wt_file, "w").write("t")
    db.touch_edit(wt_file, "backend")
    p = build_handoff_prompt(db, "backend", "x")
    section = p.split("You have unfiled edits")[1].split("\n\n")[0].splitlines()[1:]  # the bullets after the lead-in line
    assert f"- {wt_file}" in section and section[-1] == "- … and 8 more" and len(section) == 15 + 1  # 15 shown + the rest
    db.set_feature_branch(1, mode="new", branch="feat/x", base="main", tree=tree)
    db.create_commit(1, "backend", None, ["in_tree.py"], "feat: t", {}, "lead_review", [])  # relative to the feature's tree
    assert wt_file not in build_handoff_prompt(db, "backend", "x")
