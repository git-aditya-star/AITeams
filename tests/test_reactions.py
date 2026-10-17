"""F-6 reactions: db toggle/grouping, the internal endpoint (broadcast, no wake/inbox), messages and the digest."""
import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from aiteams.db import EMOJI
from aiteams.orchestrator.prompt import _fmt
from tests.conftest import AUTH, running_turn

THUMBS, HEART, PARTY = EMOJI[0], EMOJI[1], EMOJI[3]
# QA A5: only exact EMOJI strings count (bare heart without U+FE0F, skin tone, shortcode, empty, two emoji, padding)
NOT_EMOJI = ("❤", "\U0001F44D\U0001F3FD", ":+1:", "", "\U0001F44D\U0001F389", " \U0001F44D")


def test_heart_keeps_its_variation_selector():
    assert HEART == "❤️" and len(EMOJI) == 8 == len(set(EMOJI))


def test_toggle_groups_in_emoji_order_and_rejects_others(db):
    m = db.add_message("general", "master", "Message", "ship it", [], None)
    other = db.add_message("general", "lead", "Message", "x", [], None)
    assert db.toggle_reaction(m["id"], "qa", PARTY) is True
    assert db.toggle_reaction(m["id"], "lead", THUMBS) is True
    assert db.toggle_reaction(m["id"], "qa", THUMBS) is True
    assert db.reactions_for([m["id"], other["id"]]) == {
        m["id"]: [{"emoji": THUMBS, "roles": ["lead", "qa"]}, {"emoji": PARTY, "roles": ["qa"]}]}
    assert db.toggle_reaction(m["id"], "lead", THUMBS) is False  # second call removes
    assert db.reactions_for([m["id"]])[m["id"]][0] == {"emoji": THUMBS, "roles": ["qa"]}
    for bad in NOT_EMOJI:
        with pytest.raises(ValueError):
            db.toggle_reaction(m["id"], "qa", bad)
    # attached on every message read path
    assert db.message(m["id"])["reactions"] == db.messages("general")[-2]["reactions"] != []
    assert db.messages_by_ids([m["id"], other["id"]])[1]["reactions"] == []


def test_fmt_has_id_and_reaction_suffix(db):
    m = db.add_message("general", "master", "Message", "ship it", [], None)
    assert f"#{m['id']} master (Message): ship it" in _fmt(db.message(m["id"]))
    db.toggle_reaction(m["id"], "lead", THUMBS)
    db.toggle_reaction(m["id"], "qa", THUMBS)
    db.toggle_reaction(m["id"], "frontend", PARTY)
    assert _fmt(db.message(m["id"])).endswith(f"ship it  [{THUMBS} lead, qa · {PARTY} frontend]")


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


def test_endpoint_toggles_broadcasts_and_wakes_nobody(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    proj = client.app.state.app.projects[pid]
    m = client.post(f"/api/p/{pid}/messages", json={"conversation": "general", "text": "@lead done?"}).json()
    proj.db.x("DELETE FROM wake_queue")
    st = client.app.state.app
    notified = []
    if st.scheduler:
        st.scheduler.notify = lambda *a, **k: notified.append(1)

    def side_tables():
        return [[dict(r) for r in proj.db.q(f"SELECT * FROM {t}")] for t in ("receipts", "kv", "resolved")]
    tables_before = side_tables()
    msgs_before = proj.db.q("SELECT COUNT(*) c FROM messages")[0]["c"]
    acts_before = len(proj.db.activity())
    inbox_before = client.get(f"/api/p/{pid}/inbox").json()["count"]
    _, hdr = running_turn(proj.db, "qa", pid=pid)
    events = []
    orig = proj.hub.broadcast

    async def spy(event, data):
        events.append((event, data))
        await orig(event, data)
    proj.hub.broadcast = spy
    r = client.post("/internal/reactions", json={"message_id": m["id"], "emoji": HEART}, headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json() == {"message_id": m["id"], "added": True, "reactions": [{"emoji": HEART, "roles": ["qa"]}]}
    assert events == [("reaction", {"message_id": m["id"], "reactions": [{"emoji": HEART, "roles": ["qa"]}]})]
    assert proj.db.q("SELECT COUNT(*) c FROM wake_queue")[0]["c"] == 0
    assert proj.db.q("SELECT COUNT(*) c FROM messages")[0]["c"] == msgs_before
    assert len(proj.db.activity()) == acts_before
    assert client.get(f"/api/p/{pid}/inbox").json()["count"] == inbox_before
    assert side_tables() == tables_before and notified == []
    # public messages and internal get_messages carry it
    pub = client.get(f"/api/p/{pid}/messages", params={"conversation": "general"}).json()
    assert next(x for x in pub if x["id"] == m["id"])["reactions"] == [{"emoji": HEART, "roles": ["qa"]}]
    got = client.get("/internal/messages", params={"conversation": "general"}, headers=hdr).json()
    assert next(x for x in got if x["id"] == m["id"])["reactions"] == [{"emoji": HEART, "roles": ["qa"]}]
    # toggle off, bad emoji, unknown message, no turn
    r = client.post("/internal/reactions", json={"message_id": m["id"], "emoji": HEART}, headers=hdr).json()
    assert r["added"] is False and r["reactions"] == [] and events[-1][1]["reactions"] == []
    for bad in NOT_EMOJI:
        assert client.post("/internal/reactions", json={"message_id": m["id"], "emoji": bad}, headers=hdr).status_code == 400
    # any existing message is fair game, including another role's DM (Lead's B7 decision)
    dm = proj.db.add_message("dm-frontend", "master", "Message", "for frontend", [], None)
    assert client.post("/internal/reactions", json={"message_id": dm["id"], "emoji": THUMBS}, headers=hdr).json()["added"]
    assert client.post("/internal/reactions", json={"message_id": 99999, "emoji": THUMBS}, headers=hdr).status_code == 404
    assert client.post("/internal/reactions", json={"message_id": m["id"], "emoji": THUMBS}).status_code == 403


def test_every_digest_path_shows_reactions(db):
    """QA B6: the wake digest (messages_by_ids), the focus conversation (messages) and the standup compile digest."""
    from aiteams.orchestrator.prompt import build_compile_prompt, build_wake_prompt
    m = db.add_message("general", "master", "Message", "@backend go", ["backend"], None)
    db.toggle_reaction(m["id"], "lead", THUMBS)
    line = f"#{m['id']} master (Message) @backend: @backend go  [{THUMBS} lead]"
    assert build_wake_prompt(db, "backend", [m["id"]]).count(line) == 2  # digest section + focus conversation
    su = db.create_standup(["qa"], "master")
    r = db.add_message("standup", "qa", "Message", "Done: x", [], None)
    db.toggle_reaction(r["id"], "lead", PARTY)
    assert f"Done: x  [{PARTY} lead]" in build_compile_prompt(db, db.project_path, su, [], [])
