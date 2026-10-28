"""JSON-RPC handshake, tools/list, post_message and the permission tool against a mocked daemon."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.conftest import ROOT

SERVER = os.path.join(ROOT, "aiteams", "mcp_server.py")


class Mock(BaseHTTPRequestHandler):
    calls = []
    mode = "answer"

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Mock.calls.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}))
        if self.path.startswith("/internal/conversations"):
            self._send(200, [{"id": "general", "title": "general", "kind": "channel"}])
        else:
            self._send(200, [{"id": 1, "author": "master", "type": "Message", "text": "hi", "mentions": []}])

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Mock.calls.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path == "/internal/messages":
            self._send(200, {"id": 42, "conversation_id": "general"})
        elif self.path == "/internal/reactions":
            added = not any(c[1] == "/internal/reactions" for c in Mock.calls[:-1])
            self._send(200, {"message_id": body["message_id"], "added": added, "reactions": []})
        elif self.path == "/internal/permission":
            if Mock.mode == "answer":
                self._send(200, {"behavior": "allow", "updatedInput": {**body["input"], "answers": {"Which DB?": "Postgres"}}})
            else:
                self._send(200, {"behavior": "deny", "message": "The Master did not answer within 10 minutes. Post the question"})
        elif self.path == "/internal/branches":
            self._send(200, {"id": 9, "state": "pending", "payload": body})
        elif self.path == "/internal/withdraw":
            if not body.get("reason"):
                self._send(400, {"detail": "give a one-line reason (no line breaks); it is shown in chat"})
            else:
                self._send(200, {"kind": body["kind"], "id": body["id"], "state": "withdrawn", "note": body["reason"]})
        else:
            self._send(404, {"detail": "nope"})


def run_rpc(url, requests):
    env = {**os.environ, "AITEAMS_DAEMON_URL": url, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7",
           "AITEAMS_TURN_TOKEN": "turn-secret", "AITEAMS_ROLE": "backend", "PYTHONDONTWRITEBYTECODE": "1"}
    inp = "".join(json.dumps(r) + "\n" for r in requests)
    out = subprocess.run([sys.executable, "-B", SERVER], input=inp, capture_output=True, text=True, env=env, timeout=30)
    return [json.loads(l) for l in out.stdout.splitlines() if l.strip()]


def with_server(fn):
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        return fn("http://127.0.0.1:%d" % srv.server_port)
    finally:
        srv.shutdown()


def test_handshake_tools_list_and_errors():
    res = run_rpc("http://127.0.0.1:1", [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "bogus/method"},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "post_message", "arguments": {"conversation": "general", "text": "x"}}},
    ])
    by_id = {r.get("id"): r for r in res}
    assert by_id[1]["result"]["serverInfo"]["name"] == "aiteams"
    assert by_id[2]["result"] == {}
    assert {t["name"] for t in by_id[3]["result"]["tools"]} == {
        "post_message", "get_messages", "list_conversations", "permission", "list_features", "get_feature",
        "create_feature", "set_feature", "request_approval", "request_commit", "review_commit", "lock_file",
        "unlock_file", "list_locks", "write_handoff", "get_team_status", "run_standup", "save_standup",
        "interrupt", "checkpoint", "react", "ask_master", "withdraw_request", "start_brainstorm", "propose_branch"}
    assert by_id[4]["error"]["code"] == -32601
    assert by_id[5]["error"]["code"] == -32602
    assert by_id[6]["result"]["isError"] is True and "unreachable" in by_id[6]["result"]["content"][0]["text"]


def test_post_message_and_reads_hit_daemon_with_headers():
    Mock.calls.clear()

    def go(url):
        return run_rpc(url, [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "post_message", "arguments": {
                "conversation": "general", "text": "hello @lead", "type": "Finding", "mentions": ["lead"]}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_messages", "arguments": {"conversation": "general", "limit": 5}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_conversations", "arguments": {}}},
        ])
    res = {r["id"]: r for r in with_server(go)}
    assert res[1]["result"]["content"][0]["text"] == "posted #42"
    post = [c for c in Mock.calls if c[0] == "POST"][0]
    assert post[2]["x-aiteams-project"] == "p1" and post[2]["x-aiteams-turn"] == "7"
    assert post[2]["x-aiteams-token"] == "turn-secret"
    assert all(c[2]["x-aiteams-token"] == "turn-secret" for c in Mock.calls)
    assert post[3] == {"conversation": "general", "text": "hello @lead", "type": "Finding", "mentions": ["lead"]}
    assert json.loads(res[2]["result"]["content"][0]["text"])[0]["text"] == "hi"
    assert json.loads(res[3]["result"]["content"][0]["text"])[0]["id"] == "general"


def test_permission_passthrough_answer_and_timeout():
    q = {"questions": [{"question": "Which DB?", "header": "DB", "multiSelect": False,
                        "options": [{"label": "Postgres", "description": ""}, {"label": "SQLite", "description": ""}]}]}

    def go(url):
        Mock.mode = "answer"
        a = run_rpc(url, [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "permission", "arguments": {
                "tool_name": "Write", "input": {"file_path": "x"}, "tool_use_id": "t1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "permission", "arguments": {
                "tool_name": "AskUserQuestion", "input": q, "tool_use_id": "t2"}}},
        ])
        Mock.mode = "timeout"
        b = run_rpc(url, [{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "permission", "arguments": {
            "tool_name": "AskUserQuestion", "input": q, "tool_use_id": "t3"}}}])
        return a + b
    res = {r["id"]: json.loads(r["result"]["content"][0]["text"]) for r in with_server(go)}
    assert res[1] == {"behavior": "allow", "updatedInput": {"file_path": "x"}}
    assert res[2]["behavior"] == "allow" and res[2]["updatedInput"]["answers"] == {"Which DB?": "Postgres"}
    assert res[2]["updatedInput"]["questions"] == q["questions"]
    assert res[3]["behavior"] == "deny" and "did not answer" in res[3]["message"]
    perm_calls = [c for c in Mock.calls if c[1] == "/internal/permission"]
    assert len(perm_calls) == 2  # the Write passthrough never reached the daemon


def test_react_round_trip_and_emoji_enum():
    from aiteams.db import EMOJI
    Mock.calls.clear()
    call = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "react", "arguments": {
        "message_id": 12, "emoji": EMOJI[0]}}}
    res = with_server(lambda url: run_rpc(url, [{**call, "id": 1}, {**call, "id": 2},
                                                {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}]))
    by_id = {r["id"]: r for r in res}
    assert by_id[1]["result"]["content"][0]["text"] == "added %s to #12" % EMOJI[0]
    assert by_id[2]["result"]["content"][0]["text"] == "removed %s from #12" % EMOJI[0]
    posts = [c for c in Mock.calls if c[1] == "/internal/reactions"]
    assert [c[3] for c in posts] == [{"message_id": 12, "emoji": EMOJI[0]}] * 2
    assert posts[0][2]["x-aiteams-token"] == "turn-secret"
    react = next(t for t in by_id[3]["result"]["tools"] if t["name"] == "react")
    assert tuple(react["inputSchema"]["properties"]["emoji"]["enum"]) == EMOJI  # the server is stdlib-only: keep in sync


def test_withdraw_request_tool_posts_kind_id_and_reason_and_withdraw_question_is_gone():
    Mock.calls.clear()
    call = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "withdraw_request", "arguments": {
        "kind": "commit", "id": 95, "reason": "superseded by #97"}}}
    old = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "withdraw_question", "arguments": {"id": 3}}}
    blank = {**call, "id": 3, "params": {"name": "withdraw_request", "arguments": {"kind": "card", "id": 2}}}
    res = with_server(lambda url: run_rpc(url, [{**call, "id": 1}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                                blank, old]))
    by_id = {r["id"]: r for r in res}
    assert by_id[1]["result"]["content"][0]["text"] == "commit #95 withdrawn: superseded by #97"
    post = [c for c in Mock.calls if c[1] == "/internal/withdraw"][0]
    assert post[3] == {"kind": "commit", "id": 95, "reason": "superseded by #97"}
    assert post[2]["x-aiteams-token"] == "turn-secret"
    tools = {t["name"]: t for t in by_id[2]["result"]["tools"]}
    assert "withdraw_question" not in tools
    schema = tools["withdraw_request"]["inputSchema"]
    assert schema["required"] == ["kind", "id", "reason"] and schema["properties"]["kind"]["enum"] == ["approval", "commit", "card"]
    assert "withdraw_request" in tools["ask_master"]["description"]  # the card tool points at the new one
    assert by_id[3]["result"]["isError"] is True and "one-line reason" in by_id[3]["result"]["content"][0]["text"]
    assert by_id[4]["error"]["code"] == -32602  # unknown tool


def test_propose_branch_tool_posts_the_proposal_and_the_schema_lists_the_modes():
    Mock.calls.clear()
    call = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "propose_branch", "arguments": {
        "feature": "F-39", "mode": "new", "branch": "feat/f39-x", "base": "origin/main", "why": "keeps it apart"}}}
    bare = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "propose_branch", "arguments": {"feature": "F-2"}}}
    res = with_server(lambda url: run_rpc(url, [{**call, "id": 1}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, bare]))
    by_id = {r["id"]: r for r in res}
    assert by_id[1]["result"]["content"][0]["text"] == "Branch card #9 pending; end your turn, the Master will answer in the Inbox"
    posts = [c for c in Mock.calls if c[1] == "/internal/branches"]
    assert posts[0][3] == {"feature": "F-39", "mode": "new", "branch": "feat/f39-x", "base": "origin/main", "shared_from": None,
                           "why": "keeps it apart"}
    assert posts[1][3]["mode"] == "new" and posts[1][3]["branch"] is None and posts[1][3]["why"] == ""  # the defaults
    assert posts[0][2]["x-aiteams-token"] == "turn-secret"
    tool = next(t for t in by_id[2]["result"]["tools"] if t["name"] == "propose_branch")
    assert tool["inputSchema"]["required"] == ["feature"] and tool["inputSchema"]["properties"]["mode"]["enum"] == ["new", "current", "shared"]
