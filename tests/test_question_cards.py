"""F-10 R1: non-blocking question cards (ask_master / withdraw / answer -> Decision), voices, and the blocking
AskUserQuestion path staying as it was."""
import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    return pid


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def turn(client, pid, role):
    return running_turn(proj_of(client, pid).db, role, pid=pid)[1]


def q(text, header="H", labels=("A", "B"), multi=False):
    return {"question": text, "header": header, "multiSelect": multi,
            "options": [{"label": l, "description": f"about {l}"} for l in labels]}


CARD = [q("Which format first?", "Format", ("CSV (Recommended)", "XLSX")),
        q("Which columns?", "Columns", ("Date", "Amount", "Customer"), multi=True)]


@pytest.mark.parametrize("questions,needle", [
    ([], "1 to 6"), ([q(f"Q{i}") for i in range(7)], "1 to 6"),
    ([q("Q", labels=("A",))], "2 to 4"), ([q("Q", labels=("A", "B", "C", "D", "E"))], "2 to 4"),
    ([q("Q", labels=("A", "A"))], "repeated"), ([q("Q", labels=("A", ""))], "empty"),
    ([q("Q", header="")], "header"), ([q("Q", header="much too long header")], "header"),
    ([q("Q"), q("Q")], "repeats"), ([q("Q", labels=("A", "Other"))], "Other"), ("nope", "1 to 6"),
])
def test_validation(client, pid, questions, needle):
    r = client.post("/internal/questions", json={"questions": questions, "feature": "F-1"}, headers=turn(client, pid, "product"))
    assert r.status_code == 400 and needle in r.json()["detail"], r.text


def test_post_card_message_inbox_once_and_answer_wakes_role(client, pid):
    proj = proj_of(client, pid)
    proj.db.x("DELETE FROM wake_queue")
    before = client.get(f"/api/p/{pid}/inbox").json()["count"]
    r = client.post("/internal/questions", json={"questions": CARD, "feature": "F-1", "purpose": "interview",
                                                 "intro": "My take: CSV first."}, headers=turn(client, pid, "product"))
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    card = proj.db.question(cid)
    assert (card["kind"], card["purpose"], card["state"], card["conversation_id"]) == ("card", "interview", "pending", "feature-1")
    msg = proj.db.message(card["message_id"])
    assert (msg["type"], msg["author"], msg["mentions"], msg["ref"]) == ("Question", "product", [], f"question:{cid}")
    assert msg["question"]["id"] == cid and "My take: CSV first." in msg["text"] and "- XLSX: about XLSX" in msg["text"]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert inbox["count"] == before + 1 and [x["id"] for x in inbox["questions"]] == [cid]
    assert all(m["id"] != msg["id"] for m in inbox["messages"])  # counted once, as a question
    assert proj.db.queued_wakes() == []  # posting wakes nobody
    # the Master answers: an option, and a multi-select mixing labels; then Other on a fresh card
    r = client.post(f"/api/p/{pid}/questions/{cid}/answer",
                    json={"answers": {"Which format first?": "CSV (Recommended)", "Which columns?": "Date, Amount"}})
    assert r.status_code == 200 and r.json()["state"] == "answered"
    dec = proj.db.messages("feature-1")[-1]
    assert (dec["author"], dec["type"], dec["mentions"]) == ("master", "Decision", ["product"])
    assert dec["text"] == (f"@product Answers to card #{cid}:\n- **Format**: CSV (Recommended)\n"
                           "- **Columns**: Date, Amount")
    assert [w["role"] for w in proj.db.queued_wakes()] == ["product"]
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == before
    assert client.post(f"/api/p/{pid}/questions/{cid}/answer", json={"answers": {}}).status_code == 409
    r = client.post("/internal/questions", json={"questions": CARD[:1], "feature": "F-1"}, headers=turn(client, pid, "lead"))
    c2 = r.json()["id"]
    client.post(f"/api/p/{pid}/questions/{c2}/answer", json={"answers": {"Which format first?": "JSON, please"}})
    assert proj.db.messages("feature-1")[-1]["text"].endswith("- **Format**: (Other) JSON, please")


def test_card_without_feature_goes_to_dm(client, pid):
    r = client.post("/internal/questions", json={"questions": CARD[:1]}, headers=turn(client, pid, "backend")).json()
    assert r["conversation_id"] == "dm-backend"


def test_withdraw_rules(client, pid):
    proj = proj_of(client, pid)
    cid = client.post("/internal/questions", json={"questions": CARD, "feature": "F-1"},
                      headers=turn(client, pid, "product")).json()["id"]
    assert client.post(f"/internal/questions/{cid}/withdraw", headers=turn(client, pid, "lead")).status_code == 403
    r = client.post(f"/internal/questions/{cid}/withdraw", headers=turn(client, pid, "product"))
    assert r.status_code == 200 and r.json()["state"] == "withdrawn"
    assert proj.db.messages("feature-1")[-1]["text"] == f"Card #{cid} withdrawn by product."
    assert client.post(f"/internal/questions/{cid}/withdraw", headers=turn(client, pid, "product")).status_code == 409
    assert client.post(f"/api/p/{pid}/questions/{cid}/answer", json={"answers": {}}).status_code == 409
    assert [x["id"] for x in client.get(f"/api/p/{pid}/questions", params={"state": "withdrawn"}).json()] == [cid]
    # answered cards can't be withdrawn
    c2 = client.post("/internal/questions", json={"questions": CARD, "feature": "F-1"},
                     headers=turn(client, pid, "product")).json()["id"]
    client.post(f"/api/p/{pid}/questions/{c2}/answer", json={"answers": {"Which format first?": "XLSX",
                                                                         "Which columns?": "Date"}})
    assert client.post(f"/internal/questions/{c2}/withdraw", headers=turn(client, pid, "product")).status_code == 409


def test_partial_answer_is_refused(client, pid):
    """QA (a) / Lead #451: every question needs a non-empty answer; nothing is stored or posted otherwise."""
    proj = proj_of(client, pid)
    cid = client.post("/internal/questions", json={"questions": CARD, "feature": "F-1"},
                      headers=turn(client, pid, "product")).json()["id"]
    proj.db.x("DELETE FROM wake_queue")
    n = len(proj.db.messages("feature-1"))
    for answers in ({}, {"Which format first?": "XLSX"}, {"Which format first?": "XLSX", "Which columns?": "  "}):
        r = client.post(f"/api/p/{pid}/questions/{cid}/answer", json={"answers": answers})
        assert r.status_code == 400 and "answer every question" in r.json()["detail"], r.text
    assert proj.db.question(cid)["state"] == "pending" and len(proj.db.messages("feature-1")) == n
    assert proj.db.queued_wakes() == []
    r = client.post(f"/api/p/{pid}/questions/{cid}/answer", json={"answers": {"Format": "XLSX", "Columns": "Date"}})
    assert r.status_code == 200  # keyed by header works too


def test_comma_in_multi_select_label_is_refused(client, pid):
    """QA (b) / Lead #451: multi-select answers are comma-joined, so their labels can't hold a comma."""
    hdr = turn(client, pid, "product")
    r = client.post("/internal/questions", json={"questions": [q("Q", labels=("Date, time", "Amount"), multi=True)]},
                    headers=hdr)
    assert r.status_code == 400 and "comma" in r.json()["detail"]
    r = client.post("/internal/questions", json={"questions": [q("Q", labels=("Yes, ship it", "No"))]}, headers=hdr)
    assert r.status_code == 200, r.text  # single-select answers aren't split


def test_voices_and_concern_gate(client, pid):
    proj = proj_of(client, pid)
    feat = proj.db.feature("F-1")
    assert workflow.voices(proj.db, feat) == ["lead", "product"]
    proj.db.add_message("feature-1", "system", "System", "@qa ignored: system lines give no voice", ["qa"], None)
    body = {"questions": CARD[:1], "feature": "F-1", "purpose": "concern", "intro": "I think X is wrong because Y."}
    r = client.post("/internal/questions", json=body, headers=turn(client, pid, "qa"))
    assert r.status_code == 403 and "no voice" in r.json()["detail"]
    proj.db.add_message("feature-1", "lead", "Message", "@frontend please build it", ["frontend"], None)
    proj.db.add_message("feature-1", "backend", "Message", "on it", [], None)
    assert workflow.voices(proj.db, feat) == ["lead", "product", "frontend", "backend"]
    assert "- voices: lead, product, frontend, backend" in workflow.feature_header(proj.db, proj.path, feat)
    r = client.post("/internal/questions", json=body, headers=turn(client, pid, "frontend"))
    assert r.status_code == 200, r.text
    assert proj.db.message(proj.db.question(r.json()["id"])["message_id"])["type"] == "Concern"
    assert client.post("/internal/questions", json={**body, "feature": None}, headers=turn(client, pid, "lead")).status_code == 400
    for purpose, code in (("brainstorm", 403), ("bogus", 400)):
        assert client.post("/internal/questions", json={**body, "purpose": purpose},
                           headers=turn(client, pid, "product")).status_code == code


def test_blocking_ask_user_question_still_expires(client, pid):
    """Regression: the held AskUserQuestion path is untouched (kind 'blocking', 600 s timer -> expired + deny)."""
    st = client.app.state.app
    st.settings["ask_timeout_s"] = 0.2
    proj = proj_of(client, pid)
    r = client.post("/internal/permission", json={"tool_name": "AskUserQuestion", "tool_use_id": "t1",
                                                  "input": {"questions": CARD}}, headers=turn(client, pid, "qa"))
    assert r.json()["behavior"] == "deny" and "did not answer" in r.json()["message"]
    q = proj.db.questions("expired")[0]
    assert q["kind"] == "blocking" and q["purpose"] is None and q["message_id"] is None


def test_other_text_with_mentions_wakes_only_the_asker(client, pid):
    """QA C4: the Decision's mentions come from the card, not from parsing the Master's Other text."""
    proj = proj_of(client, pid)
    cid = client.post("/internal/questions", json={"questions": CARD[:1], "feature": "F-1"},
                      headers=turn(client, pid, "product")).json()["id"]
    proj.db.x("DELETE FROM wake_queue")
    client.post(f"/api/p/{pid}/questions/{cid}/answer",
                json={"answers": {"Which format first?": "ask @backend and @qa\nthen decide"}})
    dec = proj.db.messages("feature-1")[-1]
    assert dec["mentions"] == ["product"] and [w["role"] for w in proj.db.queued_wakes()] == ["product"]
    assert "(Other) ask @backend and @qa\nthen decide" in dec["text"]


def test_pending_card_survives_a_restart(home, repo):
    """QA B2/B3: cards have no timer and nothing expires them when the daemon starts again."""
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        pid = c.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
        c.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
        hdr = running_turn(c.app.state.app.projects[pid].db, "product", pid=pid)[1]
        cid = c.post("/internal/questions", json={"questions": CARD, "feature": "F-1"}, headers=hdr).json()["id"]
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        assert [q["id"] for q in c.get(f"/api/p/{pid}/inbox").json()["questions"]] == [cid]
        r = c.post(f"/api/p/{pid}/questions/{cid}/answer", json={"answers": {"Which format first?": "XLSX",
                                                                             "Which columns?": "Amount"}})
        assert r.status_code == 200 and r.json()["state"] == "answered"
