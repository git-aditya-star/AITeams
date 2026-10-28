"""F-22 B1: Lead 1 / Lead 2 identity, @lead-N mentions, prompt files, features.lead, feature_lead() and set_feature(lead)."""
import pytest
from fastapi.testclient import TestClient

from aiteams import config, workflow
from aiteams.daemon import create_app
from aiteams.orchestrator.prompt import build_system_prompt, build_wake_prompt, role_prompt_text
from aiteams.orchestrator.router import parse_mentions, roles_to_wake
from tests.conftest import AUTH, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def new_feature(client, pid, title="Export"):
    return client.post(f"/api/p/{pid}/features", json={"text": title, "starter": "product"}).json()


def hdr(client, pid, role):
    return running_turn(proj_of(client, pid).db, role, pid=pid)[1]


def lead_of(client, pid, n=1):
    return proj_of(client, pid).db.feature(n)["lead"]


def pick(client, pid, n=1):
    """feature_lead() in the app's event loop, the way B2's routing will call it."""
    proj = proj_of(client, pid)
    return client.portal.call(workflow.feature_lead, client.app.state.app, proj, proj.db.feature(n))


# ---- identity -----------------------------------------------------------------------
def test_roles_names_and_aliases():
    assert config.ROLES == ["manager", "lead", "lead-2", "product", "frontend", "backend", "qa"]
    assert config.LEADS == ("lead", "lead-2")
    assert [config.role_name(r) for r in config.ROLES] == ["Manager", "Lead 1", "Lead 2", "Product", "Frontend",
                                                            "Backend", "Qa"]
    assert config.base_role("lead-2") == "lead" and config.base_role("lead") == "lead" and config.base_role("qa") == "qa"
    assert [config.canonical_role(x) for x in ("lead-1", "@Lead-1", "Lead-2", " qa ", None)] == [
        "lead", "lead", "lead-2", "qa", ""]


def test_seeding_gives_lead_2_a_dm_and_role_state(client, pid):
    db = proj_of(client, pid).db
    conv = db.conversation("dm-lead-2")
    assert conv["kind"] == "dm" and conv["role"] == "lead-2"
    assert db.one("SELECT 1 FROM role_state WHERE role='lead-2'") is not None
    assert "dm-lead-2" in [c["id"] for c in client.get(f"/api/p/{pid}/conversations").json()]


# ---- mentions -----------------------------------------------------------------------
def test_mention_aliases_and_unknown_names():
    assert parse_mentions("@lead-1 and @lead-2 please") == ["lead", "lead-2"]
    assert parse_mentions("@Lead-2, @LEAD-1.") == ["lead-2", "lead"]
    assert parse_mentions("@lead @lead-1 @lead") == ["lead"]  # the alias de-duplicates
    assert parse_mentions("@lead-3 and @lead-23") == []  # not roles, and not read as @lead either
    assert parse_mentions("@lead-review: ok, (@backend-2x)") == ["lead", "backend"]  # a hyphen + text ends the name, as before
    assert parse_mentions("mail me@lead-2 or foo@@lead-2") == []
    assert parse_mentions("no mentions", extra=["lead-1", "@lead-2", "nobody", "Master"]) == ["lead", "lead-2", "master"]


def test_a_lead_2_mention_wakes_lead_2_and_its_dm_wakes_it():
    msg = {"author": "master", "mentions": ["lead-2"]}
    assert roles_to_wake(msg, {"kind": "general", "role": None}) == ["lead-2"]
    assert roles_to_wake({"author": "master", "mentions": []}, {"kind": "dm", "role": "lead-2"}) == ["lead-2"]


# ---- prompt -------------------------------------------------------------------------
def test_lead_2_uses_lead_md_and_titles_say_lead_1_and_2(repo):
    assert role_prompt_text(str(repo), "lead-2") == role_prompt_text(str(repo), "lead") != ""
    (repo / ".aiteams" / "roles").mkdir(parents=True)
    (repo / ".aiteams" / "roles" / "lead.md").write_text("CUSTOM LEAD\n")  # a project override serves both Leads
    s1 = build_system_prompt(str(repo), "demo", "lead", ["F-1", "F-2"])
    s2 = build_system_prompt(str(repo), "demo", "lead-2", [])
    assert s1.startswith('# You are Lead 1 of the AITeams team on project "demo"') and "CUSTOM LEAD" in s1
    assert s2.startswith('# You are Lead 2 of the AITeams team on project "demo"') and "CUSTOM LEAD" in s2
    assert "(you are `lead-2`; the human is the Master)" in s2 and "manager, lead, lead-2, product" in s2
    assert ("- You are Lead 1. You act only in features whose Lead is you: F-1, F-2. "
            "Another Lead's feature is read-only for you.") in s1
    assert "features whose Lead is you: none yet." in s2
    assert "features whose Lead is you" not in build_system_prompt(str(repo), "demo", "backend", ["F-9"])
    assert "features whose Lead is you" not in build_system_prompt(str(repo), "demo", "lead")  # not computed: no line
    assert build_system_prompt(str(repo), "demo", "backend").startswith('# You are the Backend of the AITeams team')


def test_wake_prompt_names_lead_2(db):
    m = db.add_message("dm-lead-2", "master", "Message", "hi", [], None)
    assert build_wake_prompt(db, "lead-2", [m["id"]]).startswith("# New messages for Lead 2")
    assert build_wake_prompt(db, "lead", [m["id"]]).startswith("# New messages for Lead 1")


@pytest.mark.parametrize("role,expect", [("lead-2", 'You are Lead 2 of the AITeams team'),
                                         ("backend", 'You are the Backend of the AITeams team')])
def test_a_real_turn_writes_the_leads_features_line(db, home, monkeypatch, role, expect):
    """TurnRunner passes a Lead's open features to its system prompt file (lead-2 -> prompts/lead-2.system.md); other
    roles get no such line."""
    import asyncio
    from aiteams.orchestrator import scheduler as sched_mod
    from aiteams.orchestrator import turn as turn_mod
    from aiteams.runtime import AppState, Project
    from tests.conftest import FakeHub
    from tests.test_claude_proc import INIT, RESULT, fake_cmd, fake_env
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env([INIT, RESULT]))
    for t in ("One", "Two", "Three"):
        db.create_feature(t)
    db.update_feature(1, lead="lead-2")
    db.update_feature(2, lead="lead-2", phase="done")  # done: not listed
    db.update_feature(3, lead="lead")
    st = AppState(7799)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    m = proj.db.add_message(f"dm-{role}", "master", "Message", "hi", [], None)
    runner = turn_mod.TurnRunner(st, proj, proj.db.add_wake(role, f"dm-{role}", [m["id"]], "master"))
    asyncio.run(sched_mod.Scheduler(st)._run(("p1", role, ""), runner))
    text = open(f"{db.project_path}/.aiteams/prompts/{role}.system.md").read()
    assert expect in text
    if role == "lead-2":
        assert "You act only in features whose Lead is you: F-1. Another Lead's feature is read-only for you." in text
        assert "# New messages for Lead 2" in proj.db.turns("lead-2", limit=1)[0]["prompt"]
    else:
        assert "features whose Lead is you" not in text
    proj.db.close()


def test_lead_open_features_lists_only_open_features_of_that_lead(client, pid):
    for t in ("One", "Two", "Three"):
        new_feature(client, pid, t)
    db = proj_of(client, pid).db
    db.update_feature(1, lead="lead-2")
    db.update_feature(2, lead="lead-2", phase="done")
    db.update_feature(3, lead="lead-2", archived=True)
    assert workflow.lead_open_features(db, "lead-2") == ["F-1"]
    assert workflow.lead_open_features(db, "lead") == []


# ---- features.lead, header, listings ----------------------------------------------------
def test_new_features_have_no_lead_and_the_header_says_so(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    assert lead_of(client, pid) is None
    head = workflow.feature_header(db, "", db.feature(1))
    assert "· conversation id: feature-1 · lead: not assigned" in head.splitlines()[1]
    db.update_feature(1, lead="lead-2")
    assert workflow.feature_header(db, "", db.feature(1)).splitlines()[1].endswith("· lead: Lead 2")
    db.update_feature(1, lead="lead", archived=True)
    assert workflow.feature_header(db, "", db.feature(1)).splitlines()[1].endswith("· lead: Lead 1 · ARCHIVED")
    assert client.get(f"/api/p/{pid}/features/1").json()["lead"] == "lead"
    assert client.get(f"/api/p/{pid}/features", params={"history": 1}).json()[0]["lead"] == "lead"
    assert client.get(f"/api/p/{pid}/dashboard").json()["features"] == []  # archived: not active
    line = client.get("/internal/features", params={"history": 1}, headers=hdr(client, pid, "qa")).json()["features"][0]
    assert " · lead Lead 1 · Export" in line


def test_start_over_keeps_the_lead(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    db.update_feature(1, lead="lead-2", owner="backend", phase="build")
    r = client.post(f"/api/p/{pid}/features/1/fresh-start", json={"mode": "start_over"})
    assert r.status_code == 200, r.text
    f = db.feature(1)
    assert (f["lead"], f["owner"], f["phase"]) == ("lead-2", None, "intake")


def test_standup_compile_prompt_shows_each_features_lead(client, pid, repo):
    from aiteams.orchestrator.prompt import build_compile_prompt
    new_feature(client, pid, "One")
    new_feature(client, pid, "Two")
    db = proj_of(client, pid).db
    db.update_feature(2, lead="lead-2")
    st, proj = client.app.state.app, proj_of(client, pid)
    standup = {"id": 1, "requested_by": "master", "start_message_id": 0}
    text = build_compile_prompt(db, str(repo), standup, workflow.team_status(st, proj), workflow.dashboard_features(st, proj))
    assert "- F-2 Two: phase intake, owner none, lead Lead 2, waits on" in text
    assert "- F-1 One: phase intake, owner none, lead not assigned, waits on" in text


# ---- feature_lead(): the no-stall fallback ----------------------------------------------
def test_feature_lead_fallback_order_and_the_system_line(client, pid):
    for t in ("One", "Two", "Three", "Four", "Five"):
        new_feature(client, pid, t)
    db = proj_of(client, pid).db
    st, proj = client.app.state.app, proj_of(client, pid)

    # both idle, no features: Lead 1 (the default)
    assert pick(client, pid, 1) == "lead" and lead_of(client, pid, 1) == "lead"
    assert "Lead 1 picked automatically (default)." in db.messages("feature-1", limit=1)[-1]["text"]
    assert db.messages("feature-1", limit=1)[-1]["author"] == "system"
    n = len(db.messages("feature-1", limit=50))
    assert pick(client, pid, 1) == "lead" and len(db.messages("feature-1", limit=50)) == n  # stored: no second pick

    # Lead 1 has an open feature and both idle: Lead 2 has fewer, so it's picked
    assert pick(client, pid, 2) == "lead-2"
    assert "Lead 2 picked automatically (fewer open features)." in db.messages("feature-2", limit=1)[-1]["text"]

    # Lead 1 busy (a queued wake), Lead 2 idle although it has more open features: the idle one wins
    db.add_wake("lead", "general", [], "role")
    db.update_feature(2, phase="build")
    assert pick(client, pid, 3) == "lead-2"
    assert "Lead 2 picked automatically (Lead 1 was busy)." in db.messages("feature-3", limit=1)[-1]["text"]

    # a running Lead counts as busy too: Lead 2 running, Lead 1's queued wake gone -> Lead 1
    db.x("DELETE FROM wake_queue WHERE role='lead'")
    st.scheduler.running[(proj.id, "lead-2", "")] = object()
    try:
        assert pick(client, pid, 4) == "lead"
        # both busy: fewer open features (Lead 1 has F-1, F-4; Lead 2 has F-2, F-3) -> equal -> Lead 1
        db.add_wake("lead", "general", [], "role")
        assert pick(client, pid, 5) == "lead"
    finally:
        st.scheduler.running.pop((proj.id, "lead-2", ""), None)
    assert [lead_of(client, pid, i) for i in range(1, 6)] == ["lead", "lead-2", "lead-2", "lead", "lead"]


def test_feature_lead_done_and_archived_features_dont_count_as_load(client, pid):
    for t in ("One", "Two", "Three"):
        new_feature(client, pid, t)
    db = proj_of(client, pid).db
    db.update_feature(1, lead="lead")
    db.update_feature(2, lead="lead", phase="done")
    assert workflow.lead_open_features(db, "lead") == ["F-1"]
    db.update_feature(1, phase="done")
    assert pick(client, pid, 3) == "lead"  # equal load (0 vs 0) again -> Lead 1


# ---- set_feature(lead) ----------------------------------------------------------------
def test_the_manager_sets_the_lead_others_are_refused(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    for role in ("lead", "lead-2", "backend", "product"):
        r = client.patch("/internal/features/F-1", json={"lead": "lead-2"}, headers=hdr(client, pid, role))
        assert r.status_code == 403 and r.json()["detail"] == "only the Manager assigns a feature's Lead", role
    assert lead_of(client, pid) is None
    manager = hdr(client, pid, "manager")
    r = client.patch("/internal/features/F-1", json={"lead": "lead-2"}, headers=manager)
    assert r.status_code == 200 and "· lead: Lead 2" in r.json()["header"]
    assert lead_of(client, pid) == "lead-2"
    sys_line = [m for m in db.messages("feature-1", limit=10) if m["author"] == "system"][-1]
    assert sys_line["text"] == "Lead set to Lead 2 by manager"
    # lead-1 is an alias for lead; invalid and empty values are refused for the Manager
    assert client.patch("/internal/features/F-1", json={"lead": "lead-1"}, headers=manager).status_code == 200
    assert lead_of(client, pid) == "lead"
    for bad in ("lead-3", "backend", "", "master"):
        r = client.patch("/internal/features/F-1", json={"lead": bad}, headers=manager)
        assert r.status_code == 400 and r.json()["detail"] == "lead must be lead-1 or lead-2", bad
    assert lead_of(client, pid) == "lead"
    # a Lead can still change phase/owner/title with no `lead` in the body
    assert client.patch("/internal/features/F-1", json={"phase": "spec"}, headers=hdr(client, pid, "lead")).status_code == 200


def test_the_master_sets_or_clears_the_lead_and_the_new_lead_is_woken(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    db.x("DELETE FROM wake_queue")
    r = client.patch(f"/api/p/{pid}/features/1", json={"lead": "lead-2"})
    assert r.status_code == 200 and r.json()["lead"] == "lead-2"
    msgs = db.messages("feature-1", limit=4)
    assert msgs[-2]["text"] == "Lead set to Lead 2 by Master" and msgs[-2]["author"] == "system"
    assert msgs[-1]["text"] == "@lead-2 F-1 is yours now." and msgs[-1]["author"] == "master"
    assert msgs[-1]["mentions"] == ["lead-2"]
    assert [w["role"] for w in db.queued_wakes()] == ["lead-2"]  # only the new Lead is woken
    # unchanged: nothing posted, no wake
    n = len(db.messages("feature-1", limit=50))
    assert client.patch(f"/api/p/{pid}/features/1", json={"lead": "lead-2"}).status_code == 200
    assert len(db.messages("feature-1", limit=50)) == n
    # reassign to Lead 1 through the alias wakes Lead 1
    db.x("DELETE FROM wake_queue")
    assert client.patch(f"/api/p/{pid}/features/1", json={"lead": "lead-1"}).json()["lead"] == "lead"
    assert [w["role"] for w in db.queued_wakes()] == ["lead"]
    # the Master can clear it ("not assigned"); that wakes nobody
    db.x("DELETE FROM wake_queue")
    assert client.patch(f"/api/p/{pid}/features/1", json={"lead": None}).json()["lead"] is None
    assert db.messages("feature-1", limit=2)[-1]["text"] == "Lead set to not assigned by Master"
    assert db.queued_wakes() == []
    assert client.patch(f"/api/p/{pid}/features/1", json={"lead": "lead-9"}).status_code == 400


def test_a_manager_reassign_wakes_the_new_lead_with_the_managers_message(client, pid):
    new_feature(client, pid)
    db = proj_of(client, pid).db
    db.update_feature(1, lead="lead")
    db.x("DELETE FROM wake_queue")
    assert client.patch("/internal/features/F-1", json={"lead": "lead-2"}, headers=hdr(client, pid, "manager")).status_code == 200
    last = db.messages("feature-1", limit=1)[-1]
    assert last["author"] == "manager" and last["text"] == "@lead-2 F-1 is yours now." and last["mentions"] == ["lead-2"]
    assert [w["role"] for w in db.queued_wakes()] == ["lead-2"]


# ---- MCP -------------------------------------------------------------------------------
def test_set_feature_tool_offers_and_forwards_lead(monkeypatch):
    from aiteams import mcp_server
    tool = next(t for t in mcp_server.TOOLS if t["name"] == "set_feature")
    assert tool["inputSchema"]["properties"]["lead"]["enum"] == ["lead-1", "lead-2"]
    seen = {}
    monkeypatch.setattr(mcp_server, "http",
                        lambda method, path, body=None: seen.update(method=method, path=path, body=body) or {"header": "h"})
    assert mcp_server.tool_set_feature({"feature": "F-3", "lead": "lead-2", "bogus": 1}) == "h"
    assert seen == {"method": "PATCH", "path": "/internal/features/F-3", "body": {"lead": "lead-2"}}
