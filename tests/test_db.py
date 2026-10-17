"""Schema v3: migration of a real v2 db, standup seed on an existing db, wake kinds, session seq, history order."""
import sqlite3
import time

from aiteams.db import ProjectDB

V2_DDL = """
CREATE TABLE conversations(id TEXT PRIMARY KEY, kind TEXT, title TEXT, role TEXT NULL, created_at REAL);
CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT, author TEXT, type TEXT, text TEXT,
  mentions TEXT, turn_id INTEGER NULL, created_at REAL, ref TEXT NULL);
CREATE TABLE sessions(role TEXT, session_id TEXT, created_at REAL, reason TEXT, active INTEGER);
CREATE TABLE turns(id INTEGER PRIMARY KEY, role TEXT, session_id TEXT, state TEXT, origin TEXT, conversation_id TEXT,
  created_at REAL, started_at REAL, ended_at REAL, pid INTEGER, exit_code INTEGER, result_json TEXT,
  context_tokens INTEGER, cost_usd REAL, error TEXT, wake_id INTEGER);
CREATE TABLE wake_queue(id INTEGER PRIMARY KEY, role TEXT, conversation_id TEXT, message_ids TEXT, origin TEXT,
  created_at REAL, state TEXT);
CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);
INSERT INTO kv VALUES('schema_version', '2');
INSERT INTO conversations VALUES('general','channel','general',NULL,1.0);
INSERT INTO sessions VALUES('backend','s-old',1.0,'first turn',0);
INSERT INTO sessions VALUES('backend','s-new',2.0,'resume failed',1);
INSERT INTO wake_queue(role, conversation_id, message_ids, origin, created_at, state) VALUES('qa','general','[1]','role',1.0,'queued');
"""


def test_v2_db_migrates_to_v3(repo):
    d = repo / ".aiteams"
    d.mkdir()
    conn = sqlite3.connect(str(d / "state.db"))
    conn.executescript(V2_DDL)
    conn.commit()
    conn.close()
    db = ProjectDB(str(repo))
    try:
        assert db.get_kv("schema_version") == 3
        assert db.conversation("standup")["kind"] == "channel"  # seeded on an existing db
        assert db.session_info("backend") == {"session_id": "s-new", "seq": 2, "fresh_starts": 1, "reason": "resume failed"}
        w = db.queued_wake("qa")
        assert w["kind"] == "normal" and w["ref"] is None
        assert db.role_state("manager")["fail_streak"] == 0
        assert db.reset_after("general") == 0
        assert db.turns() == [] and db.activity() == [] and db.handoffs() == []
    finally:
        db.close()


def test_wake_kinds_sessions_handoffs_history(db):
    assert db.add_wake("qa", "standup", [], "master", kind="standup", ref=7)["ref"] == 7
    assert db.queued_wake("qa") is None and db.queued_wake("qa", "standup")["ref"] == 7
    db.add_wake("qa", "dm-qa", [1], "role")
    assert db.queued_wake("qa")["kind"] == "normal"
    assert db.session_info("qa")["seq"] == 0
    db.new_session("qa", "a", "first turn"); db.new_session("qa", "b", "context 70%")
    assert db.session_info("qa") == {"session_id": "b", "seq": 2, "fresh_starts": 1, "reason": "context 70%"}
    assert db.latest_handoff("qa") is None
    db.add_handoff("qa", "a", 1, "/x/1.md", None, "context 70%", 0.7)
    h2 = db.add_handoff("qa", "b", 2, "/x/2.md", None, "own initiative", None)
    assert db.latest_handoff("qa")["id"] == h2["id"]
    f1 = db.create_feature("one"); f2 = db.create_feature("two"); f3 = db.create_feature("three")
    db.update_feature(f1["id"], phase="done"); time.sleep(0.01)
    db.update_feature(f3["id"], archived=True); time.sleep(0.01)
    db.update_feature(f2["id"], phase="build")
    assert [f["key"] for f in db.features_history()] == ["F-3", "F-1"]
    assert [f["key"] for f in db.features()] == ["F-2"]
