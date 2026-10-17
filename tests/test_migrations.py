"""PRAGMA user_version migrations: fresh DB, M1c-shaped DB, schema ahead (read-only), run once."""
import sqlite3

from aiteams.db import MIGRATIONS, SCHEMA, ProjectDB


def cols(db, table):
    return [r["name"] for r in db.q(f"PRAGMA table_info({table})")]


def test_fresh_db_lands_at_latest(db):
    assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS) == 3
    assert db.applied == [1, 2, 3] and db.schema_ahead is False
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
        assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS) and db.applied == [1, 2, 3]
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
    assert db.applied == [1, 2, 3]
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
    db.conn.execute("PRAGMA user_version=2")
    db.close()
    db = ProjectDB(str(repo))
    try:
        assert db.applied == [3] and cols(db, "reactions") == ["message_id", "role", "emoji", "created_at"]
        assert db.message(m["id"])["text"] == "hi" and db.message(m["id"])["reactions"] == []
    finally:
        db.close()
