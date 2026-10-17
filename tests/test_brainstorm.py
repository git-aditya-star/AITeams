"""F-10 R2: brainstorm round — start (Master / Product / Manager), fan-out to the feature's voices, progress to
Product's compile turn, the brainstorm card closing the round, the failure path, prompts and restart re-queue."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from aiteams.orchestrator import lifecycle
from aiteams.orchestrator.prompt import build_brainstorm_compile_prompt, build_brainstorm_prompt
from aiteams.orchestrator.scheduler import Scheduler
from aiteams.orchestrator.turn import TurnRunner
from tests.conftest import AUTH, running_turn
from tests.test_lifecycle import FinishedRunner

CARD = [{"question": "Which approach?", "header": "Approach", "multiSelect": False,
         "options": [{"label": "Stream it (Recommended)", "description": "backend"},
                     {"label": "Chunk in the UI", "description": "frontend"}]}]


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    client.post(f"/api/p/{pid}/features", json={"text": "Import", "starter": "lead"})
    proj = client.app.state.app.projects[pid]
    proj.db.add_message("feature-1", "lead", "Message", "@backend take the API", ["backend"], None)
    proj.db.x("DELETE FROM wake_queue")
    return pid


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def end_turn(st, proj, wake, state="done", idea=None):
    """Run a fake turn for `wake` to the end (optionally posting an idea from it), then the after-turn bookkeeping."""
    db = proj.db
    db.set_wake_state(wake["id"], "started")
    t = db.create_turn(wake["role"], "s", wake["origin"], wake["conversation_id"], wake["kind"])
    db.update_turn(t["id"], state="running", wake_id=wake["id"])
    if idea:
        db.add_message(wake["conversation_id"], wake["role"], "Message", idea, [], t["id"])
    t = db.update_turn(t["id"], state=state, ended_at=time.time())
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, kind=wake["kind"])))
    return t


def test_master_start_fans_out_to_voices(client, pid):
    proj = proj_of(client, pid)
    db = proj.db
    r = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "  How to export\nbig files? "})
    assert r.status_code == 200, r.text
    bs = r.json()
    assert (bs["state"], bs["topic"], bs["requested_by"]) == ("collecting", "How to export big files?", "master")
    assert bs["roles"] == workflow.voices(db, db.feature("F-1")) == ["lead", "product", "backend"]
    line = db.message(bs["start_message_id"])
    assert (line["author"], line["type"], line["mentions"], line["ref"]) == ("system", "System", [], f"brainstorm:{bs['id']}")
    assert line["text"] == (f"Brainstorm #{bs['id']} on How to export big files? (started by the Master)"
                            " — waiting for lead, product, backend")
    assert line["brainstorm"]["id"] == bs["id"]
    assert sorted((w["role"], w["kind"], w["ref"], w["origin"], w["conversation_id"]) for w in db.queued_wakes()) == [
        (r_, "brainstorm", bs["id"], "master", "feature-1") for r_ in ("backend", "lead", "product")]
    assert {t["role"]: t["state"] for t in workflow.team_status(client.app.state.app, proj)}["backend"] == "queued"
    # one active round per feature; another feature is fine
    assert client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "again"}).status_code == 409
    assert client.post(f"/api/p/{pid}/features/2/brainstorm", json={"topic": "other"}).status_code == 200
    assert [b["id"] for b in client.get(f"/api/p/{pid}/features/1/brainstorms").json()] == [bs["id"]]


@pytest.mark.parametrize("topic", ["", "   \n "])
def test_empty_topic_is_400(client, pid, topic):
    r = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": topic})
    assert r.status_code == 400 and "topic" in r.json()["detail"]
    assert client.post(f"/api/p/{pid}/features/99/brainstorm", json={"topic": "x"}).status_code == 404


def test_master_route_needs_the_token(home, repo):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app) as c:
        assert c.post("/api/p/x/features/1/brainstorm", json={"topic": "x"}).status_code == 401


def test_roles_start_product_and_manager_only(client, pid):
    db = proj_of(client, pid).db
    for role in ("backend", "lead", "qa", "frontend"):
        hdr = running_turn(db, role, pid=pid)[1]
        r = client.post("/internal/brainstorm", json={"feature": "F-1", "topic": "x"}, headers=hdr)
        assert r.status_code == 403, role
    hdr = running_turn(db, "product", pid=pid)[1]
    r = client.post("/internal/brainstorm", json={"feature": "F-1", "topic": "Big files"}, headers=hdr)
    assert r.status_code == 200 and r.json()["feature"] == "F-1" and r.json()["roles"] == ["lead", "product", "backend"]
    assert db.brainstorm(r.json()["id"])["requested_by"] == "product"
    assert {w["origin"] for w in db.queued_wakes()} == {"role"}
    hdr = running_turn(db, "manager", pid=pid)[1]
    assert client.post("/internal/brainstorm", json={"feature": "F-1", "topic": "y"}, headers=hdr).status_code == 409
    assert client.post("/internal/brainstorm", json={"feature": "F-2", "topic": "y"}, headers=hdr).status_code == 200
    assert client.post("/internal/brainstorm", json={"feature": "", "topic": "y"}, headers=hdr).status_code == 400


def test_progress_compile_card_closes_round(client, pid):
    st = client.app.state.app
    proj = proj_of(client, pid)
    db = proj.db
    bs = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "Big files"}).json()
    wakes = {w["role"]: w for w in db.queued_wakes()}
    db.add_message("feature-1", "qa", "Message", "unrelated chatter", [], None)  # not an idea: no brainstorm turn
    end_turn(st, proj, wakes["lead"], "done", "Stream from the backend.")
    end_turn(st, proj, wakes["product"], "interrupted")
    assert db.brainstorm(bs["id"])["state"] == "collecting" and db.queued_wake("product", "brainstorm_compile") is None
    end_turn(st, proj, wakes["backend"], "failed", "Chunk it in the UI.")  # any end state counts
    assert db.brainstorm(bs["id"])["state"] == "compiling"
    cw = db.queued_wake("product", "brainstorm_compile")
    assert (cw["ref"], cw["origin"], cw["conversation_id"]) == (bs["id"], "system", "feature-1")

    # the compile prompt lists exactly the ideas from the round's turns
    prompt = build_brainstorm_compile_prompt(db, proj.path, db.brainstorm(bs["id"]))
    assert "Stream from the backend." in prompt and "Chunk it in the UI." in prompt and "unrelated" not in prompt
    assert 'ask_master(purpose="brainstorm", feature="F-1"' in prompt

    # Product's compile turn posts the brainstorm card: the round is done with question_id
    db.set_wake_state(cw["id"], "started")
    t, hdr = running_turn(db, "product", "feature-1", "brainstorm_compile", cw["id"], pid=pid)
    r = client.post("/internal/questions", json={"questions": CARD, "purpose": "brainstorm", "intro": "Two camps."},
                    headers=hdr)
    assert r.status_code == 200, r.text
    card = db.question(r.json()["id"])
    assert (card["purpose"], card["conversation_id"], card["feature_id"]) == ("brainstorm", "feature-1", 1)
    done = db.brainstorm(bs["id"])
    assert (done["state"], done["question_id"]) == ("done", card["id"]) and done["ended_at"]
    assert card["id"] in [q["id"] for q in client.get(f"/api/p/{pid}/inbox").json()["questions"]]
    # a second card in the same turn is refused; the turn ending doesn't fail the round
    assert client.post("/internal/questions", json={"questions": CARD, "purpose": "brainstorm"},
                       headers=hdr).status_code == 409
    t = db.update_turn(t["id"], state="done", ended_at=time.time())
    asyncio.run(lifecycle.after_turn(st, proj, FinishedRunner(proj, t, kind="brainstorm_compile")))
    assert db.brainstorm(bs["id"])["state"] == "done"
    assert client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "next"}).status_code == 200


def test_brainstorm_card_permissions(client, pid):
    db = proj_of(client, pid).db
    body = {"questions": CARD, "feature": "F-1", "purpose": "brainstorm"}
    # Product in a normal turn, and another role, are refused
    assert client.post("/internal/questions", json=body, headers=running_turn(db, "product", pid=pid)[1]).status_code == 403
    bs = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "x"}).json()
    db.update_brainstorm(bs["id"], state="compiling")
    w = db.add_wake("lead", "feature-1", [], "system", kind="brainstorm_compile", ref=bs["id"])
    hdr = running_turn(db, "lead", "feature-1", "brainstorm_compile", w["id"], pid=pid)[1]
    assert client.post("/internal/questions", json=body, headers=hdr).status_code == 403
    # Product's compile turn, but naming another feature
    w = db.add_wake("product", "feature-1", [], "system", kind="brainstorm_compile", ref=bs["id"])
    hdr = running_turn(db, "product", "feature-1", "brainstorm_compile", w["id"], pid=pid)[1]
    assert client.post("/internal/questions", json={**body, "feature": "F-2"}, headers=hdr).status_code == 400
    assert db.questions("pending") == []


def test_compile_without_card_fails_round(client, pid):
    st = client.app.state.app
    proj = proj_of(client, pid)
    db = proj.db
    bs = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "x"}).json()
    for w in db.queued_wakes():
        end_turn(st, proj, w)
    cw = db.queued_wake("product", "brainstorm_compile")
    assert "no role posted an idea" in build_brainstorm_compile_prompt(db, proj.path, db.brainstorm(bs["id"]))
    end_turn(st, proj, cw)
    failed = db.brainstorm(bs["id"])
    assert failed["state"] == "failed" and failed["ended_at"] and failed["question_id"] is None
    line = db.messages("feature-1")[-1]
    assert (line["author"], line["ref"]) == ("system", f"brainstorm:{bs['id']}")
    assert line["text"] == f"Brainstorm #{bs['id']}: Product did not post a summary card; the round failed."
    assert client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "again"}).status_code == 200


def test_brainstorm_prompts_and_turn_wiring(client, pid):
    st = client.app.state.app
    proj = proj_of(client, pid)
    db = proj.db
    bs = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "Big files"}).json()
    w = db.queued_wake("backend", "brainstorm")
    prompt = TurnRunner(st, proj, w)._prompt("brainstorm", [], None)
    assert prompt == build_brainstorm_prompt(db, proj.path, "backend", db.brainstorm(bs["id"]))
    assert f"# Brainstorm #{bs['id']} on F-1, started by the Master" in prompt and "Topic: Big files" in prompt
    assert "Post exactly ONE message in conversation `feature-1`" in prompt and "Do no other work" in prompt
    assert "@backend take the API" in prompt and "- voices: lead, product, backend" in prompt
    gone = db.add_wake("backend", "feature-1", [], "system", kind="brainstorm", ref=999)
    assert "no longer exists" in TurnRunner(st, proj, gone)._prompt("brainstorm", [], None)


def test_restart_requeues_brainstorm_wakes(client, pid):
    st = client.app.state.app
    proj = proj_of(client, pid)
    db = proj.db
    bs = client.post(f"/api/p/{pid}/features/1/brainstorm", json={"topic": "x"}).json()
    for kind, role in (("brainstorm", "lead"), ("brainstorm_compile", "product")):
        w = db.queued_wake(role, "brainstorm")
        if kind == "brainstorm_compile":
            w = db.add_wake("product", "feature-1", [], "system", kind=kind, ref=bs["id"])
        db.set_wake_state(w["id"], "started")
        asyncio.run(Scheduler(st).requeue_once(proj, db.wake(w["id"]), role))
        again = db.queued_wake(role, kind)
        assert again and again["id"] != w["id"] and again["ref"] == bs["id"]
