"""F-41: a role's work runs in lanes: "" is its main session, a feature key ("F-1") its own session for a feature with its own
worktree. Routing, the scheduler key and caps, sessions / rotation / handoffs / locks per lane, retire on done, interrupt by
lane, team_status.lanes and messages.lane."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteams import branches, config, lanes, workflow
from aiteams.daemon import create_app
from aiteams.orchestrator import lifecycle
from aiteams.orchestrator import scheduler as sched_mod
from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.router import route
from aiteams.orchestrator.turn import TurnRunner
from aiteams.runtime import AppState, Project
from tests.conftest import AUTH, FakeHub
from tests.test_claude_proc import ASSISTANT, INIT, RESULT, fake_cmd, fake_env
from tests.test_scheduler import StubRunner

ROOT_DIR = Path(__file__).resolve().parent.parent
BIG = {"modelUsage": {"m": {"contextWindow": 1000}}}


def treed(db, title="x", **fields):
    """A feature with its own live worktree on disk (a Branch card in `new` mode, approved)."""
    f = db.create_feature(title)
    tree = os.path.join(branches.worktrees_root(db.project_path), f"f{f['id']}")
    os.makedirs(tree, exist_ok=True)
    db.set_feature_branch(f["id"], **{"mode": "new", "branch": f"feat/f{f['id']}", "base": "main", "tree": tree, **fields})
    return f


def project(db, **settings):
    st = AppState(7799)
    st.settings.update(settings)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    return st, proj


def say(db, conv, role, text="@{r} go", author="master"):
    return db.add_message(conv, author, "Message", text.format(r=role), [role], None)


def route_to(db, conv, role, author="master"):
    m = say(db, conv, role, author=author)
    return route(db, m, db.conversation(conv))[0]


def lane_turn(db, role, lane, state="done", ctx=None, conv=None, result=None, error=None, kind="normal"):
    sid = db.active_session(role, lane) or db.new_session(role, f"s-{role}-{lane or 'main'}", "first turn", lane)
    t = db.create_turn(role, sid, "master", conv or "dm-" + role, kind, token=f"tok-{time.time_ns()}", lane=lane)
    return db.update_turn(t["id"], state=state, started_at=1.0, ended_at=2.0, context_tokens=ctx,
                          result_json=json.dumps(result) if result else None, error=error)


class Finished:
    """A TurnRunner whose turn ended; after_turn reads the turn row (and its lane) from the DB."""
    def __init__(self, proj, turn):
        self.project, self.turn, self.role = proj, turn, turn["role"]
        self.interrupt_reason = self.interrupt_note = None
        self.kind, self.model = turn.get("kind") or "normal", "opus"


# ---- the lane rule ---------------------------------------------------------------------------------------------------
def test_lane_for_is_a_feature_with_its_own_live_worktree_and_nothing_else(db):
    own = treed(db, "own")
    assert lanes.lane_for(db, own["conversation_id"]) == own["key"]
    plain = db.create_feature("no branch card")
    cur = db.create_feature("current")
    db.set_feature_branch(cur["id"], mode="current", branch="main", tree=None)
    shared = db.create_feature("shared")
    db.set_feature_branch(shared["id"], mode="shared", branch=own["key"], tree=db.feature_branch(own["id"])["tree"],
                          shared_from=own["id"])
    gone = treed(db, "vanished")
    os.rmdir(db.feature_branch(gone["id"])["tree"])
    removed = treed(db, "removed", tree_state="removed")
    done = treed(db, "done")
    db.update_feature(done["id"], phase="done")
    arch = treed(db, "archived")
    db.update_feature(arch["id"], archived=True)
    for f in (plain, cur, shared, gone, removed, done, arch):
        assert lanes.lane_for(db, f["conversation_id"]) == "", f["title"]
    for conv in ("general", "dm-backend", "standup", "nope", None):
        assert lanes.lane_for(db, conv) == ""
    assert lanes.label("backend", "") == "backend" and lanes.label("backend", "F-3") == "backend · F-3"


def test_a_mention_in_a_treed_feature_wakes_the_lane_and_never_merges_into_the_main_wake(db):
    a, b = treed(db, "a"), treed(db, "b")
    main = route_to(db, "general", "frontend")
    wa = route_to(db, a["conversation_id"], "frontend")
    wb = route_to(db, b["conversation_id"], "frontend")
    assert (main["lane"], wa["lane"], wb["lane"]) == ("", a["key"], b["key"])
    assert len({main["id"], wa["id"], wb["id"]}) == 3
    again = route_to(db, a["conversation_id"], "frontend")  # same lane: merged into its queued wake
    assert again["id"] == wa["id"] and len(again["message_ids"]) == 2
    assert db.queued_wake("frontend")["id"] == main["id"]  # the main lane's wake is its own
    assert db.queued_wake("frontend", lane=a["key"])["id"] == wa["id"]
    assert db.queued_wake("frontend", lane=None)["id"] == main["id"]  # None = any lane, the oldest
    plain = db.create_feature("plain")  # a feature without its own tree stays on the main lane (and merges there)
    assert route_to(db, plain["conversation_id"], "frontend")["id"] == main["id"]


def test_a_system_wake_runs_in_the_conversations_lane_or_the_one_named(db, home):
    st, proj = project(db)
    a = treed(db, "a")
    asyncio.run(lifecycle.system_wake(st, proj, "backend", a["conversation_id"], "nudge"))
    assert db.queued_wake("backend", lane=a["key"]) and db.queued_wake("backend") is None
    asyncio.run(lifecycle.system_wake(st, proj, "qa", "dm-qa", "continue", lane=a["key"]))  # a DM, but the turn's own lane
    assert db.queued_wake("qa", lane=a["key"])["conversation_id"] == "dm-qa" and db.queued_wake("qa") is None


# ---- the scheduler -----------------------------------------------------------------------------------------------------
def sched(db, monkeypatch, **settings):
    monkeypatch.setattr(sched_mod, "TurnRunner", StubRunner)
    st, proj = project(db, **settings)
    s = sched_mod.Scheduler(st)
    st.scheduler = s
    return st, proj, s


def finish(s):
    asyncio.run(asyncio.gather(*[r.task for r in s.running.values()]))


def run_tick(s):
    async def go():
        s._tick()
        started = set(s.running)
        await asyncio.gather(*[r.task for r in s.running.values()])
        return started
    return asyncio.run(go())


def test_two_lanes_of_a_role_start_together_and_a_second_wake_waits_for_its_own_lane(db, home, monkeypatch):
    st, proj, s = sched(db, monkeypatch, max_concurrent=6, max_lanes_per_role=3)
    a, b = treed(db, "a"), treed(db, "b")
    for conv in (a["conversation_id"], b["conversation_id"], "general"):
        route_to(db, conv, "frontend")

    async def go():
        s._tick()
        first = set(s.running)
        route_to(db, a["conversation_id"], "frontend")  # lane A is running: this wake queues behind it
        s._tick()
        assert set(s.running) == first
        await asyncio.gather(*[r.task for r in list(s.running.values())])
        s._tick()
        assert set(s.running) == {("p1", "frontend", a["key"])}  # lane A's turn ended, so its queued wake runs now
        await asyncio.gather(*[r.task for r in list(s.running.values())])
        return first
    assert asyncio.run(go()) == {("p1", "frontend", a["key"]), ("p1", "frontend", b["key"]), ("p1", "frontend", "")}


def test_a_role_runs_at_most_max_lanes_per_role_lanes_at_once(db, home, monkeypatch):
    st, proj, s = sched(db, monkeypatch, max_concurrent=6)  # max_lanes_per_role defaults to 2
    assert config.DEFAULT_SETTINGS["max_lanes_per_role"] == 2 and config.DEFAULT_SETTINGS["max_concurrent"] == 6
    a, b = treed(db, "a"), treed(db, "b")
    for conv in ("general", a["conversation_id"], b["conversation_id"]):
        route_to(db, conv, "frontend")
    route_to(db, "general", "backend")
    started = run_tick(s)
    frontend = {k for k in started if k[1] == "frontend"}
    assert len(frontend) == 2 and ("p1", "backend", "") in started  # a third frontend lane waits; others are not held up
    assert len([w for w in db.queued_wakes() if w["role"] == "frontend"]) == 1


def test_the_total_cap_holds_a_seventh_turn_back(db, home, monkeypatch):
    st, proj, s = sched(db, monkeypatch)  # max_concurrent 6
    st.settings["max_concurrent"] = 6
    for role in config.ROLES:  # seven roles, one wake each
        route_to(db, "general", role, author="master")
    assert len(config.ROLES) == 7
    started = run_tick(s)
    assert len(started) == 6 and len([w for w in db.queued_wakes() if w["kind"] == "normal"]) == 1


def test_a_pending_handoff_goes_first_in_its_own_lane_only(db, home, monkeypatch):
    st, proj, s = sched(db, monkeypatch, max_concurrent=6)
    a = treed(db, "a")
    route_to(db, a["conversation_id"], "backend")
    db.add_wake("backend", "dm-backend", [], "system", kind="handoff", lane=a["key"])
    route_to(db, "general", "backend")  # the main lane has no handoff: its normal wake isn't held back by lane A's
    picked = []
    real = sched_mod.TurnRunner

    class Spy(real):
        def __init__(self, st_, proj_, wake):
            picked.append((wake["lane"], wake["kind"]))
            super().__init__(st_, proj_, wake)
    monkeypatch.setattr(sched_mod, "TurnRunner", Spy)
    run_tick(s)
    assert sorted(picked) == [("", "normal"), (a["key"], "handoff")]


def test_stop_acts_on_every_running_lane_of_the_role_or_just_one(db, home, monkeypatch):
    st, proj, s = sched(db, monkeypatch)
    hits = []

    class R:
        def __init__(self, key):
            self.key = key

        def interrupt(self, reason, note=None):
            hits.append((self.key, reason, note))
    for k in (("p1", "backend", ""), ("p1", "backend", "F-1"), ("p1", "frontend", ""), ("other", "backend", "")):
        s.running[k] = R(k)
    assert s.stop("p1", "backend", "master") is True
    assert sorted(h[0] for h in hits) == [("p1", "backend", ""), ("p1", "backend", "F-1")]
    hits.clear()
    assert s.stop("p1", "backend", "manager", "why", lane="F-1") is True and hits == [(("p1", "backend", "F-1"), "manager", "why")]
    hits.clear()
    assert s.stop("p1", "backend", "master", lane="") is True and [h[0] for h in hits] == [("p1", "backend", "")]
    assert s.stop("p1", "backend", "master", lane="F-9") is False and s.stop("p1", "qa", "master") is False


def test_the_locks_of_a_lane_end_with_its_turn_and_nobody_elses(db, home, monkeypatch):
    st, proj, s = sched(db, monkeypatch, max_concurrent=6)
    a = treed(db, "a")
    db.acquire_lock("x.py", "backend", 1, a["key"])
    db.acquire_lock("y.py", "backend", 2, "")
    route_to(db, a["conversation_id"], "backend")
    run_tick(s)  # the lane's stub turn ends: its (backend, F-1) locks go
    assert [l["path"] for l in db.locks()] == ["y.py"]


# ---- a real turn in a lane ---------------------------------------------------------------------------------------------
@pytest.fixture
def real(db, home, monkeypatch):
    st, proj = project(db)
    seen = {}
    real_env = turn_mod.child_env

    def env_spy(*a, **k):
        seen["env"] = real_env(*a, **k)
        return fake_env([INIT, ASSISTANT, RESULT])
    monkeypatch.setattr(turn_mod, "child_env", env_spy)
    monkeypatch.setattr(turn_mod, "build_cmd", lambda settings, role, env, sys_file, *rest, **k: seen.update(sys=sys_file) or fake_cmd())
    return st, proj, seen


def run_wake(st, proj, wake):
    t = asyncio.run(TurnRunner(st, proj, wake).run())
    assert t["state"] == "done", t
    return t


def test_a_lane_turn_has_its_own_session_env_and_system_prompt(real, db):
    st, proj, seen = real
    a = treed(db, "a")
    t = run_wake(st, proj, route_to(db, a["conversation_id"], "backend"))
    assert t["lane"] == a["key"] and seen["env"]["AITEAMS_LANE"] == a["key"]
    assert seen["env"]["AITEAMS_TREE"] == db.feature_branch(a["id"])["tree"]  # the tree and the lane agree
    assert db.active_session("backend", a["key"]) == t["session_id"] and db.active_session("backend") is None
    assert seen["sys"].endswith(f"backend.{a['key']}.system.md")
    text = open(seen["sys"]).read()
    assert f"in your session for {a['key']}" in text and f"Work only on {a['key']}" in text
    t2 = run_wake(st, proj, route_to(db, "general", "backend"))  # the main lane: no lane env, its own prompt file, its own session
    assert t2["lane"] == "" and "AITEAMS_LANE" not in seen["env"] and seen["sys"].endswith("backend.system.md")
    assert "in your session for" not in open(seen["sys"]).read()
    assert db.active_session("backend") == t2["session_id"] != t["session_id"]
    t3 = run_wake(st, proj, route_to(db, a["conversation_id"], "backend"))  # lane A resumes ITS session
    assert t3["session_id"] == t["session_id"]


def handoff_file(db, role, lane, text, reason="own initiative"):
    path = os.path.join(db.project_path, f"hand-{role}-{lane or 'main'}.md")
    with open(path, "w") as f:
        f.write(text)
    return db.add_handoff(role, "old", 1, path, None, reason, None, lane)


def test_a_lanes_first_session_reads_the_main_handoff_as_background_once(real, db):
    st, proj, seen = real
    handoff_file(db, "backend", "", "MAIN NOTES: the migration is 16")
    a = treed(db, "a")
    t = run_wake(st, proj, route_to(db, a["conversation_id"], "backend"))
    prompt = db.turn(t["id"])["prompt"]
    assert f"# Fresh session for {a['key']} — background: your main session's latest handoff (read-only" in prompt
    assert "MAIN NOTES: the migration is 16" in prompt
    t2 = run_wake(st, proj, route_to(db, a["conversation_id"], "backend"))  # resumed: nothing re-read
    assert "MAIN NOTES" not in db.turn(t2["id"])["prompt"] and "background" not in db.turn(t2["id"])["prompt"]
    t3 = run_wake(st, proj, route_to(db, "general", "backend"))  # the main lane is not a "fresh lane": no background block
    assert "MAIN NOTES" not in db.turn(t3["id"])["prompt"]


def test_rotation_and_the_handoff_are_per_lane(real, db):
    st, proj, seen = real
    a, b = treed(db, "a"), treed(db, "b")
    db.new_session("backend", "main-s", "first turn")
    db.new_session("backend", "lane-a", "first turn", a["key"])
    lane_turn(db, "backend", a["key"], ctx=100)  # the session's first turn never rotates
    t = lane_turn(db, "backend", a["key"], ctx=900, result=BIG)  # 900 tokens of a 1000 window: past 60%
    asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, t)))
    ra = db.role_state("backend", a["key"])
    assert ra["rotate_reason"].startswith("context 900 tokens") and ra["rotate_handoff"] == 1
    assert db.role_state("backend")["rotate_reason"] is None and db.role_state("backend", b["key"])["rotate_reason"] is None
    w = db.queued_wake("backend", "handoff", a["key"])
    assert w and w["lane"] == a["key"] and db.queued_wake("backend", "handoff") is None  # only lane A's session is retired
    # the handoff turn runs in lane A, is told so, and write_handoff lands on lane A
    ht = run_wake(st, proj, w)
    assert ht["lane"] == a["key"] and f"your session for {a['key']}" in db.turn(ht["id"])["prompt"]
    asyncio.run(workflow.write_handoff(st, proj, "backend", ht, "LANE A STATE"))
    mh = asyncio.run(workflow.write_handoff(st, proj, "backend", lane_turn(db, "backend", "", state="running"), "MAIN STATE"))
    ha, hm = db.latest_handoff("backend", a["key"]), db.latest_handoff("backend")
    assert ha["lane"] == a["key"] and os.path.basename(ha["path"]) == f"{a['key']}-1.md" and ha["seq"] == 1
    assert hm["lane"] == "" and os.path.basename(hm["path"]) == "1.md" and mh["path"].endswith(os.path.join("backend", "1.md"))
    assert db.latest_handoff("backend", b["key"]) is None
    assert [h["lane"] for h in db.handoffs("backend")] == ["", a["key"]] and [h["lane"] for h in db.handoffs("backend", lane=a["key"])] == [a["key"]]
    # the next lane-A turn starts a FRESH lane-A session with lane A's own handoff, and the main session is untouched
    asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, db.turn(ht["id"]))))
    assert db.role_state("backend", a["key"])["rotate_handoff"] == 0
    t2 = run_wake(st, proj, route_to(db, a["conversation_id"], "backend"))
    assert t2["session_id"] != "lane-a" and db.session_info("backend", a["key"])["seq"] == 2
    p2 = db.turn(t2["id"])["prompt"]
    assert "handoff from your previous session" in p2 and "LANE A STATE" in p2 and "MAIN STATE" not in p2
    assert db.active_session("backend") == "main-s" and db.role_state("backend", a["key"])["rotate_reason"] is None


def test_a_lanes_first_turn_never_rotates_and_a_failed_streak_counts_per_lane(db, home):
    st, proj = project(db, fail_streak_rotate=2)
    a = "F-1"
    db.new_session("backend", "lane-a", "first turn", a)
    t = lane_turn(db, "backend", a, ctx=900, result=BIG)
    asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, t)))
    assert db.role_state("backend", a)["rotate_reason"] is None
    skipped = [x for x in db.activity() if x["kind"] == "rotation_skipped"]
    assert skipped and "backend · F-1" in skipped[0]["text"]
    for _ in range(2):  # two failed lane turns in a row rotate the lane; a failed MAIN turn between them doesn't reset it
        asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, lane_turn(db, "backend", a, state="failed"))))
        lane_turn(db, "backend", "", state="done")
    assert db.role_state("backend", a)["rotate_reason"] == "errors" and db.role_state("backend")["fail_streak"] == 0


def test_the_capped_turn_continues_in_its_own_lane(db, home):
    st, proj = project(db)
    a = treed(db, "a")
    db.new_session("backend", "lane-a", "first turn", a["key"])
    t = lane_turn(db, "backend", a["key"], error=config.MAX_TURNS_ERROR, conv="dm-backend")
    asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, t)))
    w = db.queued_wake("backend", lane=a["key"])
    assert w and w["conversation_id"] == "dm-backend" and db.queued_wake("backend") is None
    assert db.cap_streak("backend", a["key"]) == 1 and db.cap_streak("backend", "") == 0
    lane_turn(db, "backend", "", state="done")  # a main-lane turn between two capped lane turns doesn't end the lane's streak
    lane_turn(db, "backend", a["key"], error=config.MAX_TURNS_ERROR)
    assert db.cap_streak("backend", a["key"]) == 2 and db.cap_streak("backend") == 1  # unfiltered, the main turn ends it


# ---- locks -------------------------------------------------------------------------------------------------------------
def test_the_lock_holder_is_a_role_and_a_lane(db):
    assert db.acquire_lock("a.py", "backend", 1, "F-1") is None
    assert db.acquire_lock("a.py", "backend", 2, "F-1") is None  # the same holder again
    for lane in ("F-2", ""):
        holder = db.acquire_lock("a.py", "backend", 3, lane)
        assert holder["role"] == "backend" and holder["lane"] == "F-1"
    assert db.acquire_lock("b.py", "backend", 4, "F-2") is None
    assert db.release_locks("backend", "F-1") == ["a.py"] and [l["path"] for l in db.locks()] == ["b.py"]
    assert db.release_locks("backend") == []


@pytest.fixture
def client(home):
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def api_turn(client, pid, role, lane, conv=None):
    proj = client.app.state.app.projects[pid]
    t = lane_turn(proj.db, role, lane, state="running", conv=conv)
    return proj, t, {"X-AITeams-Project": pid, "X-AITeams-Turn": str(t["id"]), "X-AITeams-Token": t["token"]}


def test_two_lanes_of_one_role_conflict_on_a_file_and_the_holder_is_named_with_its_lane(client, pid):
    proj, _, f1 = api_turn(client, pid, "backend", "F-1")
    _, _, f2 = api_turn(client, pid, "backend", "F-2")
    _, _, main = api_turn(client, pid, "backend", "")
    assert client.post("/internal/locks/acquire", json={"path": "docs/x.md"}, headers=f1).json()["ok"] is True
    assert client.post("/internal/locks/acquire", json={"path": "docs/x.md"}, headers=f1).json()["ok"] is True
    r = client.post("/internal/locks/acquire", json={"path": "docs/x.md"}, headers=f2).json()
    assert r["ok"] is False and r["holder"] == "backend · F-1" and r["holder_role"] == "backend" and r["holder_lane"] == "F-1"
    r = client.post("/internal/locks/acquire", json={"path": "docs/x.md"}, headers=main).json()
    assert r["ok"] is False and r["holder"] == "backend · F-1"
    # a role's own lane may release it; another lane of the same role may not (it isn't the holder)
    assert client.post("/internal/locks/release", json={"path": "docs/x.md"}, headers=f2).status_code == 403
    assert client.post("/internal/locks/release", json={"path": "docs/x.md"}, headers=f1).json()["released"] is True
    assert client.get(f"/api/p/{pid}/locks").json() == []
    client.post("/internal/locks/acquire", json={"path": "docs/y.md"}, headers=f1)
    assert any("(backend · F-1)" in line for line in workflow.feature_header(proj.db, proj.path, proj.db.create_feature("h")).splitlines())


def test_a_commit_request_is_refused_on_a_file_another_lane_of_the_role_holds(client, pid, repo):
    proj, _, f1 = api_turn(client, pid, "backend", "F-1")
    _, t2, f2 = api_turn(client, pid, "backend", "F-2")
    (repo / "mine.py").write_text("x = 1\n")
    client.post("/internal/locks/acquire", json={"path": "mine.py"}, headers=f1)
    r = client.post("/internal/commits", json={"files": ["mine.py"], "message": "feat: x\n\nbody"}, headers=f2)
    assert r.status_code == 409 and "locked by backend · F-1" in r.json()["detail"]
    ok = client.post("/internal/commits", json={"files": ["mine.py"], "message": "feat: x\n\nbody"}, headers=f1)
    assert ok.status_code == 200


# ---- retire on done ------------------------------------------------------------------------------------------------
def test_a_done_feature_retires_its_lanes_and_moves_the_queued_work_to_main(db):
    a, b = treed(db, "a"), treed(db, "b")
    for role in ("backend", "frontend", "qa"):
        db.new_session(role, f"{role}-main", "first turn")
        db.new_session(role, f"{role}-a", "first turn", a["key"])
    db.new_session("backend", "backend-b", "first turn", b["key"])
    db.set_role_state("backend", a["key"], fail_streak=1)
    wb_lane = route_to(db, a["conversation_id"], "backend")  # lane A has queued work; main already has a wake: merged
    wb_main = route_to(db, "general", "backend")
    wf_lane = route_to(db, a["conversation_id"], "frontend")  # no main wake yet: re-keyed
    h = db.add_wake("qa", "dm-qa", [], "system", kind="handoff", lane=a["key"])  # a handoff of a retired session is moot
    wb_other = route_to(db, b["conversation_id"], "backend")
    db.update_feature(a["id"], phase="done")
    assert db.active_session("backend", a["key"]) is None and db.active_session("frontend", a["key"]) is None
    assert db.active_session("backend", b["key"]) == "backend-b" and db.active_session("backend") == "backend-main"
    assert db.role_state("backend", a["key"])["fail_streak"] == 0
    merged = db.wake(wb_main["id"])
    assert merged["state"] == "queued" and merged["lane"] == "" and set(merged["message_ids"]) == set(wb_lane["message_ids"]) | set(wb_main["message_ids"])
    assert db.wake(wb_lane["id"])["state"] == "done"
    moved = db.wake(wf_lane["id"])
    assert moved["state"] == "queued" and moved["lane"] == "" and moved["message_ids"] == wf_lane["message_ids"]
    assert db.wake(h["id"])["state"] == "done"
    assert db.wake(wb_other["id"])["lane"] == b["key"]  # another feature's lane is untouched
    assert db.active_lanes() == [("backend", b["key"])]
    assert lanes.lane_for(db, a["conversation_id"]) == ""  # a later message in the done feature goes to the main lane
    assert route_to(db, a["conversation_id"], "backend")["lane"] == ""
    db.update_feature(b["id"], archived=True)  # archiving retires too
    assert db.active_lanes() == [] and db.wake(wb_other["id"])["state"] == "done"  # merged into the main wake queued there
    assert set(wb_other["message_ids"]) <= set(db.wake(wb_main["id"])["message_ids"])


def test_a_turn_still_running_when_its_feature_finishes_ends_normally_without_rotating(db, home):
    st, proj = project(db, rotate_pct=0.5)
    a = treed(db, "a")
    db.new_session("backend", "lane-a", "first turn", a["key"])
    lane_turn(db, "backend", a["key"], ctx=100)
    t = lane_turn(db, "backend", a["key"], ctx=900, result=BIG)
    db.update_feature(a["id"], phase="done")  # retired while the turn ran
    asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, t)))
    assert db.role_state("backend", a["key"])["rotate_reason"] is None and db.queued_wakes() == []
    st.settings["fail_streak_rotate"] = 1  # nor does a failed turn of the retired lane start a rotation for it
    failed = db.create_turn("backend", "lane-a", "master", "dm-backend", lane=a["key"])  # on the retired session
    failed = db.update_turn(failed["id"], state="failed", started_at=1.0, ended_at=2.0)
    asyncio.run(lifecycle.after_turn(st, proj, Finished(proj, failed)))
    assert db.role_state("backend", a["key"])["rotate_reason"] is None and db.queued_wakes() == []
    assert db.q("SELECT 1 FROM role_state WHERE lane=?", a["key"]) == []


# ---- team status, messages ---------------------------------------------------------------------------------------------
def test_messages_carry_the_authors_lane_and_nobody_elses(db):
    a = treed(db, "a")
    db.new_session("backend", "lane-a", "first turn", a["key"])
    t = lane_turn(db, "backend", a["key"], state="running")
    m = db.add_message(a["conversation_id"], "backend", "Message", "from the lane", [], t["id"])
    assert m["lane"] == a["key"] and db.messages(a["conversation_id"])[-1]["lane"] == a["key"]
    assert db.add_message("general", "backend", "Message", "main post", [], None)["lane"] == ""
    assert db.add_message(a["conversation_id"], "system", "System", "x", [], t["id"])["lane"] == ""  # not the turn's author
    assert db.add_message(a["conversation_id"], "master", "Message", "hi", [], None)["lane"] == ""
    assert db.add_message("general", "frontend", "Message", "other role", [], t["id"])["lane"] == ""
    assert db.add_message("general", "backend", "Message", "explicit", [], None, lane="F-7")["lane"] == "F-7"


def test_team_status_lists_each_active_feature_lane_with_its_own_meter(db, home):
    st, proj = project(db, rotate_pct=0.6, rotate_tokens=0)
    s = sched_mod.Scheduler(st)
    st.scheduler = s
    a, b = treed(db, "a"), treed(db, "b")
    db.new_session("backend", "main-s", "first turn")
    lane_turn(db, "backend", "", ctx=300, result=BIG)
    db.new_session("backend", "lane-a", "first turn", a["key"])
    ta = db.update_turn(lane_turn(db, "backend", a["key"], ctx=240, result=BIG)["id"], started_at=time.time())
    db.new_session("backend", "lane-b", "first turn", b["key"])
    lane_turn(db, "backend", b["key"], ctx=60, result=BIG)
    db.new_session("backend", "gone", "first turn", "F-99")
    db.retire_lane("F-99")  # a retired lane has no chip
    s.running[("p1", "backend", a["key"])] = type("R", (), {"turn": ta, "kind": "normal", "wake": {"conversation_id": a["conversation_id"]}})()
    row = next(r for r in workflow.team_status(st, proj) if r["role"] == "backend")
    assert row["state"] == "idle" and row["session_id"] == "main-s" and row["context_tokens"] == 300  # the role row is its MAIN session
    assert [l["lane"] for l in row["lanes"]] == [a["key"], b["key"]]
    la, lb = row["lanes"]
    assert la["state"] == "running" and la["turn_id"] == ta["id"] and la["context_tokens"] == 240 and la["context_limit"] == 600
    assert la["context_pct"] == pytest.approx(0.4) and la["session_seq"] == 1 and la["rotation"] is None and la["stuck"] is False
    assert lb["state"] == "idle" and lb["context_pct"] == pytest.approx(0.1) and lb["turn_id"] is not None
    qa = next(r for r in workflow.team_status(st, proj) if r["role"] == "qa")
    assert qa["lanes"] == []
    db.set_role_state("backend", a["key"], rotate_reason="context 600 tokens (limit 600)", rotate_handoff=1)
    db.add_wake("frontend", a["conversation_id"], [1], "role", lane=a["key"])  # a queued wake gives a lane a chip too
    st_rows = {r["role"]: r for r in workflow.team_status(st, proj)}
    assert st_rows["backend"]["lanes"][0]["rotation"].startswith("context 600") and st_rows["backend"]["lanes"][0]["rotation_handoff_pending"]
    assert st_rows["frontend"]["lanes"][0]["lane"] == a["key"] and st_rows["frontend"]["lanes"][0]["state"] == "queued"
    assert st_rows["frontend"]["state"] == "idle"


# ---- the API ---------------------------------------------------------------------------------------------------------
def test_stop_fresh_start_turns_and_handoffs_take_a_lane(client, pid):
    proj = client.app.state.app.projects[pid]
    s = client.app.state.app.scheduler
    hits = []
    for lane in ("", "F-1"):
        s.running[(pid, "backend", lane)] = type("R", (), {"interrupt": lambda self, *a, _l=lane: hits.append(_l)})()
    assert client.post(f"/api/p/{pid}/team/backend/stop?lane=F-1").status_code == 200 and hits == ["F-1"]
    hits.clear()
    assert client.post(f"/api/p/{pid}/team/backend/stop").status_code == 200 and sorted(hits) == ["", "F-1"]
    assert client.post(f"/api/p/{pid}/team/backend/stop?lane=F-9").status_code == 409
    s.running.clear()
    proj.db.new_session("backend", "lane-a", "first turn", "F-1")
    r = client.post(f"/api/p/{pid}/team/backend/fresh-start", json={"handoff": True, "lane": "F-1"})
    assert r.status_code == 200 and r.json()["rotate_reason"] == "fresh start" and r.json()["lane"] == "F-1"
    assert proj.db.role_state("backend")["rotate_reason"] is None and proj.db.queued_wake("backend", "handoff", "F-1")
    assert client.post(f"/api/p/{pid}/team/backend/fresh-start", json={"handoff": False, "lane": "F-1"}).status_code == 409
    lane_turn(proj.db, "backend", "F-1")
    lane_turn(proj.db, "backend", "")
    assert [t["lane"] for t in client.get(f"/api/p/{pid}/turns?role=backend").json()] == ["", "F-1"]
    assert [t["lane"] for t in client.get(f"/api/p/{pid}/turns?role=backend&lane=F-1").json()] == ["F-1"]
    assert [t["lane"] for t in client.get(f"/api/p/{pid}/turns?role=backend&lane=").json()] == [""]


def test_the_manager_interrupts_one_lane_or_all_and_checkpoints_one_lane(client, pid):
    proj, _, mgr = api_turn(client, pid, "manager", "")
    s = client.app.state.app.scheduler
    hits = []
    for lane in ("", "F-1"):
        s.running[(pid, "qa", lane)] = type("R", (), {"interrupt": lambda self, *a, _l=lane: hits.append(_l)})()
    assert client.post("/internal/interrupt", json={"role": "qa", "reason": "x", "lane": "F-1"}, headers=mgr).json()["ok"] is True
    assert hits == ["F-1"]
    client.post("/internal/interrupt", json={"role": "qa", "reason": "x"}, headers=mgr)
    assert sorted(hits[1:]) == ["", "F-1"]
    s.running.clear()
    proj.db.new_session("qa", "qa-a", "first turn", "F-1")
    r = client.post("/internal/checkpoint", json={"role": "qa", "reason": "r", "handoff": True, "lane": "F-1"}, headers=mgr)
    assert r.status_code == 200 and r.json()["lane"] == "F-1" and proj.db.role_state("qa")["rotate_reason"] is None
    assert proj.db.queued_wake("qa", "handoff", "F-1")


# ---- settings --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key,top", [("max_concurrent", 12), ("max_lanes_per_role", 6)])
def test_the_lane_caps_are_whole_numbers_from_one_and_an_unchanged_stored_value_passes(key, top):
    for ok in (1, top):
        assert config.validate_role_settings({key: ok})[key] == ok
    for bad in (0, top + 1, -1, True, "3", 2.5, None):
        with pytest.raises(ValueError):
            config.validate_role_settings({key: bad})
    assert config.validate_role_settings({key: 0}, {key: 0})[key] == 0  # the dialog re-sends a stored 0 (a paused sandbox)


# ---- the heavy-run lock ------------------------------------------------------------------------------------------------
def load_heavy():
    import importlib.util
    spec = importlib.util.spec_from_file_location("heavy_under_test", ROOT_DIR / "scripts" / "heavy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_heavy_run_waits_for_the_one_that_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("AITEAMS_HOME", str(tmp_path))
    monkeypatch.delenv("AITEAMS_HEAVY_HELD", raising=False)
    heavy = load_heavy()
    ready = tmp_path / "ready"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import importlib.util, sys, time, pathlib\n"
        f"s = importlib.util.spec_from_file_location('h', {str(ROOT_DIR / 'scripts' / 'heavy.py')!r})\n"
        "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
        f"with m.heavy_lock():\n    pathlib.Path({str(ready)!r}).write_text('x'); time.sleep(1.2)\n")])
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.05)
        said, t0 = [], time.time()
        with heavy.heavy_lock(said.append):
            waited = time.time() - t0
            assert os.environ[heavy.HELD] == "1"
            with heavy.heavy_lock(said.append):  # a run started BY the holder (review -> pytest -> review tests) passes
                pass
        assert waited >= 0.5 and len(said) == 2 and said[0].startswith("heavy: another review or e2e run holds")
        assert heavy.HELD not in os.environ
        with heavy.heavy_lock(said.append):  # free again: no waiting message
            pass
        assert len(said) == 2
    finally:
        holder.wait(5)


def test_the_heavy_wrapper_runs_the_command_under_the_lock_and_passes_its_exit_code_on(tmp_path, monkeypatch):
    monkeypatch.setenv("AITEAMS_HOME", str(tmp_path))
    monkeypatch.delenv("AITEAMS_HEAVY_HELD", raising=False)
    heavy = load_heavy()
    code = "import os, sys; sys.exit(3 if os.environ.get('AITEAMS_HEAVY_HELD') == '1' else 9)"
    assert heavy.main(["--", sys.executable, "-c", code]) == 3
    assert heavy.main([]) == 2


def test_review_tree_runs_its_tests_under_the_heavy_lock(tmp_path, monkeypatch):
    import importlib.util
    monkeypatch.setenv("AITEAMS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AITEAMS_HEAVY_HELD", raising=False)
    spec = importlib.util.spec_from_file_location("review_tree_under_test", ROOT_DIR / "scripts" / "review_tree.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    held = []
    monkeypatch.setattr(rt, "run_pytest", lambda tree: (held.append(os.environ.get("AITEAMS_HEAVY_HELD")) or (True, "pytest: ok")))
    assert rt.main(["--files", "a.py", "--repo", str(repo)]) == 0
    assert held == ["1"] and "AITEAMS_HEAVY_HELD" not in os.environ


# ---- stalls and the sandbox --------------------------------------------------------------------------------------------
def test_the_stall_payload_names_the_lane_the_waited_on_role_works_in(db, home):
    from aiteams import stalls  # noqa: F401  (the row is written by the ladder; here it is written directly)
    a, plain = treed(db, "a"), db.create_feature("plain")
    db.set_stall(a["id"], "frontend", "message", "msg:1", 1)
    db.set_stall(plain["id"], "frontend", "message", "msg:2", 1)
    db.set_stall(treed(db, "m")["id"], "master", "approval", "approval:1", 1)
    assert db.feature(a["id"])["stall"]["lane"] == a["key"]
    assert db.feature(plain["id"])["stall"]["lane"] == ""
    master_feat = [f for f in db.features() if f["stall"] and f["stall"]["who"] == "master"][0]
    assert master_feat["stall"]["lane"] == ""  # the Master has no lane
    db.update_feature(a["id"], phase="done")
    assert db.stall_info(a["id"])["lane"] == ""  # a done feature's wake goes to the main lane


def test_a_long_lane_turn_is_flagged_with_its_feature(db, home):
    from aiteams import stalls
    st, proj = project(db, stuck_turn_s=60)
    st.scheduler = sched_mod.Scheduler(st)
    a = treed(db, "a")
    t = lane_turn(db, "backend", a["key"], state="running")
    t = db.update_turn(t["id"], started_at=time.time() - 30 * 60)
    st.scheduler.running = {("p1", "backend", a["key"]): type("R", (), {"turn": t})()}
    lines = asyncio.run(stalls.flag_long_turns(st, proj, set()))
    assert lines == ["Backend · F-1's turn has been running for 30m; it was not stopped."]
    assert stalls.is_stuck(st, proj, "backend", lane=a["key"]) is True and stalls.is_stuck(st, proj, "backend") is False
