"""F-34: a role withdraws a request the Master hasn't decided (an approval, a commit request or a question card):
the sender or the feature's Lead, with a one-line reason; it leaves the Inbox and stays as `withdrawn`."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from aiteams import commit_graph, workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, BRIEF, running_turn

LEAD = "lead-2"  # F-1's Lead in every test; "lead" is the other one
KINDS = ("approval", "commit", "card")
LABEL = {"approval": "Approval", "commit": "Commit request", "card": "Card"}
FRAME = {"approval": "approval", "commit": "commit", "card": "question"}
CARD = [{"question": "Which format?", "header": "Format", "multiSelect": False,
         "options": [{"label": "CSV", "description": "a"}, {"label": "XLSX", "description": "b"}]}]


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    client.app.state.app.projects[pid].db.update_feature(1, lead=LEAD)
    return pid


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def hdr(client, pid, role):
    return running_turn(proj_of(client, pid).db, role, pid=pid)[1]


def inbox(client, pid):
    return client.get(f"/api/p/{pid}/inbox").json()


def make(client, pid, repo, kind, role="frontend", feature="F-1", name=None, requires=(), approve=True):
    """Create an approval, a commit request or a card as `role`; a commit is Lead-approved into master_review."""
    h = hdr(client, pid, role)
    if kind == "approval":
        r = client.post("/internal/approvals", json={"feature": feature, "title": "Scope lock: Export", **BRIEF}, headers=h)
    elif kind == "card":
        r = client.post("/internal/questions", json={"questions": CARD, "feature": feature}, headers=h)
    else:
        name = name or f"f_{role}_{len(list(repo.iterdir()))}.py"
        (repo / name).write_text(f"{name}\n")
        r = client.post("/internal/commits", json={"feature": feature, "files": [name], "message": f"add {name}",
                                                   "requires": list(requires)}, headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    if kind == "commit" and approve and r.json()["state"] == "lead_review":
        assert client.post(f"/internal/commits/{rid}/review", json={"decision": "approve"},
                           headers=hdr(client, pid, LEAD)).status_code == 200
    return rid


def withdraw(client, pid, role, kind, rid, reason="superseded by #99"):
    return client.post("/internal/withdraw", json={"kind": kind, "id": rid, "reason": reason}, headers=hdr(client, pid, role))


def row(client, pid, kind, rid):
    db = proj_of(client, pid).db
    return {"approval": db.approval, "commit": db.commit, "card": db.question}[kind](rid)


def last(client, pid, conv="feature-1"):
    return proj_of(client, pid).db.messages(conv)[-1]


# ---- who, what, and what it leaves behind -----------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_the_sender_withdraws_and_it_leaves_the_inbox(client, pid, repo, kind):
    proj = proj_of(client, pid)
    before = inbox(client, pid)["count"]
    rid = make(client, pid, repo, kind)
    assert inbox(client, pid)["count"] == before + 1
    wakes = len(proj.db.queued_wakes())
    r = withdraw(client, pid, "frontend", kind, rid)
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": kind, "id": rid, "state": "withdrawn", "note": "superseded by #99"}
    assert (row(client, pid, kind, rid)["state"], row(client, pid, kind, rid)["note"]) == ("withdrawn", "superseded by #99")
    assert inbox(client, pid)["count"] == before
    m = last(client, pid)
    assert (m["author"], m["type"], m["mentions"]) == ("system", "System", [])
    assert m["text"] == f"{LABEL[kind]} #{rid} withdrawn by frontend: superseded by #99."
    assert m["ref"] == (f"commit_event:{rid}" if kind == "commit" else None)  # a commit: its events, not a commit card
    assert len(proj.db.queued_wakes()) == wakes  # the sender withdrew its own request: nobody is woken
    act = proj.db.activity(1)[0]
    assert (act["kind"], act["role"], act["text"]) == (FRAME[kind], "frontend", f"{LABEL[kind]} #{rid} withdrawn by frontend")
    assert act["ref"] == f"{FRAME[kind]}:{rid}"


@pytest.mark.parametrize("kind", KINDS)
def test_the_features_lead_withdraws_another_roles_request_and_the_sender_is_mentioned(client, pid, repo, kind):
    proj = proj_of(client, pid)
    rid = make(client, pid, repo, kind)
    wakes = len(proj.db.queued_wakes())
    r = withdraw(client, pid, LEAD, kind, rid, "wrong files!")
    assert r.status_code == 200, r.text
    assert row(client, pid, kind, rid)["state"] == "withdrawn"
    m = last(client, pid)
    assert (m["author"], m["mentions"]) == ("system", ["frontend"])
    assert m["text"] == f"{LABEL[kind]} #{rid} withdrawn by {LEAD}: wrong files!"  # no second full stop after "!"
    assert len(proj.db.queued_wakes()) == wakes  # a System line never wakes anyone, mention or not (router.roles_to_wake)


@pytest.mark.parametrize("kind", KINDS)
def test_nobody_else_can_withdraw_it(client, pid, repo, kind):
    rid = make(client, pid, repo, kind)
    n = len(proj_of(client, pid).db.messages("feature-1"))
    for role in ("product", "backend", "manager", "lead"):  # "lead" is the other Lead
        r = withdraw(client, pid, role, kind, rid)
        assert r.status_code == 403, (role, r.text)
        assert f"only frontend or {LEAD} can withdraw {kind} #{rid}" in r.json()["detail"]
    assert row(client, pid, kind, rid)["state"] in ("pending", "master_review")
    assert len(proj_of(client, pid).db.messages("feature-1")) == n


def test_a_lead_sends_its_own_request_and_a_feature_without_a_lead_lets_only_the_sender_withdraw(client, pid, repo):
    proj = proj_of(client, pid)
    mine = make(client, pid, repo, "approval", role=LEAD)
    assert withdraw(client, pid, "lead", "approval", mine).status_code == 403
    assert "only lead-2 can withdraw" in withdraw(client, pid, "frontend", "approval", mine).json()["detail"]  # Lead = sender
    proj.db.x("UPDATE features SET lead=NULL WHERE id=1")
    theirs = make(client, pid, repo, "card", role="frontend")
    r = withdraw(client, pid, "lead", "card", theirs)
    assert r.status_code == 403 and "only frontend can withdraw card" in r.json()["detail"]
    assert proj.db.feature(1)["lead"] is None  # a withdrawal never claims an unled feature (the F-22 path)
    assert withdraw(client, pid, "frontend", "card", theirs).status_code == 200
    other = make(client, pid, repo, "approval", role="frontend")
    assert withdraw(client, pid, LEAD, "approval", other).status_code == 403  # no Lead set: not even the former one


def test_a_request_with_no_feature_goes_to_the_senders_dm_and_only_it_can_withdraw(client, pid, repo):
    proj = proj_of(client, pid)
    aid = client.post("/internal/approvals", json={"title": "Use skill: x:y", **BRIEF},
                      headers=hdr(client, pid, "frontend")).json()["id"]
    assert withdraw(client, pid, LEAD, "approval", aid).status_code == 403
    assert withdraw(client, pid, "frontend", "approval", aid, "not needed").status_code == 200
    m = proj.db.messages("dm-frontend")[-1]
    assert m["author"] == "system" and m["text"] == f"Approval #{aid} withdrawn by frontend: not needed."
    cid = client.post("/internal/questions", json={"questions": CARD}, headers=hdr(client, pid, "backend")).json()["id"]
    assert withdraw(client, pid, "backend", "card", cid, "not needed").status_code == 200
    assert proj.db.messages("dm-backend")[-1]["text"] == f"Card #{cid} withdrawn by backend: not needed."


# ---- refusals ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reason", ["", "   ", "\n", "line one\nline two", "line one\r\nline two"])
def test_a_missing_or_multi_line_reason_is_a_400(client, pid, repo, reason):
    rid = make(client, pid, repo, "approval")
    r = withdraw(client, pid, "frontend", "approval", rid, reason)
    assert r.status_code == 400 and "one-line reason" in r.json()["detail"]
    assert row(client, pid, "approval", rid)["state"] == "pending"
    assert client.post("/internal/withdraw", json={"kind": "approval", "id": rid},
                       headers=hdr(client, pid, "frontend")).status_code == 400  # no reason at all


def test_the_reason_is_trimmed_and_cut_to_200_characters_and_a_final_full_stop_is_not_doubled(client, pid, repo):
    a, b = make(client, pid, repo, "approval"), make(client, pid, repo, "approval")
    assert withdraw(client, pid, "frontend", "approval", a, "  " + "x" * 300 + "  ").status_code == 200
    assert row(client, pid, "approval", a)["note"] == "x" * 200
    assert withdraw(client, pid, "frontend", "approval", b, "no longer needed.").status_code == 200
    assert last(client, pid)["text"] == f"Approval #{b} withdrawn by frontend: no longer needed."


def test_bad_kind_unknown_id_and_a_blocking_question_are_refused(client, pid, repo):
    proj = proj_of(client, pid)
    rid = make(client, pid, repo, "approval")
    assert withdraw(client, pid, "frontend", "question", rid).status_code == 400
    for kind in KINDS:
        r = withdraw(client, pid, "frontend", kind, 9999)
        assert r.status_code == 404 and f"unknown {kind} #9999" in r.json()["detail"]
    t = proj.db.create_turn("frontend", "s", "master", "dm-frontend")
    blocking = proj.db.create_question(t["id"], "frontend", "tu1", {"questions": []})["id"]  # an AskUserQuestion
    assert withdraw(client, pid, "frontend", "card", blocking).status_code == 404
    assert proj.db.question(blocking)["state"] == "pending"
    assert client.post("/internal/withdraw", json={"kind": "approval", "id": "x", "reason": "r"},
                       headers=hdr(client, pid, "frontend")).status_code == 422
    assert client.post("/internal/withdraw", json={"kind": "approval", "id": rid, "reason": "r"}).status_code in (401, 403)  # no turn


def test_a_decided_request_cannot_be_withdrawn(client, pid, repo):
    proj = proj_of(client, pid)
    a1, a2, a3 = (make(client, pid, repo, "approval") for _ in range(3))
    for aid, decision in ((a1, "approve"), (a2, "reject"), (a3, "changes")):
        client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": decision, "note": "n"})
    for aid, state in ((a1, "approved"), (a2, "rejected"), (a3, "changes_requested")):
        r = withdraw(client, pid, "frontend", "approval", aid)
        assert r.status_code == 409 and r.json()["detail"] == f"approval #{aid} is {state}"
    card = make(client, pid, repo, "card")
    client.post(f"/api/p/{pid}/questions/{card}/answer", json={"answers": {"Which format?": "CSV"}})
    assert withdraw(client, pid, "frontend", "card", card).json()["detail"] == f"card #{card} is answered"
    c1, c2, c3 = (make(client, pid, repo, "commit", role="backend") for _ in range(3))
    for cid, state in ((c1, "rejected"), (c2, "committed"), (c3, "failed")):
        proj.db.update_commit(cid, state=state)
        r = withdraw(client, pid, "backend", "commit", cid)
        assert r.status_code == 409 and r.json()["detail"] == f"commit #{cid} is {state}"
    once = make(client, pid, repo, "approval")
    assert withdraw(client, pid, "frontend", "approval", once).status_code == 200
    r = withdraw(client, pid, "frontend", "approval", once)  # twice
    assert r.status_code == 409 and r.json()["detail"] == f"approval #{once} is withdrawn"


def test_a_commit_in_lead_review_can_be_withdrawn_by_the_sender_or_the_lead(client, pid, repo):
    proj = proj_of(client, pid)
    a = make(client, pid, repo, "commit", role="backend", approve=False)
    b = make(client, pid, repo, "commit", role="frontend", approve=False)
    assert proj.db.commit(a)["state"] == "lead_review"
    assert withdraw(client, pid, "backend", "commit", a).status_code == 200
    assert withdraw(client, pid, LEAD, "commit", b).status_code == 200
    assert [proj.db.commit(x)["state"] for x in (a, b)] == ["withdrawn", "withdrawn"]


def test_a_commit_that_is_being_committed_cannot_be_withdrawn(client, pid, repo):
    proj = proj_of(client, pid)
    cid = make(client, pid, repo, "commit", role="backend")
    proj.committing.add(cid)
    r = withdraw(client, pid, "backend", "commit", cid)
    assert r.status_code == 409 and r.json()["detail"] == f"commit #{cid} is being committed"
    assert proj.db.commit(cid)["state"] == "master_review"
    proj.committing.discard(cid)
    assert withdraw(client, pid, "backend", "commit", cid).status_code == 200


def decide(client, pid, cid, **body):
    return client.post(f"/api/p/{pid}/commits/{cid}/decide", json={"decision": "approve", **body})


def test_the_committing_set_is_held_around_the_git_call_and_always_released(client, pid, repo, monkeypatch):
    proj = proj_of(client, pid)
    a = make(client, pid, repo, "commit", role="backend")
    seen = []

    def fake(path, files, message):
        seen.append(set(proj.committing))
        return True, "abc1234"
    monkeypatch.setattr(workflow, "_commit_sync", fake)
    assert decide(client, pid, a).status_code == 200
    assert seen == [{a}] and proj.committing == set() and proj.db.commit(a)["state"] == "committed"
    b = make(client, pid, repo, "commit", role="backend")

    def boom(path, files, message):
        seen.append(set(proj.committing))
        raise RuntimeError("git went away")
    monkeypatch.setattr(workflow, "_commit_sync", boom)
    with pytest.raises(RuntimeError):
        decide(client, pid, b)
    assert seen[-1] == {b} and proj.committing == set()  # released in finally


def test_every_member_of_a_group_is_held_while_the_group_is_committed(client, pid, repo, monkeypatch):
    proj = proj_of(client, pid)
    base = make(client, pid, repo, "commit", role="backend")
    dep = make(client, pid, repo, "commit", role="frontend", requires=[base])
    seen = []

    def fake(path, members):
        seen.append(set(proj.committing))
        return ["s1", "s2"], None, ""
    monkeypatch.setattr(workflow, "_group_commit_sync", fake)
    r = decide(client, pid, base, group=True)
    assert r.status_code == 200, r.text
    assert seen == [{base, dep}] and proj.committing == set()
    assert [proj.db.commit(x)["state"] for x in (base, dep)] == ["committed", "committed"]


# ---- commit groups -------------------------------------------------------------------------------------------------
def test_a_commit_that_an_open_commit_requires_cannot_be_withdrawn_until_that_one_is_gone(client, pid, repo):
    proj = proj_of(client, pid)
    base = make(client, pid, repo, "commit", role="backend")
    dep = make(client, pid, repo, "commit", role="frontend", requires=[base])
    r = withdraw(client, pid, "backend", "commit", base)
    assert r.status_code == 409 and r.json()["detail"] == f"#{dep} requires #{base}: withdraw or re-file #{dep} first"
    assert proj.db.commit(base)["state"] == "master_review"
    assert withdraw(client, pid, "frontend", "commit", dep).status_code == 200
    assert withdraw(client, pid, "backend", "commit", base).status_code == 200  # nothing open requires it now


def test_a_rejected_dependent_does_not_hold_its_base(client, pid, repo):
    proj = proj_of(client, pid)
    base = make(client, pid, repo, "commit", role="backend")
    dep = make(client, pid, repo, "commit", role="frontend", requires=[base])
    proj.db.update_commit(dep, state="rejected")
    assert withdraw(client, pid, "backend", "commit", base).status_code == 200


def test_a_withdrawn_member_leaves_its_group_and_the_base_commits_alone(client, pid, repo):
    proj = proj_of(client, pid)
    base = make(client, pid, repo, "commit", role="backend")
    dep = make(client, pid, repo, "commit", role="frontend", requires=[base])
    assert [g["id"] for g in inbox(client, pid)["groups"]] == [base]
    assert withdraw(client, pid, "frontend", "commit", dep).status_code == 200
    ib = inbox(client, pid)
    assert ib["groups"] == [] and [c["id"] for c in ib["commits"]] == [base]  # a flat request again
    assert [m["id"] for m in workflow.commit_group(proj.db, base)] == [base]
    assert [m["id"] for m in workflow.commit_group(proj.db, dep)] == [dep]  # a withdrawn start is a group of one
    r = decide(client, pid, base)
    assert r.status_code == 200 and r.json()["state"] == "committed"
    assert proj.db.commit(dep)["state"] == "withdrawn"


def test_the_graph_leaves_withdrawn_requests_out():
    def c(i, state, requires=()):
        return {"id": i, "state": state, "note": None, "requires": list(requires)}
    base, mid, top = c(1, "master_review"), c(2, "withdrawn", [1]), c(3, "master_review", [2])
    byid = {x["id"]: x for x in (base, mid, top)}
    linked = [mid, top]
    assert [m["id"] for m in commit_graph.component(base, linked, byid.get)] == [1]
    assert [m["id"] for m in commit_graph.component(top, linked, byid.get)] == [3]
    assert [m["id"] for m in commit_graph.component(mid, linked, byid.get)] == [2]
    assert commit_graph.rejected_requirements([top], byid.get) == []  # withdrawn is not "rejected"


def test_a_new_request_cannot_require_a_withdrawn_one_and_a_refile_leaves_it_withdrawn(client, pid, repo):
    proj = proj_of(client, pid)
    old = make(client, pid, repo, "commit", role="backend", name="api.py")
    assert withdraw(client, pid, "backend", "commit", old, "wrong message").status_code == 200
    (repo / "ui.py").write_text("x\n")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": ["ui.py"], "message": "m", "requires": [old]},
                    headers=hdr(client, pid, "frontend"))
    assert r.status_code == 400 and r.json()["detail"] == f"requires #{old}: no such open commit request (it was withdrawn)"
    new = make(client, pid, repo, "commit", role="backend", name="api.py")  # the re-file takes nobody's place
    assert new != old and proj.db.commit(old)["state"] == "withdrawn" and proj.db.commit(old)["note"] == "wrong message"
    assert proj.db.commit(new)["state"] == "master_review"


# ---- approvals: the design gate ----------------------------------------------------------------------------------
def design(client, pid, title):
    r = client.post("/internal/approvals", json={"feature": "F-1", "title": title, **BRIEF}, headers=hdr(client, pid, LEAD))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_withdrawing_a_redesign_falls_back_to_the_previous_design_approval(client, pid, repo):
    proj = proj_of(client, pid)
    first = design(client, pid, "Design: Export")
    client.post(f"/api/p/{pid}/approvals/{first}/decide", json={"decision": "approve"})
    second = design(client, pid, "Design: Export v2")
    assert proj.db.design_info(1, 0) == {"state": "pending", "approval_id": second, "gated": True}
    events = []
    orig = proj.hub.broadcast

    async def spy(event, data):
        events.append((event, data))
        await orig(event, data)
    proj.hub.broadcast = spy
    assert withdraw(client, pid, LEAD, "approval", second, "redesign not needed").status_code == 200
    assert proj.db.design_info(1, 0) == {"state": "approved", "approval_id": first, "gated": True}
    feat = [d for e, d in events if e == "feature"]
    assert feat and feat[-1]["design"]["state"] == "approved"  # the Design chip is told
    assert [e for e, _ in events if e in ("approval", "inbox")] == ["approval", "inbox"]
    # commits flow again: the approved design counts, the withdrawn redesign doesn't block
    (repo / "x.py").write_text("x\n")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": ["x.py"], "message": "m"}, headers=hdr(client, pid, LEAD))
    assert r.status_code == 200, r.text


def test_withdrawing_the_only_design_request_leaves_the_feature_gated_and_not_requested(client, pid, repo):
    proj = proj_of(client, pid)
    only = design(client, pid, "Design: Export")
    assert withdraw(client, pid, LEAD, "approval", only).status_code == 200
    assert proj.db.design_info(1, 0) == {"state": "none", "approval_id": None, "gated": True}
    (repo / "x.py").write_text("x\n")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": ["x.py"], "message": "m"}, headers=hdr(client, pid, LEAD))
    assert r.status_code == 409 and "design isn't approved yet (not requested)" in r.json()["detail"]
    again = design(client, pid, "Design: Export")  # a fresh request starts over
    assert proj.db.design_info(1, 0)["approval_id"] == again


def test_a_withdrawn_design_is_not_the_latest_even_when_it_is_the_newest(client, pid, repo):
    proj = proj_of(client, pid)
    first = design(client, pid, "Design: Export")
    client.post(f"/api/p/{pid}/approvals/{first}/decide", json={"decision": "reject", "note": "no"})
    second = design(client, pid, "Design: Export again")
    withdraw(client, pid, LEAD, "approval", second)
    assert proj.db.design_info(1, 0)["state"] == "rejected" and proj.db.design_info(1, 0)["approval_id"] == first


# ---- nothing decides a withdrawn request ---------------------------------------------------------------------------
def test_a_withdrawn_request_cannot_be_decided_reviewed_or_answered(client, pid, repo):
    a = make(client, pid, repo, "approval")
    c = make(client, pid, repo, "commit", role="backend", approve=False)
    card = make(client, pid, repo, "card")
    for kind, rid, role in (("approval", a, "frontend"), ("commit", c, "backend"), ("card", card, "frontend")):
        assert withdraw(client, pid, role, kind, rid).status_code == 200
    r = client.post(f"/api/p/{pid}/approvals/{a}/decide", json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == "approval is withdrawn"
    assert decide(client, pid, c).json()["detail"] == f"commit #{c} is withdrawn"
    r = client.post(f"/api/p/{pid}/commits/{c}/decide", json={"decision": "reject"})
    assert r.status_code == 409 and r.json()["detail"] == f"commit #{c} is withdrawn"
    r = client.post(f"/internal/commits/{c}/review", json={"decision": "approve"}, headers=hdr(client, pid, LEAD))
    assert r.status_code == 409 and "is withdrawn" in r.json()["detail"]
    r = client.post(f"/api/p/{pid}/questions/{card}/answer", json={"answers": {"Which format?": "CSV"}})
    assert r.status_code == 409 and r.json()["detail"] == "question is withdrawn"


def test_state_filters_accept_withdrawn(client, pid, repo):
    a, c, card = (make(client, pid, repo, k, role="backend") for k in KINDS)
    keep = make(client, pid, repo, "approval", role="backend")
    for kind, rid in (("approval", a), ("commit", c), ("card", card)):
        assert withdraw(client, pid, "backend", kind, rid).status_code == 200
    assert [x["id"] for x in client.get(f"/api/p/{pid}/approvals", params={"state": "withdrawn"}).json()] == [a]
    assert [x["id"] for x in client.get(f"/api/p/{pid}/approvals").json()] == [keep]  # pending: the withdrawn one is gone
    assert [x["id"] for x in client.get(f"/api/p/{pid}/commits", params={"state": "withdrawn"}).json()] == [c]
    assert [x["id"] for x in client.get(f"/api/p/{pid}/questions", params={"state": "withdrawn"}).json()] == [card]
    withdrawn_card = client.get(f"/api/p/{pid}/questions", params={"state": "withdrawn"}).json()[0]
    assert withdrawn_card["note"] == "superseded by #99"
    assert client.get(f"/api/p/{pid}/approvals", params={"state": "nope"}).status_code == 400
    assert "withdrawn" in workflow.COMMIT_STATES


def test_the_frames_go_out_and_the_inbox_count_follows(client, pid, repo):
    proj = proj_of(client, pid)
    events = []
    orig = proj.hub.broadcast

    async def spy(event, data):
        events.append((event, data))
        await orig(event, data)
    proj.hub.broadcast = spy
    for kind in KINDS:
        rid = make(client, pid, repo, kind, role="backend")
        events.clear()
        assert withdraw(client, pid, "backend", kind, rid).status_code == 200
        frame = [d for e, d in events if e == FRAME[kind]]
        assert len(frame) == 1 and frame[0]["id"] == rid and frame[0]["state"] == "withdrawn"
        assert [d for e, d in events if e == "inbox"] == [{"count": inbox(client, pid)["count"]}]


def test_the_old_card_route_is_gone(client, pid, repo):
    card = make(client, pid, repo, "card", role="backend")
    r = client.post(f"/internal/questions/{card}/withdraw", headers=hdr(client, pid, "backend"))
    assert r.status_code == 404
    assert row(client, pid, "card", card)["state"] == "pending"
    assert not hasattr(workflow, "withdraw_question")
