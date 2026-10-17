"""Stream parsing + exit/SIGINT paths with a fake `claude` child (python -c)."""
import asyncio
import json
import sys

import pytest

from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.claude_proc import ClaudeProc
from aiteams.orchestrator.turn import classify, context_window_from, usage_tokens

INIT = {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-opus-5-5", "tools": ["Bash"],
        "mcp_servers": [{"name": "aiteams", "status": "connected"}]}
ASSISTANT = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
             "usage": {"input_tokens": 2, "cache_creation_input_tokens": 3327, "cache_read_input_tokens": 13447,
                       "output_tokens": 8}}, "session_id": "s1"}
RATE = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1792185000,
                                                         "rateLimitType": "five_hour"}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 2100, "num_turns": 1,
          "result": "done", "session_id": "s1", "total_cost_usd": 0.00171336,
          "usage": {"input_tokens": 2, "cache_creation_input_tokens": 3327, "cache_read_input_tokens": 13447},
          "modelUsage": {"claude-opus-5-5": {"inputTokens": 4, "costUSD": 0.00171336, "contextWindow": 1000000}}}

# Fake child: reads one stdin line, prints events, then exits (or hangs if HANG, handling SIGINT with a result).
FAKE = r'''
import json, os, signal, sys, time
events = json.loads(os.environ["FAKE_EVENTS"]); hang = os.environ.get("FAKE_HANG") == "1"
def out(e): sys.stdout.write(json.dumps(e) + "\n"); sys.stdout.flush()
def on_int(*a):
    out({"type": "result", "subtype": "success", "is_error": False, "result": "interrupted", "total_cost_usd": 0.001})
    sys.exit(0)
signal.signal(signal.SIGINT, on_int)
line = sys.stdin.readline()
assert json.loads(line)["type"] == "user"
for e in events: out(e)
if hang:
    while True: time.sleep(0.1)
'''


def fake_cmd():
    return [sys.executable, "-B", "-c", FAKE]


def fake_env(events, hang=False):
    import os
    return {**os.environ, "FAKE_EVENTS": json.dumps(events), "FAKE_HANG": "1" if hang else "0",
            "PYTHONDONTWRITEBYTECODE": "1"}


async def collect(proc):
    evs = []
    async for ev in proc.events():
        evs.append(ev)
        if ev.get("type") == "result":
            break
    return evs


def test_usage_and_cost_extraction():
    assert usage_tokens(ASSISTANT) == 16776
    assert context_window_from(RESULT, "claude-opus-5-5") == 1000000
    assert context_window_from(RESULT) == 1000000
    assert context_window_from(None) == 200000
    assert RESULT["total_cost_usd"] == 0.00171336


def test_classify():
    assert classify(RESULT, False) == ("done", None)
    assert classify({**RESULT, "subtype": "error_max_turns"}, False) == ("done", "max turns reached")
    assert classify({**RESULT, "subtype": "error_during_execution", "is_error": True, "result": "boom"}, False) \
        == ("failed", "boom")
    assert classify(RESULT, True)[0] == "interrupted"
    assert classify(None, False)[0] == "failed"
    assert classify(None, True)[0] == "interrupted"


def test_stream_parse_and_clean_exit(tmp_path):
    async def go():
        p = ClaudeProc(fake_cmd(), str(tmp_path), fake_env([INIT, ASSISTANT, RATE, {"type": "_garbage"}, RESULT]))
        await p.start()
        p.send_user("hello")
        evs = await collect(p)
        rc = await p.finish()
        return evs, rc, p
    evs, rc, p = asyncio.run(go())
    assert [e["type"] for e in evs] == ["system", "assistant", "rate_limit_event", "_garbage", "result"]
    assert p.saw_init and rc == 0


def test_sigint_escalation_on_hang(tmp_path):
    async def go():
        p = ClaudeProc(fake_cmd(), str(tmp_path), fake_env([INIT], hang=True))
        await p.start()
        p.send_user("hello")
        it = p.events().__aiter__()
        first = await it.__anext__()
        p.interrupt()
        nxt = await asyncio.wait_for(it.__anext__(), 5)
        rc = await p.finish()
        return first, nxt, rc, p
    first, nxt, rc, p = asyncio.run(go())
    assert first["type"] == "system" and nxt["type"] == "result" and nxt["result"] == "interrupted"
    assert p.interrupted and rc == 0


def test_full_turn_with_fake_claude(db, home, monkeypatch):
    """TurnRunner end to end: receipts seen, events stored, context_tokens/cost recorded, state done."""
    from aiteams.runtime import AppState, Project
    from aiteams.orchestrator.turn import TurnRunner
    from tests.conftest import FakeHub

    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env([INIT, ASSISTANT, RATE, RESULT]))
    st = AppState(7799)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    m = proj.db.add_message("dm-backend", "master", "Message", "hi", [], None)
    wake = proj.db.add_wake("backend", "dm-backend", [m["id"]], "master")
    proj.db.set_receipt(m["id"], "backend", "queued")
    t = asyncio.run(TurnRunner(st, proj, wake).run())
    assert t["state"] == "done" and t["error"] is None
    assert t["context_tokens"] == 16776 and t["cost_usd"] == 0.00171336 and t["exit_code"] == 0
    assert proj.db.receipts_for([m["id"]])[m["id"]] == [{"role": "backend", "state": "seen"}]
    types = [e["type"] for e in proj.db.turn_events(t["id"])]
    assert types == ["system", "assistant", "result"]  # rate_limit_event is stored separately
    assert proj.db.q("SELECT COUNT(*) AS n FROM rate_limits")[0]["n"] == 1
    assert proj.db.wake(wake["id"])["state"] == "started"
    assert proj.db.active_session("backend") == t["session_id"]
    assert (db.dir / "prompts" / "backend.system.md").exists()
    kinds = [e for e, _ in proj.hub.events]
    assert kinds.count("turn_state") == 2 and "turn_event" in kinds and "receipt" in kinds
    proj.db.close()


def test_turn_timeout_still_captures_sigint_result(db, home, monkeypatch):
    """turn_timeout_s elapses -> SIGINT -> the child's result is still read and recorded."""
    from aiteams.runtime import AppState, Project
    from aiteams.orchestrator.turn import TurnRunner
    from tests.conftest import FakeHub

    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env([INIT], hang=True))
    st = AppState(7799)
    st.settings["turn_timeout_s"] = 1
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    m = proj.db.add_message("dm-qa", "master", "Message", "hi", [], None)
    wake = proj.db.add_wake("qa", "dm-qa", [m["id"]], "master")
    t = asyncio.run(TurnRunner(st, proj, wake).run())
    assert t["state"] == "interrupted" and t["cost_usd"] == 0.001
    assert [e["type"] for e in proj.db.turn_events(t["id"])][-1] == "result"
    proj.db.close()
