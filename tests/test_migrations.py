"""PRAGMA user_version migrations: fresh DB, M1c-shaped DB, schema ahead (read-only), run once."""
import sqlite3

from aiteams.db import MIGRATIONS, SCHEMA, ProjectDB


def cols(db, table):
    return [r["name"] for r in db.q(f"PRAGMA table_info({table})")]


def test_fresh_db_lands_at_latest(db):
    assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS) == 8
    assert db.applied == [1, 2, 3, 4, 5, 6, 7, 8] and db.schema_ahead is False
    assert "token" in cols(db, "turns") and db.get_kv("schema_version") is None
    t = db.create_turn("qa", "s", "master", "general", token="abc")
    assert db.turn(t["id"])["token"] == "abc"


def test_m1c_shaped_db_migrates(repo):
    d = repo / ".aiteams"
    d.mkdir()
    conn = sqlite3.connect(str(d / "state.db"))
    conn.executescript(SCHEMA)  # today's tables, no token column, user_version 0, kv schema_version like M1c wrote it
    conn.execute("INSERT INTO kv VALUES('schema_version', '3')")
    conn.execute("INSERT INTO sessions(role, session_id, created_at, reason, active, seq) VALUES('qa','s1',1.0,'first turn',1,NULL)")
    conn.commit()
    conn.close()
    db = ProjectDB(str(repo))
    try:
        assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS) and db.applied == [1, 2, 3, 4, 5, 6, 7, 8]
        assert "token" in cols(db, "turns") and db.get_kv("schema_version") is None
        assert db.session_info("qa")["seq"] == 1  # the v3 body still ran (seq backfill)
        assert db.conversation("standup")["kind"] == "channel"
    finally:
        db.close()


def test_schema_ahead_opens_read_only(repo):
    d = repo / ".aiteams"
    d.mkdir()
    conn = sqlite3.connect(str(d / "state.db"))
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()
    db = ProjectDB(str(repo))
    try:
        assert db.schema_ahead is True and db.applied == []
        assert db.one("PRAGMA user_version")[0] == 99  # never downgraded, nothing seeded
        assert db.conversations() == []
    finally:
        db.close()


def test_migrations_run_once(repo):
    db = ProjectDB(str(repo))
    assert db.applied == [1, 2, 3, 4, 5, 6, 7, 8]
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [] and db.one("PRAGMA user_version")[0] == len(MIGRATIONS)
    finally:
        db.close()


def test_v2_db_gets_reactions_table(repo):
    """F-6: migration 3 applies on a database that is at user_version 2."""
    db = ProjectDB(str(repo))
    m = db.add_message("general", "lead", "Message", "hi", [], None)
    db.conn.execute("DROP TABLE reactions")
    db.conn.execute("CREATE TABLE q2 AS SELECT id, turn_id, role, tool_use_id, payload, state, answers, created_at,"
                    " answered_at FROM questions")
    db.conn.execute("DROP TABLE questions")
    db.conn.execute("ALTER TABLE q2 RENAME TO questions")
    db.conn.execute("DROP TABLE brainstorms")
    db.conn.execute("ALTER TABLE commits DROP COLUMN requires")
    db.conn.execute("ALTER TABLE features DROP COLUMN done_at")
    db.conn.execute("PRAGMA user_version=2")
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [3, 4, 5, 6, 7, 8] and cols(db, "reactions") == ["message_id", "role", "emoji", "created_at"]
        assert db.message(m["id"])["text"] == "hi" and db.message(m["id"])["reactions"] == []
    finally:
        db.close()


def test_v3_db_gets_question_card_columns(repo):
    """F-10: migration 4 adds the card columns; an existing (blocking) question reads as kind 'blocking'."""
    db = ProjectDB(str(repo))
    t = db.create_turn("qa", "s", "master", "dm-qa")
    old = db.create_question(t["id"], "qa", "tu1", {"questions": []})
    db.conn.execute("CREATE TABLE q2 AS SELECT id, turn_id, role, tool_use_id, payload, state, answers, created_at,"
                    " answered_at FROM questions")
    db.conn.execute("DROP TABLE questions")
    db.conn.execute("ALTER TABLE q2 RENAME TO questions")
    db.conn.execute("DROP TABLE brainstorms")
    db.conn.execute("ALTER TABLE commits DROP COLUMN requires")
    db.conn.execute("ALTER TABLE features DROP COLUMN done_at")
    db.conn.execute("PRAGMA user_version=3")
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [4, 5, 6, 7, 8]
        assert cols(db, "questions")[-6:] == ["kind", "purpose", "intro", "feature_id", "conversation_id", "message_id"]
        q = db.question(old["id"])
        assert (q["kind"], q["purpose"], q["state"]) == ("blocking", None, "pending")
    finally:
        db.close()


def test_v4_db_gets_brainstorms_table(repo):
    """F-10 R2: migration 5 creates `brainstorms` on a database at user_version 4."""
    db = ProjectDB(str(repo))
    db.conn.execute("DROP TABLE brainstorms")
    db.conn.execute("ALTER TABLE commits DROP COLUMN requires")
    db.conn.execute("ALTER TABLE features DROP COLUMN done_at")
    db.conn.execute("PRAGMA user_version=4")
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [5, 6, 7, 8]
        assert cols(db, "brainstorms") == ["id", "feature_id", "topic", "state", "roles", "requested_by",
                                           "start_message_id", "question_id", "created_at", "ended_at"]
    finally:
        db.close()


def test_v5_db_gets_commit_requires(repo):
    """F-11: migration 6 adds commits.requires; an existing request reads as requiring nothing."""
    db = ProjectDB(str(repo))
    old = db.create_commit(None, "backend", None, ["a.py"], "m", {}, "lead_review")
    db.conn.execute("ALTER TABLE commits DROP COLUMN requires")
    db.conn.execute("ALTER TABLE features DROP COLUMN done_at")
    db.conn.execute("PRAGMA user_version=5")
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [6, 7, 8] and cols(db, "commits")[-1] == "requires"
        assert db.commit(old["id"])["requires"] == []
    finally:
        db.close()


def test_v6_db_gets_done_at_backfilled(repo):
    """F-17: migration 7 adds features.done_at, from the newest "set phase done"/"archived" System line, else
    updated_at; active features stay NULL."""
    db = ProjectDB(str(repo))
    done = db.create_feature("done one")
    db.add_message(done["conversation_id"], "system", "System", "lead set phase qa", [], None)
    db.add_message(done["conversation_id"], "system", "System", "lead set phase done", [], None)
    archived = db.create_feature("archived one")
    db.add_message(archived["conversation_id"], "system", "System", "Master archived F-2", [], None)
    bare = db.create_feature("done, no line")
    active = db.create_feature("still going")
    db.conn.execute("UPDATE messages SET created_at=100+id")
    db.conn.execute("UPDATE features SET phase='done', updated_at=50 WHERE id IN (?, ?)", (done["id"], bare["id"]))
    db.conn.execute("UPDATE features SET archived=1 WHERE id=?", (archived["id"],))
    db.conn.execute("ALTER TABLE features DROP COLUMN done_at")
    db.conn.execute("PRAGMA user_version=6")
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [7, 8]
        last = db.messages(done["conversation_id"], limit=1)[-1]
        assert db.feature(done["id"])["done_at"] == last["created_at"]  # the newest done line, not the qa one
        assert db.feature(archived["id"])["done_at"] == db.messages(archived["conversation_id"], limit=1)[-1]["created_at"]
        assert db.feature(bare["id"])["done_at"] == 50
        assert db.feature(active["id"])["done_at"] is None
    finally:
        db.close()


def test_v7_db_gets_the_design_gate_start(repo, monkeypatch):
    """F-21: migration 8 stores kv design_gate_since = now; a feature that already exists then isn't gated, one
    created afterwards is, and a Design approval gates an old feature too."""
    import time
    db = ProjectDB(str(repo))
    old = db.create_feature("old")
    db.conn.execute("DELETE FROM kv WHERE key='design_gate_since'")
    db.conn.execute("PRAGMA user_version=7")
    db.close()
    monkeypatch.setattr("aiteams.db.design_gate_start", time.time)  # the real start, not the tests' far-future one
    before = time.time()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [8] and before <= db.get_kv("design_gate_since") <= time.time()
        assert db.feature(old["id"])["design"] == {"state": "none", "approval_id": None, "gated": False}
        new = db.create_feature("new")
        assert db.feature(new["id"])["design"]["gated"] is True
        db.create_approval(old["id"], "lead", None, "Design: old", "d.md")
        assert db.feature(old["id"])["design"] == {"state": "pending", "approval_id": 1, "gated": True}
    finally:
        db.close()
