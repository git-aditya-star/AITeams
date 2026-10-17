"""Per-project SQLite state (.aiteams/state.db), schema + helpers. One connection per project."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .config import ROLES

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
  id TEXT PRIMARY KEY, kind TEXT, title TEXT,
  role TEXT NULL, created_at REAL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, conversation_id TEXT, author TEXT, type TEXT, text TEXT,
  mentions TEXT, turn_id INTEGER NULL, created_at REAL, ref TEXT NULL);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS receipts(
  message_id INTEGER, role TEXT, state TEXT CHECK(state IN ('queued','seen','acked')),
  updated_at REAL, PRIMARY KEY(message_id, role));
CREATE TABLE IF NOT EXISTS sessions(role TEXT, session_id TEXT, created_at REAL, reason TEXT, active INTEGER);
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY, role TEXT, session_id TEXT, state TEXT, origin TEXT, conversation_id TEXT,
  created_at REAL, started_at REAL, ended_at REAL, pid INTEGER, exit_code INTEGER, result_json TEXT,
  context_tokens INTEGER, cost_usd REAL, error TEXT, wake_id INTEGER);
CREATE TABLE IF NOT EXISTS turn_events(turn_id INTEGER, seq INTEGER, ts REAL, type TEXT, json TEXT,
  PRIMARY KEY(turn_id, seq));
CREATE TABLE IF NOT EXISTS wake_queue(
  id INTEGER PRIMARY KEY, role TEXT, conversation_id TEXT, message_ids TEXT, origin TEXT,
  created_at REAL, state TEXT);
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY, turn_id INTEGER, role TEXT, tool_use_id TEXT, payload TEXT, state TEXT,
  answers TEXT NULL, created_at REAL, answered_at REAL);
CREATE TABLE IF NOT EXISTS rate_limits(ts REAL, json TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS features(
  id INTEGER PRIMARY KEY, title TEXT, phase TEXT DEFAULT 'intake', owner TEXT NULL,
  conversation_id TEXT UNIQUE, archived INTEGER DEFAULT 0, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, feature_id INTEGER NULL, role TEXT, turn_id INTEGER NULL, title TEXT,
  summary TEXT, state TEXT, note TEXT NULL, message_id INTEGER NULL, created_at REAL, resolved_at REAL NULL);
CREATE TABLE IF NOT EXISTS commits(
  id INTEGER PRIMARY KEY, feature_id INTEGER NULL, role TEXT, turn_id INTEGER NULL, files TEXT,
  message TEXT, hashes TEXT, state TEXT, note TEXT NULL, sha TEXT NULL, message_id INTEGER NULL,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS locks(path TEXT PRIMARY KEY, role TEXT, turn_id INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS file_edits(path TEXT PRIMARY KEY, role TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS resolved(message_id INTEGER PRIMARY KEY, resolved_at REAL);
"""

PHASES = ("intake", "spec", "design", "build", "qa", "done")
SCHEMA_VERSION = 2


class ProjectDB:
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.dir = Path(project_path) / ".aiteams"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.db"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.migrate()
        self._seed()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ---- low level -------------------------------------------------------
    def q(self, sql: str, *args) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, *args) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    # ---- seed ------------------------------------------------------------
    def _seed(self) -> None:
        now = time.time()
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO conversations(id, kind, title, role, created_at) VALUES(?,?,?,?,?)",
                ("general", "channel", "general", None, now))
            for r in ROLES:
                self.conn.execute(
                    "INSERT OR IGNORE INTO conversations(id, kind, title, role, created_at) VALUES(?,?,?,?,?)",
                    ("dm-" + r, "dm", r, r, now))

    # ---- kv --------------------------------------------------------------
    def get_kv(self, key: str, default=None):
        row = self.one("SELECT value FROM kv WHERE key=?", key)
        return json.loads(row["value"]) if row else default

    def set_kv(self, key: str, value) -> None:
        self.x("INSERT OR REPLACE INTO kv(key, value) VALUES(?,?)", key, json.dumps(value))

    # ---- conversations ---------------------------------------------------
    def conversations(self) -> list[dict]:
        rows = self.q("""SELECT c.*, (SELECT MAX(created_at) FROM messages m WHERE m.conversation_id=c.id)
                         AS last_message_at FROM conversations c ORDER BY created_at""")
        return [dict(r) for r in rows]

    def conversation(self, ident: str) -> dict | None:
        row = self.one("SELECT * FROM conversations WHERE id=?", ident)
        if row is None:
            row = self.one("SELECT * FROM conversations WHERE title=? ORDER BY created_at DESC LIMIT 1", ident)
        return dict(row) if row else None

    def create_thread(self, title: str) -> dict:
        """M1a name kept: a titled conversation is now a feature; returns its conversation row."""
        return self.conversation(self.create_feature(title)["conversation_id"])

    # ---- messages --------------------------------------------------------
    def add_message(self, conversation_id: str, author: str, type_: str, text: str,
                    mentions: list[str], turn_id: int | None, ref: str | None = None) -> dict:
        mid = self.x("INSERT INTO messages(conversation_id, author, type, text, mentions, turn_id, created_at, ref)"
                     " VALUES(?,?,?,?,?,?,?,?)", conversation_id, author, type_, text,
                     json.dumps(mentions), turn_id, time.time(), ref)
        return self.message(mid)

    def _msg_dict(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["mentions"] = json.loads(d["mentions"] or "[]")
        d["approval"] = d["commit"] = None
        ref = d.get("ref")
        if ref and ":" in ref:
            kind, _, num = ref.partition(":")
            if kind == "approval" and num.isdigit():
                d["approval"] = self.approval(int(num))
            elif kind == "commit" and num.isdigit():
                d["commit"] = self.commit(int(num))
        return d

    def message(self, mid: int) -> dict | None:
        r = self.one("SELECT * FROM messages WHERE id=?", mid)
        if not r:
            return None
        d = self._msg_dict(r)
        d["receipts"] = self.receipts_for([mid]).get(mid, [])
        return d

    def messages(self, conversation_id: str, before: int | None = None, limit: int = 50) -> list[dict]:
        if before:
            rows = self.q("SELECT * FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT ?",
                          conversation_id, before, limit)
        else:
            rows = self.q("SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                          conversation_id, limit)
        msgs = [self._msg_dict(r) for r in reversed(rows)]
        rec = self.receipts_for([m["id"] for m in msgs])
        for m in msgs:
            m["receipts"] = rec.get(m["id"], [])
        return msgs

    def messages_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.q(f"SELECT * FROM messages WHERE id IN ({marks}) ORDER BY id", *ids)
        return [self._msg_dict(r) for r in rows]

    # ---- receipts --------------------------------------------------------
    def receipts_for(self, ids: list[int]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        if not ids:
            return out
        marks = ",".join("?" * len(ids))
        for r in self.q(f"SELECT message_id, role, state FROM receipts WHERE message_id IN ({marks})"
                        " ORDER BY role", *ids):
            out.setdefault(r["message_id"], []).append({"role": r["role"], "state": r["state"]})
        return out

    def set_receipt(self, message_id: int, role: str, state: str) -> bool:
        """Only moves forward (queued < seen < acked). Returns True if changed."""
        order = {"queued": 0, "seen": 1, "acked": 2}
        cur = self.one("SELECT state FROM receipts WHERE message_id=? AND role=?", message_id, role)
        if cur and order[cur["state"]] >= order[state]:
            return False
        self.x("INSERT OR REPLACE INTO receipts(message_id, role, state, updated_at) VALUES(?,?,?,?)",
               message_id, role, state, time.time())
        return True

    # ---- sessions --------------------------------------------------------
    def active_session(self, role: str) -> str | None:
        r = self.one("SELECT session_id FROM sessions WHERE role=? AND active=1 ORDER BY created_at DESC LIMIT 1", role)
        return r["session_id"] if r else None

    def new_session(self, role: str, session_id: str, reason: str) -> str:
        with self.lock:
            self.conn.execute("UPDATE sessions SET active=0 WHERE role=?", (role,))
            self.conn.execute("INSERT INTO sessions(role, session_id, created_at, reason, active) VALUES(?,?,?,?,1)",
                              (role, session_id, time.time(), reason))
        return session_id

    # ---- turns -----------------------------------------------------------
    def turn(self, tid: int) -> dict | None:
        r = self.one("SELECT * FROM turns WHERE id=?", tid)
        return dict(r) if r else None

    def turns(self, role: str | None = None, limit: int = 50) -> list[dict]:
        if role:
            rows = self.q("SELECT * FROM turns WHERE role=? ORDER BY id DESC LIMIT ?", role, limit)
        else:
            rows = self.q("SELECT * FROM turns ORDER BY id DESC LIMIT ?", limit)
        return [dict(r) for r in rows]

    def create_turn(self, role: str, session_id: str, origin: str, conversation_id: str) -> dict:
        tid = self.x("INSERT INTO turns(role, session_id, state, origin, conversation_id, created_at)"
                     " VALUES(?,?,?,?,?,?)", role, session_id, "queued", origin, conversation_id, time.time())
        return self.turn(tid)

    def update_turn(self, tid: int, **fields) -> dict:
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.x(f"UPDATE turns SET {cols} WHERE id=?", *fields.values(), tid)
        return self.turn(tid)

    def add_turn_event(self, tid: int, ev: dict) -> dict:
        with self.lock:
            r = self.conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM turn_events WHERE turn_id=?", (tid,)).fetchone()
            seq = r["s"]
            ts = time.time()
            self.conn.execute("INSERT INTO turn_events(turn_id, seq, ts, type, json) VALUES(?,?,?,?,?)",
                              (tid, seq, ts, str(ev.get("type")), json.dumps(ev)))
        return {"turn_id": tid, "seq": seq, "ts": ts, "type": str(ev.get("type")), "data": ev}

    def turn_events(self, tid: int, after_seq: int = 0) -> list[dict]:
        rows = self.q("SELECT * FROM turn_events WHERE turn_id=? AND seq>? ORDER BY seq", tid, after_seq)
        return [{"turn_id": r["turn_id"], "seq": r["seq"], "ts": r["ts"], "type": r["type"],
                 "data": json.loads(r["json"])} for r in rows]

    def cost_today(self, role: str) -> float:
        start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))  # local midnight
        r = self.one("SELECT COALESCE(SUM(cost_usd),0) AS c FROM turns WHERE role=? AND created_at>=?", role, start)
        return float(r["c"] or 0)

    # ---- wake queue ------------------------------------------------------
    def queued_wake(self, role: str) -> dict | None:
        r = self.one("SELECT * FROM wake_queue WHERE role=? AND state='queued' ORDER BY id LIMIT 1", role)
        return self._wake_dict(r) if r else None

    def _wake_dict(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["message_ids"] = json.loads(d["message_ids"] or "[]")
        return d

    def add_wake(self, role: str, conversation_id: str, message_ids: list[int], origin: str) -> dict:
        wid = self.x("INSERT INTO wake_queue(role, conversation_id, message_ids, origin, created_at, state)"
                     " VALUES(?,?,?,?,?,?)", role, conversation_id, json.dumps(message_ids), origin,
                     time.time(), "queued")
        return self._wake_dict(self.one("SELECT * FROM wake_queue WHERE id=?", wid))

    def merge_wake(self, wid: int, conversation_id: str, message_ids: list[int], origin: str) -> dict:
        w = self._wake_dict(self.one("SELECT * FROM wake_queue WHERE id=?", wid))
        ids = w["message_ids"] + [m for m in message_ids if m not in w["message_ids"]]
        new_origin = "master" if "master" in (origin, w["origin"]) else w["origin"]
        self.x("UPDATE wake_queue SET message_ids=?, origin=?, conversation_id=? WHERE id=?",
               json.dumps(ids), new_origin, conversation_id, wid)
        return self._wake_dict(self.one("SELECT * FROM wake_queue WHERE id=?", wid))

    def queued_wakes(self) -> list[dict]:
        rows = self.q("SELECT * FROM wake_queue WHERE state='queued' ORDER BY (origin='master') DESC, id")
        return [self._wake_dict(r) for r in rows]

    def set_wake_state(self, wid: int, state: str) -> None:
        self.x("UPDATE wake_queue SET state=? WHERE id=?", state, wid)

    def wake(self, wid: int) -> dict | None:
        r = self.one("SELECT * FROM wake_queue WHERE id=?", wid)
        return self._wake_dict(r) if r else None

    # ---- questions -------------------------------------------------------
    def _q_dict(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["answers"] = json.loads(d["answers"]) if d["answers"] else None
        return d

    def create_question(self, turn_id: int, role: str, tool_use_id: str | None, payload: dict) -> dict:
        qid = self.x("INSERT INTO questions(turn_id, role, tool_use_id, payload, state, created_at)"
                     " VALUES(?,?,?,?,?,?)", turn_id, role, tool_use_id, json.dumps(payload), "pending", time.time())
        return self.question(qid)

    def question(self, qid: int) -> dict | None:
        r = self.one("SELECT * FROM questions WHERE id=?", qid)
        return self._q_dict(r) if r else None

    def questions(self, state: str = "pending", limit: int = 50) -> list[dict]:
        rows = self.q("SELECT * FROM questions WHERE state=? ORDER BY id DESC LIMIT ?", state, limit)
        return [self._q_dict(r) for r in rows]

    def answer_question(self, qid: int, answers: dict | None, state: str) -> dict | None:
        self.x("UPDATE questions SET state=?, answers=?, answered_at=? WHERE id=? AND state='pending'",
               state, json.dumps(answers) if answers is not None else None, time.time(), qid)
        return self.question(qid)

    # ---- rate limits -----------------------------------------------------
    def add_rate_limit(self, info: dict) -> None:
        self.x("INSERT INTO rate_limits(ts, json) VALUES(?,?)", time.time(), json.dumps(info))

    # ---- migrations ------------------------------------------------------
    def migrate(self) -> None:
        """v1 -> v2: conversation kind CHECK dropped (thread -> feature), messages.ref, features rows."""
        version = self.get_kv("schema_version", 1)
        if version >= SCHEMA_VERSION:
            return
        with self.lock:
            cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(messages)")]
            if "ref" not in cols:
                self.conn.execute("ALTER TABLE messages ADD COLUMN ref TEXT NULL")
            sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='conversations'").fetchone()["sql"]
            if "CHECK" in sql:
                self.conn.executescript("""
                    BEGIN;
                    CREATE TABLE conversations_new(id TEXT PRIMARY KEY, kind TEXT, title TEXT, role TEXT NULL, created_at REAL);
                    INSERT INTO conversations_new SELECT id, kind, title, role, created_at FROM conversations;
                    DROP TABLE conversations; ALTER TABLE conversations_new RENAME TO conversations;
                    COMMIT;""")
            for r in self.conn.execute("SELECT * FROM conversations WHERE kind='thread'").fetchall():
                self.conn.execute("UPDATE conversations SET kind='feature' WHERE id=?", (r["id"],))
                if not self.conn.execute("SELECT 1 FROM features WHERE conversation_id=?", (r["id"],)).fetchone():
                    self.conn.execute("INSERT INTO features(title, phase, owner, conversation_id, archived, created_at, updated_at)"
                                      " VALUES(?,?,?,?,?,?,?)", (r["title"], "intake", None, r["id"], 0, r["created_at"], r["created_at"]))
        self.set_kv("schema_version", SCHEMA_VERSION)

    # ---- features --------------------------------------------------------
    def _feat_dict(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["key"] = f"F-{d['id']}"
        d["archived"] = bool(d["archived"])
        return d

    def feature(self, ident) -> dict | None:
        """Accepts 3, "3", "F-3", "feature-3" or a conversation id."""
        s = str(ident).strip()
        low = s.lower()
        fid = None
        if low.startswith("f-") and low[2:].isdigit():
            fid = int(low[2:])
        elif low.startswith("feature-") and low[8:].isdigit():
            fid = int(low[8:])
        elif s.isdigit():
            fid = int(s)
        if fid is not None:
            r = self.one("SELECT * FROM features WHERE id=?", fid)
        else:
            r = self.one("SELECT * FROM features WHERE conversation_id=?", s)
        return self._feat_dict(r) if r else None

    def features(self, include_history: bool = False) -> list[dict]:
        rows = self.q("""SELECT f.*, (SELECT MAX(created_at) FROM messages m WHERE m.conversation_id=f.conversation_id) AS last_activity,
                         (SELECT COUNT(*) FROM approvals a WHERE a.feature_id=f.id AND a.state='pending') AS open_approvals,
                         (SELECT COUNT(*) FROM commits c WHERE c.feature_id=f.id AND c.state IN ('lead_review','master_review')) AS open_commits
                         FROM features f ORDER BY f.id DESC""")
        out = []
        for r in rows:
            d = self._feat_dict(r)
            d["last_activity"] = d["last_activity"] or d["created_at"]
            if include_history or (d["phase"] != "done" and not d["archived"]):
                out.append(d)
        return out

    def create_feature(self, title: str, phase: str = "intake", owner: str | None = None) -> dict:
        now = time.time()
        with self.lock:
            fid = self.x("INSERT INTO features(title, phase, owner, conversation_id, archived, created_at, updated_at)"
                         " VALUES(?,?,?,?,?,?,?)", title, phase, owner, None, 0, now, now)
            cid = f"feature-{fid}"
            self.x("INSERT INTO conversations(id, kind, title, role, created_at) VALUES(?,?,?,?,?)",
                   cid, "feature", f"F-{fid} {title}", None, now)
            self.x("UPDATE features SET conversation_id=? WHERE id=?", cid, fid)
        return self.feature(fid)

    def update_feature(self, fid: int, **fields) -> dict:
        fields = {k: v for k, v in fields.items() if k in ("title", "phase", "owner", "archived")}
        if fields:
            if "archived" in fields:
                fields["archived"] = 1 if fields["archived"] else 0
            cols = ", ".join(f"{k}=?" for k in fields)
            self.x(f"UPDATE features SET {cols}, updated_at=? WHERE id=?", *fields.values(), time.time(), fid)
            if "title" in fields:
                conv_id = self.one("SELECT conversation_id FROM features WHERE id=?", fid)["conversation_id"]
                self.x("UPDATE conversations SET title=? WHERE id=?", f"F-{fid} {fields['title']}", conv_id)
        return self.feature(fid)

    # ---- approvals -------------------------------------------------------
    def create_approval(self, feature_id: int | None, role: str, turn_id: int | None, title: str, summary: str) -> dict:
        aid = self.x("INSERT INTO approvals(feature_id, role, turn_id, title, summary, state, created_at)"
                     " VALUES(?,?,?,?,?,?,?)", feature_id, role, turn_id, title, summary, "pending", time.time())
        return self.approval(aid)

    def approval(self, aid: int) -> dict | None:
        r = self.one("SELECT * FROM approvals WHERE id=?", aid)
        return dict(r) if r else None

    def approvals(self, state: str = "pending", feature_id: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM approvals WHERE 1=1", []
        if state != "all":
            sql += " AND state=?"; args.append(state)
        if feature_id is not None:
            sql += " AND feature_id=?"; args.append(feature_id)
        return [dict(r) for r in self.q(sql + " ORDER BY id DESC", *args)]

    def resolve_approval(self, aid: int, state: str, note: str | None) -> dict | None:
        self.x("UPDATE approvals SET state=?, note=?, resolved_at=? WHERE id=? AND state='pending'",
               state, note, time.time(), aid)
        return self.approval(aid)

    # ---- commits ---------------------------------------------------------
    def _commit_dict(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["files"] = json.loads(d["files"] or "[]")
        d["hashes"] = json.loads(d["hashes"] or "{}")
        return d

    def create_commit(self, feature_id: int | None, role: str, turn_id: int | None, files: list[str],
                      message: str, hashes: dict, state: str) -> dict:
        now = time.time()
        cid = self.x("INSERT INTO commits(feature_id, role, turn_id, files, message, hashes, state, created_at, updated_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?)", feature_id, role, turn_id, json.dumps(files), message,
                     json.dumps(hashes), state, now, now)
        return self.commit(cid)

    def commit(self, cid: int) -> dict | None:
        r = self.one("SELECT * FROM commits WHERE id=?", cid)
        return self._commit_dict(r) if r else None

    def commits(self, state: str = "all", feature_id: int | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM commits WHERE 1=1", []
        if state != "all":
            sql += " AND state=?"; args.append(state)
        if feature_id is not None:
            sql += " AND feature_id=?"; args.append(feature_id)
        args.append(limit)
        return [self._commit_dict(r) for r in self.q(sql + " ORDER BY id DESC LIMIT ?", *args)]

    def update_commit(self, cid: int, **fields) -> dict | None:
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.x(f"UPDATE commits SET {cols}, updated_at=? WHERE id=?", *fields.values(), time.time(), cid)
        return self.commit(cid)

    def set_message_ref(self, mid: int, ref: str) -> None:
        self.x("UPDATE messages SET ref=? WHERE id=?", ref, mid)

    # ---- locks / edits ---------------------------------------------------
    def acquire_lock(self, path: str, role: str, turn_id: int | None) -> dict | None:
        """Lock `path` for `role`. Returns None when acquired (or already held by role), else the holder row."""
        with self.lock:
            r = self.conn.execute("SELECT * FROM locks WHERE path=?", (path,)).fetchone()
            if r and r["role"] != role:
                return dict(r)
            if not r:
                self.conn.execute("INSERT INTO locks(path, role, turn_id, created_at) VALUES(?,?,?,?)",
                                  (path, role, turn_id, time.time()))
        return None

    def release_lock(self, path: str) -> dict | None:
        r = self.one("SELECT * FROM locks WHERE path=?", path)
        if r:
            self.x("DELETE FROM locks WHERE path=?", path)
        return dict(r) if r else None

    def release_locks(self, role: str) -> list[str]:
        paths = [r["path"] for r in self.q("SELECT path FROM locks WHERE role=?", role)]
        if paths:
            self.x("DELETE FROM locks WHERE role=?", role)
        return paths

    def locks(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM locks ORDER BY created_at")]

    def touch_edit(self, path: str, role: str) -> None:
        self.x("INSERT OR REPLACE INTO file_edits(path, role, updated_at) VALUES(?,?,?)", path, role, time.time())

    def file_edits_for(self, prefix: str) -> dict[str, dict]:
        rows = self.q("SELECT * FROM file_edits WHERE path LIKE ?", prefix.replace("%", "%%") + "%")
        return {r["path"]: dict(r) for r in rows}

    # ---- inbox -----------------------------------------------------------
    def resolve_message(self, mid: int) -> None:
        self.x("INSERT OR IGNORE INTO resolved(message_id, resolved_at) VALUES(?,?)", mid, time.time())

    def inbox_messages(self) -> list[dict]:
        rows = self.q("""SELECT m.*, c.title AS conversation_title FROM messages m
                         JOIN conversations c ON c.id=m.conversation_id
                         WHERE m.type IN ('Question','Blocker') AND m.mentions LIKE '%"master"%'
                           AND m.id NOT IN (SELECT message_id FROM resolved) ORDER BY m.id DESC""")
        out = []
        for r in rows:
            d = self._msg_dict(r)
            d["receipts"] = []
            out.append(d)
        return out


def ensure_git_exclude(project_path: str) -> bool:
    """Add .aiteams/ to .git/info/exclude if this is a git repo. Returns is_git."""
    git_dir = os.path.join(project_path, ".git")
    if not os.path.isdir(git_dir):
        return False
    info = os.path.join(git_dir, "info")
    os.makedirs(info, exist_ok=True)
    excl = os.path.join(info, "exclude")
    try:
        existing = open(excl).read() if os.path.exists(excl) else ""
        if ".aiteams/" not in existing.splitlines():
            with open(excl, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(".aiteams/\n")
    except OSError:
        pass
    return True
