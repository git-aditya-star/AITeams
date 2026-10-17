"""F-16: messages posted while a role's turn runs are delivered on its next tool call (relevant ones in full, the rest
counted, each once per turn), and a decision on a conversation that moved on is refused once."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    proj = client.app.state.app.projects[pid]
    proj.db.add_message("feature-1", "lead", "Message", "@backend take the API", ["backend"], None)  # backend: voice
    return pid


def proj_of(client, pid):
    return client.app.state.app.projects[pid]


def start(client, pid, role):
    """A running turn whose prompt was just built (the watermark = the newest message)."""
    proj = proj_of(client, pid)
    t, hdr = running_turn(proj.db, role, pid=pid)
    proj.delivered[t["id"]] = proj.db.max_message_id()
    return t, hdr


def news(client, hdr, shown=None):
    return client.get("/internal/new_messages", params={"shown": shown} if shown else {}, headers=hdr).json()["text"]


def say(proj, conv, author, text, mentions=(), type_="Message", ref=None):
    return proj.db.add_message(conv, author, type_, text, list(mentions), None, ref)


def test_delivered_once_relevant_in_full_others_counted(client, pid):
    proj = proj_of(client, pid)
    t, hdr = start(client, pid, "backend")
    assert news(client, hdr) == ""
    m1 = say(proj, "feature-1", "frontend", "the UI needs `requires` too")           # voiced feature
    say(proj, "general", "product", "unrelated chatter")                              # not relevant: counted
    m3 = say(proj, "general", "lead", "@backend ping", ["backend"])                   # mention
    m4 = say(proj, "dm-backend", "master", "status?")                                 # DM
    say(proj, "feature-1", "backend", "my own message")                               # own: skipped
    say(proj, "feature-1", "system", "product set phase build", type_="System")       # plain System: skipped
    text = news(client, hdr)
    assert text.startswith("---\n**4 new messages since your turn started.** Read them before you act further.")
    for m in (m1, m3, m4):
        assert f"#{m['id']} {m['author']} ({m['type']})" in text
    assert "- [general] " in text and "@backend: @backend ping" in text
    assert text.endswith("and 1 other message in general (1).")
    assert "unrelated chatter" not in text and "my own message" not in text and "set phase" not in text
    assert news(client, hdr) == ""  # once per turn
    m5 = say(proj, "feature-1", "qa", "newer")
    text = news(client, hdr)
    assert f"#{m5['id']}" in text and f"#{m1['id']}" not in text
    # delivered relevant messages get the `seen` receipt
    assert any(r["role"] == "backend" and r["state"] == "seen" for r in proj.db.message(m1["id"])["receipts"])


def test_cap_overflow_and_long_text(client, pid):
    proj = proj_of(client, pid)
    t, hdr = start(client, pid, "backend")
    ids = [say(proj, "dm-backend", "master", f"note {i}")["id"] for i in range(12)]
    say(proj, "dm-backend", "master", "x" * 700)
    text = news(client, hdr)
    assert "**13 new messages" in text and text.count("\n- [dm-backend]") == 10
    assert f"#{ids[2]} " not in text and f"#{ids[3]} " in text  # the newest 10 are shown
    assert "x" * 600 + "…" in text and "x" * 601 not in text
    assert text.endswith("and 3 other messages in dm-backend (3).")


def test_commit_event_lines_are_delivered(client, pid, repo):
    proj = proj_of(client, pid)
    (repo / "a.py").write_text("1\n")
    lead = running_turn(proj.db, "lead", pid=pid)[1]
    t, hdr = start(client, pid, "qa")  # qa has no voice on F-1: only its own request's events are relevant
    c = client.post("/internal/commits", json={"feature": "F-1", "files": ["a.py"], "message": "a"},
                    headers=running_turn(proj.db, "qa", pid=pid)[1]).json()
    news(client, hdr)  # the commit card itself
    client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=lead)
    line = proj.db.messages("feature-1", limit=1)[-1]
    assert line["ref"] == f"commit_event:{c['id']}" and line["commit"] is None  # no second commit card in the UI
    assert f"Lead approved commit #{c['id']}" in news(client, hdr)


def test_guard_refuses_a_decision_once(client, pid):
    proj = proj_of(client, pid)
    t, hdr = start(client, pid, "lead")
    m = say(proj, "feature-1", "frontend", "wait, #34 was superseded")
    body = {"conversation": "feature-1", "text": "@master commit #34", "type": "Decision"}
    n = len(proj.db.messages("feature-1", limit=100))
    r = client.post("/internal/messages", json=body, headers=hdr)
    assert r.status_code == 409
    guard = r.json()["detail"]["guard"]
    assert guard.startswith("---\n**Not done: 1 new messages since your turn started.**") and f"#{m['id']}" in guard
    assert len(proj.db.messages("feature-1", limit=100)) == n  # nothing posted
    assert news(client, hdr) == ""  # the refusal delivered it
    assert client.post("/internal/messages", json=body, headers=hdr).status_code == 200  # the retry passes
    # a plain Message is never guarded; news elsewhere doesn't block a Decision here
    say(proj, "feature-1", "qa", "more")
    assert client.post("/internal/messages", json={**body, "type": "Message"}, headers=hdr).status_code == 200
    say(proj, "general", "product", "@lead elsewhere", ["lead"])
    t2, hdr2 = start(client, pid, "lead")
    say(proj, "general", "product", "@lead other feature", ["lead"])
    assert client.post("/internal/messages", json=body, headers=hdr2).status_code == 200


def test_guard_on_review_request_and_a_vs_b(client, pid, repo):
    proj = proj_of(client, pid)
    (repo / "a.py").write_text("1\n")
    backend = running_turn(proj.db, "backend", pid=pid)[1]
    old = client.post("/internal/commits", json={"feature": "F-1", "files": ["a.py"], "message": "a"}, headers=backend).json()
    t, lead = start(client, pid, "lead")
    client.post("/internal/commits", json={"feature": "F-1", "files": ["a.py"], "message": "a2"}, headers=backend)
    r = client.post(f"/internal/commits/{old['id']}/review", json={"decision": "approve"}, headers=lead)
    assert r.status_code == 409 and f"Commit #{old['id']} superseded" in r.json()["detail"]["guard"]
    r = client.post(f"/internal/commits/{old['id']}/review", json={"decision": "approve"}, headers=lead)
    assert r.status_code == 409 and r.json()["detail"] == f"commit #{old['id']} is rejected"  # the state's own 409
    # request_commit: the feature conversation
    t, fe = start(client, pid, "frontend")
    say(proj, "feature-1", "lead", "@frontend hold the UI half", ["frontend"])
    (repo / "u.ts").write_text("1\n")
    body = {"feature": "F-1", "files": ["u.ts"], "message": "ui"}
    r = client.post("/internal/commits", json=body, headers=fe)
    assert r.status_code == 409 and "hold the UI half" in r.json()["detail"]["guard"]
    assert proj.db.commits("lead_review", limit=100)[0]["files"] != ["u.ts"]  # nothing filed
    assert client.post("/internal/commits", json=body, headers=fe).status_code == 200
    # a_vs_b: the feature conversation
    t, lead2 = start(client, pid, "lead")
    say(proj, "feature-1", "frontend", "I still disagree")
    card = {"questions": [{"question": "Where?", "header": "Where", "options": [{"label": "A"}, {"label": "B"}]}],
            "feature": "F-1", "purpose": "a_vs_b"}
    r = client.post("/internal/questions", json=card, headers=lead2)
    assert r.status_code == 409 and "I still disagree" in r.json()["detail"]["guard"]
    assert client.post("/internal/questions", json=card, headers=lead2).status_code == 200


def test_watermark_set_at_prompt_and_cleared_at_turn_end(db, home, monkeypatch):
    from aiteams.orchestrator import scheduler as sched_mod
    from aiteams.orchestrator import turn as turn_mod
    from aiteams.runtime import AppState, Project
    from tests.conftest import FakeHub
    from tests.test_claude_proc import INIT, RESULT, fake_cmd, fake_env
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env([INIT, RESULT]))
    st = AppState(7799)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    m = proj.db.add_message("dm-qa", "master", "Message", "hi", [], None)
    seen = {}
    real = turn_mod.TurnRunner._prompt

    def spy(self, kind, ids, handoff):
        seen["wm"] = proj.delivered.get(self.turn["id"])
        return real(self, kind, ids, handoff)
    monkeypatch.setattr(turn_mod.TurnRunner, "_prompt", spy)
    s = sched_mod.Scheduler(st)
    runner = turn_mod.TurnRunner(st, proj, proj.db.add_wake("qa", "dm-qa", [m["id"]], "master"))

    async def go():
        await s._run(("p1", "qa"), runner)
    asyncio.run(go())
    assert seen["wm"] == m["id"] and proj.delivered == {}
    proj.db.close()


def run_mcp(daemon_url, msgs, tmp_path):
    env = {"AITEAMS_DAEMON_URL": daemon_url, "AITEAMS_PROJECT": "p", "AITEAMS_TURN": "1", "AITEAMS_TURN_TOKEN": "t",
           "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-B", str(ROOT / "aiteams" / "mcp_server.py")],
                         input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True, env=env,
                         timeout=30)
    return [json.loads(l) for l in out.stdout.splitlines() if l.strip()]


def test_mcp_appends_the_block_and_survives_a_failing_endpoint(tmp_path):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/internal/new_messages"):
                if H.fail:
                    return self._send(500, {"detail": "boom"})
                return self._send(200, {"text": "---\n**1 new messages since your turn started.**" +
                                        (" shown=" + self.path.split("shown=")[1] if "shown=" in self.path else "")})
            return self._send(200, [])

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/internal/commits/9/review":
                return self._send(409, {"detail": {"guard": "---\n**Not done: 1 new messages**"}})
            return self._send(200, {"id": 5, "conversation_id": "general"})
    H.fail = False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}}
    try:
        res = {r["id"]: r for r in run_mcp(url, [
            call(1, "post_message", {"conversation": "general", "text": "x"}),
            call(2, "get_messages", {"conversation": "feature-1"}),
            call(3, "review_commit", {"commit": 9, "decision": "approve"}),
            call(4, "permission", {"tool_name": "Bash", "input": {}})], tmp_path)}
        assert res[1]["result"]["content"][0]["text"] == "posted #5\n\n---\n**1 new messages since your turn started.**"
        assert res[2]["result"]["content"][0]["text"].endswith("shown=feature-1")
        assert res[3]["result"]["isError"] and res[3]["result"]["content"][0]["text"] == \
            "error: ---\n**Not done: 1 new messages**"
        assert "new messages" not in res[4]["result"]["content"][0]["text"]
        H.fail = True
        res = run_mcp(url, [call(1, "post_message", {"conversation": "general", "text": "x"})], tmp_path)
        assert res[0]["result"] == {"content": [{"type": "text", "text": "posted #5"}], "isError": False}
    finally:
        srv.shutdown()
