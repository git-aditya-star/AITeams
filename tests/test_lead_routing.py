"""F-22 B2: a Lead mention in a feature means the feature's Lead, voices follow it, the Lead literals cover both Leads,
a Lead acts only in its own features (409 otherwise, a Lead-less feature is claimed), and the review script's overlap."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, BRIEF, running_turn

SENTENCE = "{key} belongs to {name}; you can read it but not act in it. Ask @manager to reassign it if needed."


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


def hdr(client, pid, role):
    return running_turn(proj_of(client, pid).db, role, pid=pid)[1]


def feature(client, pid, lead="lead", title="Export"):
    """A feature (F-<n>) with its Lead already set (None = no Lead yet); returns its number."""
    f = client.post(f"/api/p/{pid}/features", json={"text": title, "starter": "product"}).json()
    proj_of(client, pid).db.update_feature(f["id"], lead=lead)
    return f["id"]


def say(client, pid, role, conv, text, type_="Message"):
    return client.post("/internal/messages", json={"conversation": conv, "text": text, "type": type_},
                       headers=hdr(client, pid, role))


def last(client, pid, conv, n=1):
    return proj_of(client, pid).db.messages(conv, limit=n)[-n]


def wakes(client, pid):
    return sorted(w["role"] for w in proj_of(client, pid).db.queued_wakes())


def lead_of(client, pid, n=1):
    return proj_of(client, pid).db.feature(n)["lead"]


def commit(client, pid, role, name="a.py", feature_key="F-1", message="add"):
    (Path(proj_of(client, pid).path) / name).write_text(f"{name}\n")
    body = {"files": [name], "message": message} | ({"feature": feature_key} if feature_key else {})
    return client.post("/internal/commits", json=body, headers=hdr(client, pid, role))


# ---- routing -----------------------------------------------------------------------------
@pytest.mark.parametrize("owner,other", [("lead", "lead-2"), ("lead-2", "lead")])
def test_a_lead_mention_in_a_feature_wakes_only_its_lead(client, pid, owner, other):
    n = feature(client, pid, owner)
    proj_of(client, pid).db.x("DELETE FROM wake_queue")
    for text in ("@lead please look", "@lead-1 please look", "@lead-2 please look", "@lead @lead-2 both?"):
        r = say(client, pid, "backend", f"feature-{n}", text)
        assert r.status_code == 200, r.text
        m = last(client, pid, f"feature-{n}")
        assert m["mentions"] == [owner] and m["text"] == text, text  # the resolved role is stored, the text isn't touched
        assert wakes(client, pid) == [owner]
    assert other not in wakes(client, pid)


def test_mentions_outside_a_feature_are_not_resolved(client, pid):
    proj_of(client, pid).db.x("DELETE FROM wake_queue")
    assert say(client, pid, "backend", "general", "@lead-2 and @lead").status_code == 200
    assert last(client, pid, "general")["mentions"] == ["lead-2", "lead"]
    assert wakes(client, pid) == ["lead", "lead-2"]
    # other roles' mentions in a feature are untouched; a message with no Lead mention picks no Lead
    n = feature(client, pid, None)
    assert say(client, pid, "backend", f"feature-{n}", "@qa look").status_code == 200
    assert last(client, pid, f"feature-{n}")["mentions"] == ["qa"] and lead_of(client, pid, n) is None


def test_a_feature_without_a_lead_gets_one_after_the_message_that_needs_it(client, pid):
    n = feature(client, pid, None)
    assert say(client, pid, "backend", f"feature-{n}", "@lead please look").status_code == 200
    msgs = proj_of(client, pid).db.messages(f"feature-{n}", limit=3)
    assert msgs[-2]["author"] == "backend" and msgs[-2]["mentions"] == ["lead"]  # the message first,
    assert msgs[-1]["author"] == "system" and msgs[-1]["text"] == "Lead 1 picked automatically (default)."  # then the pick
    assert lead_of(client, pid, n) == "lead"


def test_the_master_starting_a_task_with_at_lead_gets_the_feature_lead(client, pid):
    proj_of(client, pid).db.x("DELETE FROM wake_queue")
    f = client.post(f"/api/p/{pid}/features", json={"text": "@lead fix the typo", "starter": "lead"}).json()
    msgs = proj_of(client, pid).db.messages(f["conversation_id"], limit=5)
    assert msgs[0]["author"] == "master" and msgs[0]["text"] == "@lead fix the typo"  # the request stays first
    assert msgs[0]["mentions"] == ["lead"] and wakes(client, pid) == ["lead"]


def test_a_commit_request_and_its_decisions_wake_the_features_lead(client, pid, repo):
    n = feature(client, pid, "lead-2")
    db = proj_of(client, pid).db
    db.x("DELETE FROM wake_queue")
    c = commit(client, pid, "backend").json()
    card = last(client, pid, f"feature-{n}")
    assert card["type"] == "Commit" and card["mentions"] == ["lead-2"]  # was ["lead"] before B2
    assert wakes(client, pid) == ["lead-2"]
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"},
                       headers=hdr(client, pid, "lead-2")).status_code == 200
    assert last(client, pid, f"feature-{n}")["text"] == f"Lead 2 approved commit #{c['id']}; waiting on the Master."
    db.x("DELETE FROM wake_queue")
    r = client.post(f"/api/p/{pid}/commits/{c['id']}/decide", json={"decision": "approve"}).json()
    assert r["state"] == "committed"
    line = last(client, pid, f"feature-{n}")
    assert line["author"] == "master" and line["text"].startswith("@lead Committed") and line["mentions"] == ["lead-2"]
    assert wakes(client, pid) == ["lead-2"]  # F-20: only the Lead, and it's this feature's Lead
    # a rejection wakes the filer and the feature's Lead
    c2 = commit(client, pid, "backend", "b.py").json()
    client.post(f"/internal/commits/{c2['id']}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead-2"))
    db.x("DELETE FROM wake_queue")
    client.post(f"/api/p/{pid}/commits/{c2['id']}/decide", json={"decision": "reject", "note": "no"})
    assert last(client, pid, f"feature-{n}")["mentions"] == ["backend", "lead-2"] and wakes(client, pid) == ["backend", "lead-2"]


def test_a_featureless_request_goes_to_lead_1_and_a_leads_own_goes_to_the_master(client, pid):
    db = proj_of(client, pid).db
    db.x("DELETE FROM wake_queue")
    c = commit(client, pid, "backend", feature_key=None).json()
    assert c["state"] == "lead_review" and last(client, pid, "dm-backend")["mentions"] == ["lead"]
    assert wakes(client, pid) == ["lead"]
    for role in ("lead", "lead-2"):  # a Lead's own request needs no Lead review
        r = commit(client, pid, role, f"own_{role}.py", feature_key=None).json()
        assert r["state"] == "master_review", role


def test_a_lead_2_can_review_in_its_feature_and_a_rejection_is_authored_by_it(client, pid):
    n = feature(client, pid, "lead-2")
    c = commit(client, pid, "backend").json()
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "reject", "note": "redo"},
                       headers=hdr(client, pid, "lead-2")).status_code == 200
    m = last(client, pid, f"feature-{n}")
    assert m["author"] == "lead-2" and m["type"] == "Decision" and m["text"] == f"@backend Commit #{c['id']} rejected: redo"
    assert proj_of(client, pid).db.commit(c["id"])["state"] == "rejected"
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"},
                       headers=hdr(client, pid, "backend")).status_code == 403
    assert client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"},
                       headers=hdr(client, pid, "backend")).json()["detail"] == "only a Lead reviews commits"


# ---- voices, waits_on, news ---------------------------------------------------------------
def test_voices_use_the_features_lead(client, pid):
    db = proj_of(client, pid).db
    feature(client, pid, "lead-2")
    feature(client, pid, "lead", "Other")
    feature(client, pid, None, "Unassigned")
    assert workflow.voices(db, db.feature(1)) == ["lead-2", "product"]
    assert workflow.voices(db, db.feature(2)) == ["lead", "product"]
    assert workflow.voices(db, db.feature(3)) == ["lead", "product"]  # no Lead yet: Lead 1, as before
    say(client, pid, "backend", "feature-1", "hi")
    assert workflow.voices(db, db.feature(1)) == ["lead-2", "product", "backend"]


def test_a_brainstorm_wakes_only_the_features_lead(client, pid):
    n = feature(client, pid, "lead-2")
    db = proj_of(client, pid).db
    db.x("DELETE FROM wake_queue")
    r = client.post("/internal/brainstorm", json={"feature": f"F-{n}", "topic": "pools?"}, headers=hdr(client, pid, "product"))
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == ["lead-2", "product"]  # the feature's Lead and Product, not Lead 1
    assert wakes(client, pid) == ["lead-2", "product"]


def test_waits_on_names_the_features_lead(client, pid):
    feature(client, pid, "lead-2")
    feature(client, pid, None, "Unassigned")
    commit(client, pid, "backend")
    commit(client, pid, "backend", "b.py", "F-2")
    feats = {f["key"]: f for f in client.get(f"/api/p/{pid}/dashboard").json()["features"]}
    assert feats["F-1"]["waits_on"]["who"] == "lead-2" and feats["F-1"]["waits_on"]["kind"] == "review"
    assert feats["F-2"]["waits_on"]["who"] == "lead"  # no Lead yet: Lead 1


def test_a_leads_news_follows_the_commit_events_of_its_own_features(client, pid):
    feature(client, pid, "lead")
    feature(client, pid, "lead-2", "Mine")
    db = proj_of(client, pid).db
    c1 = commit(client, pid, "backend", "a.py", "F-1").json()
    c2 = commit(client, pid, "backend", "b.py", "F-2").json()
    c3 = commit(client, pid, "backend", "c.py", feature_key=None).json()
    t2, h2 = running_turn(db, "lead-2", pid=pid)
    db.add_message("feature-1", "system", "System", "x", [], None, f"commit_event:{c1['id']}")
    db.add_message("feature-2", "system", "System", "y", [], None, f"commit_event:{c2['id']}")
    db.add_message("dm-backend", "system", "System", "z", [], None, f"commit_event:{c3['id']}")
    proj_of(client, pid).delivered[t2["id"]] = db.max_message_id() - 3
    relevant, others, _ = workflow._news(db, "lead-2", db.max_message_id() - 3, t2["id"])
    assert [m["text"] for m in relevant if m["author"] == "system"] == ["y", "z"]  # its own feature + featureless
    assert [m["text"] for m in others if m["author"] == "system"] == ["x"]  # Lead 1's feature: just counted


# ---- unlock ---------------------------------------------------------------------------------
def test_lead_2_and_the_manager_may_force_unlock(client, pid):
    db = proj_of(client, pid).db
    for role, ok in (("lead-2", True), ("manager", True), ("qa", False)):
        db.x("DELETE FROM locks")
        db.acquire_lock("src/a.py", "backend", 1)
        r = client.post("/internal/locks/release", json={"path": "src/a.py", "force": True}, headers=hdr(client, pid, role))
        assert (r.status_code == 200) is ok, (role, r.text)
        if not ok:
            assert r.json()["detail"] == "src/a.py is locked by backend; only a Lead or the manager may force-unlock it"


# ---- the guards -------------------------------------------------------------------------------
@pytest.mark.parametrize("who,owner", [("lead-2", "lead"), ("lead", "lead-2")])
def test_the_other_lead_is_refused_everywhere_in_a_feature_and_nothing_changes(client, pid, who, owner):
    n = feature(client, pid, owner)
    db = proj_of(client, pid).db
    c = commit(client, pid, "backend").json()
    before = (len(db.messages(f"feature-{n}", limit=200)), len(db.approvals("all")), len(db.questions("pending")))
    want = SENTENCE.format(key=f"F-{n}", name="Lead 1" if owner == "lead" else "Lead 2")
    card = {"questions": [{"question": "Where?", "header": "Where", "options": [{"label": "A"}, {"label": "B"}]}],
            "feature": f"F-{n}"}
    for r in (say(client, pid, who, f"feature-{n}", "hello"),
              say(client, pid, who, f"feature-{n}", "decided", "Decision"),
              client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=hdr(client, pid, who)),
              client.patch(f"/internal/features/F-{n}", json={"phase": "build"}, headers=hdr(client, pid, who)),
              client.post("/internal/approvals", json={"feature": f"F-{n}", "title": "Design: x", **BRIEF},
                          headers=hdr(client, pid, who)),
              client.post("/internal/approvals", json={"feature": f"F-{n}", "title": "  design: y", **BRIEF},
                          headers=hdr(client, pid, who)),
              client.post("/internal/questions", json=card, headers=hdr(client, pid, who)),
              client.post("/internal/questions", json=card | {"purpose": "a_vs_b"}, headers=hdr(client, pid, who))):
        assert r.status_code == 409 and r.json()["detail"] == want, (r.request.url, r.text)
    after = (len(db.messages(f"feature-{n}", limit=200)), len(db.approvals("all")), len(db.questions("pending")))
    assert after == before and db.commit(c["id"])["state"] == "lead_review" and db.feature(n)["phase"] == "intake"
    assert lead_of(client, pid, n) == owner


def test_reading_is_never_guarded_and_other_approvals_and_places_are_open(client, pid):
    n = feature(client, pid, "lead")
    who = hdr(client, pid, "lead-2")
    assert client.get(f"/internal/features/F-{n}", headers=who).status_code == 200
    assert client.get("/internal/messages", params={"conversation": f"feature-{n}"}, headers=who).status_code == 200
    assert client.get("/internal/features", headers=who).status_code == 200
    # a non-Design approval and a Design request in a different place are not the guarded case
    assert client.post("/internal/approvals", json={"feature": f"F-{n}", "title": "Scope lock: x", **BRIEF},
                       headers=who).status_code == 200
    assert say(client, pid, "lead-2", "general", "hi").status_code == 200
    assert say(client, pid, "lead-2", "dm-lead", "hi Lead 1").status_code == 200
    # the owning Lead and every other role are unaffected
    assert say(client, pid, "lead", f"feature-{n}", "mine").status_code == 200
    assert say(client, pid, "backend", f"feature-{n}", "mine too").status_code == 200
    assert client.patch(f"/internal/features/F-{n}", json={"phase": "spec"}, headers=hdr(client, pid, "lead")).status_code == 200
    assert client.patch(f"/internal/features/F-{n}", json={"phase": "build"}, headers=hdr(client, pid, "product")).status_code == 200


def test_the_owning_lead_may_request_a_design_approval_and_ask(client, pid):
    n = feature(client, pid, "lead-2")
    who = hdr(client, pid, "lead-2")
    r = client.post("/internal/approvals", json={"feature": f"F-{n}", "title": "Design: x", **BRIEF}, headers=who)
    assert r.status_code == 200, r.text
    card = {"questions": [{"question": "Where?", "header": "Where", "options": [{"label": "A"}, {"label": "B"}]}],
            "feature": f"F-{n}", "purpose": "a_vs_b"}
    assert client.post("/internal/questions", json=card, headers=hdr(client, pid, "lead-2")).status_code == 200
    # Lead 1 is not an a_vs_b owner of a Lead 2 feature (and is refused before that check)
    assert client.post("/internal/questions", json=card, headers=hdr(client, pid, "lead")).status_code == 409


def test_a_feature_without_a_lead_is_claimed_by_the_first_lead_that_acts(client, pid):
    n = feature(client, pid, None)
    db = proj_of(client, pid).db
    assert say(client, pid, "lead-2", f"feature-{n}", "taking this").status_code == 200
    assert lead_of(client, pid, n) == "lead-2"
    msgs = db.messages(f"feature-{n}", limit=4)
    assert msgs[-2]["author"] == "system" and msgs[-2]["text"] == f"Lead 2 claimed F-{n} (it had no Lead yet)."
    assert msgs[-1]["author"] == "lead-2" and msgs[-1]["text"] == "taking this"  # claimed, then its message
    assert say(client, pid, "lead", f"feature-{n}", "me too").status_code == 409  # Lead 1 is now refused
    # each acting route claims
    for i, (route, role) in enumerate((("review", "lead"), ("set_feature", "lead-2"), ("design", "lead"),
                                       ("ask", "lead-2"))):
        k = feature(client, pid, None, f"Claim {i}")
        if route == "review":
            c = commit(client, pid, "backend", f"r{i}.py", f"F-{k}").json()
            r = client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=hdr(client, pid, role))
        elif route == "set_feature":
            r = client.patch(f"/internal/features/F-{k}", json={"phase": "spec"}, headers=hdr(client, pid, role))
        elif route == "design":
            r = client.post("/internal/approvals", json={"feature": f"F-{k}", "title": "Design: x", **BRIEF},
                            headers=hdr(client, pid, role))
        else:
            card = {"questions": [{"question": "Where?", "header": "Where", "options": [{"label": "A"}, {"label": "B"}]}],
                    "feature": f"F-{k}"}
            r = client.post("/internal/questions", json=card, headers=hdr(client, pid, role))
        assert r.status_code == 200, (route, r.text)
        assert lead_of(client, pid, k) == role, route


def test_a_refused_decision_claims_nothing_until_the_retry(client, pid):
    """The F-16 refuse-once guard comes before the claim: a refusal changes nothing, the retry claims and posts."""
    n = feature(client, pid, None)
    db = proj_of(client, pid).db
    t, h = running_turn(db, "lead", pid=pid)
    proj_of(client, pid).delivered[t["id"]] = db.max_message_id()
    db.add_message(f"feature-{n}", "frontend", "Message", "wait, #34 was superseded", [], None)
    body = {"conversation": f"feature-{n}", "text": "@master commit #34", "type": "Decision"}
    count = len(db.messages(f"feature-{n}", limit=200))
    r = client.post("/internal/messages", json=body, headers=h)
    assert r.status_code == 409 and "Not done" in r.json()["detail"]["guard"]
    assert lead_of(client, pid, n) is None and len(db.messages(f"feature-{n}", limit=200)) == count
    assert client.post("/internal/messages", json=body, headers=h).status_code == 200
    assert lead_of(client, pid, n) == "lead"


def test_a_lead_cannot_set_the_lead_and_a_refusal_claims_nothing(client, pid):
    n = feature(client, pid, None)
    r = client.patch(f"/internal/features/F-{n}", json={"lead": "lead-2"}, headers=hdr(client, pid, "lead"))
    assert r.status_code == 403 and r.json()["detail"] == "only the Manager assigns a feature's Lead"
    assert lead_of(client, pid, n) is None  # the 403 came before any claim


def test_a_reassignment_moves_who_may_act(client, pid):
    n = feature(client, pid, "lead")
    assert say(client, pid, "lead-2", f"feature-{n}", "x").status_code == 409
    assert client.patch(f"/internal/features/F-{n}", json={"lead": "lead-2"}, headers=hdr(client, pid, "manager")).status_code == 200
    assert say(client, pid, "lead-2", f"feature-{n}", "x").status_code == 200
    assert say(client, pid, "lead", f"feature-{n}", "x").status_code == 409


def test_a_manager_or_master_post_is_never_guarded(client, pid):
    n = feature(client, pid, "lead")
    assert say(client, pid, "manager", f"feature-{n}", "@lead-2 take a look").status_code == 200
    m = last(client, pid, f"feature-{n}")
    assert m["mentions"] == ["lead"]  # even an explicit @lead-2 means the feature's Lead
    assert client.post(f"/api/p/{pid}/messages", json={"conversation": f"feature-{n}", "text": "@lead-2 hello"}).status_code == 200
    assert last(client, pid, f"feature-{n}")["mentions"] == ["lead"]


# ---- review_tree's overlap -----------------------------------------------------------------
def test_the_files_answer_lists_other_open_requests_that_share_a_file(client, pid):
    feature(client, pid, "lead")
    feature(client, pid, "lead-2", "Other")
    feature(client, pid, None, "Unassigned")
    mine = commit(client, pid, "backend", "db.py", "F-1").json()
    other = commit(client, pid, "frontend", "db.py", "F-2").json()
    far = commit(client, pid, "qa", "x.py", "F-3").json()
    nofeat = commit(client, pid, "backend", "db.py", feature_key=None).json()
    assert {mine["id"], other["id"], far["id"], nofeat["id"]} and len({mine["id"], other["id"], far["id"], nofeat["id"]}) == 4
    r = client.get(f"/internal/commits/{mine['id']}/files", headers=hdr(client, pid, "lead")).json()
    assert r["ids"] == [mine["id"]] and r["files"] == ["db.py"]
    assert r["overlap"] == [
        {"commit_id": other["id"], "role": "frontend", "state": "lead_review", "files": ["db.py"], "feature": "F-2",
         "lead": "lead-2", "lead_name": "Lead 2"},
        {"commit_id": nofeat["id"], "role": "backend", "state": "lead_review", "files": ["db.py"], "feature": None,
         "lead": None, "lead_name": None}]
    # the Unassigned feature's request doesn't share a file; its own answer shows no overlap with the others
    assert client.get(f"/internal/commits/{far['id']}/files", headers=hdr(client, pid, "lead")).json()["overlap"] == []
    # a committed or rejected request is no longer open, so it isn't an overlap
    proj_of(client, pid).db.update_commit(other["id"], state="rejected")
    r = client.get(f"/internal/commits/{mine['id']}/files", headers=hdr(client, pid, "lead")).json()
    assert [o["commit_id"] for o in r["overlap"]] == [nofeat["id"]]


def test_a_group_overlap_skips_its_own_members(client, pid):
    feature(client, pid, "lead")
    base = commit(client, pid, "backend", "api.py").json()
    (Path(proj_of(client, pid).path) / "ui.ts").write_text("ui\n")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": ["api.py", "ui.ts"], "message": "ui",
                                               "requires": [base["id"]]}, headers=hdr(client, pid, "frontend"))
    assert r.status_code == 200, r.text
    ui = r.json()
    stranger = commit(client, pid, "qa", "ui.ts", feature_key=None).json()
    assert stranger["id"] not in (base["id"], ui["id"])
    r = client.get(f"/internal/commits/{ui['id']}/files", headers=hdr(client, pid, "lead")).json()
    assert r["ids"] == [base["id"], ui["id"]]
    assert [o["commit_id"] for o in r["overlap"]] == [stranger["id"]] and r["overlap"][0]["files"] == ["ui.ts"]
