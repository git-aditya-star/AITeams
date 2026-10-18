"""Stream parsing + exit/SIGINT paths with a fake `claude` child (python -c)."""
import asyncio
import json
import sys

import pytest

from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.claude_proc import HOOK, HOOK_TOOLS, ClaudeProc, display_argv, aiteams_env, build_cmd, child_env
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
time.sleep(float(os.environ.get("FAKE_STARTUP_DELAY") or 0))  # a slow interpreter start, as on a loaded machine
signal.signal(signal.SIGINT, on_int)
if os.environ.get("FAKE_READY"):
    open(os.environ["FAKE_READY"], "w").close()  # the SIGINT handler is installed: safe to start a timeout now
line = sys.stdin.readline()
assert json.loads(line)["type"] == "user"
for e in events: out(e)
if hang:
    while True: time.sleep(0.1)
'''


def fake_cmd():
    return [sys.executable, "-B", "-c", FAKE]


def fake_env(events, hang=False, **extra):
    import os
    return {**os.environ, "FAKE_EVENTS": json.dumps(events), "FAKE_HANG": "1" if hang else "0",
            "PYTHONDONTWRITEBYTECODE": "1", **extra}


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
    evs = proj.db.turn_events(t["id"])
    assert [e["type"] for e in evs] == ["aiteams_cmd", "system", "assistant", "result"]  # rate_limit stored apart
    cmd_ev = evs[0]["data"]
    assert cmd_ev["model"] == "opus" and cmd_ev["effort"] is None and cmd_ev["argv"][0] == sys.executable
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

    # F-26: the 1 s turn timeout starts when the stream starts, so on a loaded machine a slow-starting child could get
    # its SIGINT before it had installed the handler (it died with no result). The child is made slow on purpose (1.3 s
    # before the handler) and the test waits for its ready marker before the clock can start, so it never depends on load.
    ready = db.dir / "child-ready"
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env(
        [INIT], hang=True, FAKE_READY=str(ready), FAKE_STARTUP_DELAY="1.3"))
    orig_start = ClaudeProc.start

    async def start_when_ready(self):
        await orig_start(self)
        for _ in range(3000):  # up to 30 s for the child to be ready; no timing assumption about the machine
            if ready.exists():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the fake child never became ready")
    monkeypatch.setattr(ClaudeProc, "start", start_when_ready)
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


def test_child_env_carries_turn_token_not_master_token(monkeypatch):
    monkeypatch.setenv("AITEAMS_TOKEN", "master-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-no")
    env = child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "backend", 7, 600, "turn-secret")
    assert env["AITEAMS_TURN_TOKEN"] == "turn-secret" and env["AITEAMS_TURN"] == "7"
    assert "AITEAMS_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    fwd = aiteams_env(env)
    assert fwd["AITEAMS_TURN_TOKEN"] == "turn-secret" and "AITEAMS_TOKEN" not in fwd


def test_build_cmd_keeps_local_settings_only_and_our_hook():
    env = child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "backend", 7, 600, "turn-secret")
    cmd = build_cmd({"max_turns": 40}, "backend", env, "/tmp/sys.md", "s1", False, "opus")
    assert cmd[cmd.index("--setting-sources") + 1] == "local"  # never "user": no Master CLAUDE.md or hooks
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert "enabledPlugins" not in settings  # F-23: an empty list sends nothing (tests/test_plugins.py has the rest)
    assert "Skill" in HOOK_TOOLS.split("|")
    [pre] = settings["hooks"]["PreToolUse"]
    assert HOOK in pre["hooks"][0]["command"]


def test_build_cmd_effort_flag_and_display_argv():
    """F-9: --effort follows --model only when an effort resolves; the event argv hides the JSON args."""
    env = child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "qa", 7, 600, "turn-secret")
    plain = build_cmd({"max_turns": 40}, "qa", env, "/tmp/sys.md", "s1", False, "opus")
    assert "--effort" not in plain
    cmd = build_cmd({"max_turns": 40}, "qa", env, "/tmp/sys.md", "s1", False, "sonnet", "low")
    i = cmd.index("--model")
    assert cmd[i:i + 4] == ["--model", "sonnet", "--effort", "low"]
    shown = display_argv(cmd)
    assert shown[shown.index("--settings") + 1] == "<json>" and shown[shown.index("--mcp-config") + 1] == "<json>"
    assert "turn-secret" not in " ".join(shown)  # the turn token lives in the mcp-config env
    assert [a for a in shown if a != "<json>"] == [a for a in cmd if not a.startswith("{")]


def test_turn_records_resolved_effort(db, home, monkeypatch):
    from aiteams.orchestrator.turn import TurnRunner
    from aiteams.runtime import AppState, Project
    from tests.conftest import FakeHub
    seen = []
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: seen.append(a) or fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env([INIT, RESULT]))
    st = AppState(7799)
    st.settings.update(models={"qa": "sonnet"}, efforts={"qa": "low"}, default_effort="high")
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    m = proj.db.add_message("dm-qa", "master", "Message", "hi", [], None)
    t = asyncio.run(TurnRunner(st, proj, proj.db.add_wake("qa", "dm-qa", [m["id"]], "master")).run())
    assert seen[-1][-2:] == ("sonnet", "low")
    ev = proj.db.turn_events(t["id"])[0]["data"]
    assert (ev["type"], ev["model"], ev["effort"]) == ("aiteams_cmd", "sonnet", "low")
    proj.db.close()


# ---- F-13: caffeinate -i -w <claude pid> keeps the Mac awake for the turn ----------------------------
# Fake caffeinate: records its argv, then waits until the -w pid is gone (like the real one) or 10 s pass.
FAKE_CAFFEINATE = r'''#!{py}
import json, os, sys, time
open(os.environ["CAFF_LOG"], "w").write(json.dumps(sys.argv[1:]))
pid = int(sys.argv[sys.argv.index("-w") + 1])
for _ in range(100):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        break
    time.sleep(0.1)
'''


def fake_caffeinate(tmp_path, monkeypatch):
    from aiteams.orchestrator import claude_proc
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "caffeinate"
    exe.write_text(FAKE_CAFFEINATE.replace("{py}", sys.executable))
    exe.chmod(0o755)
    log = tmp_path / "caff.json"
    monkeypatch.setenv("CAFF_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setattr(claude_proc.sys, "platform", "darwin")
    return log


def run_turn(tmp_path):
    async def go():
        p = ClaudeProc(fake_cmd(), str(tmp_path), fake_env([INIT, RESULT]))
        await p.start()
        caff = p._caffeinate
        p.send_user("hello")
        evs = await collect(p)
        rc = await p.finish()
        return p, caff, evs, rc
    return asyncio.run(go())


def test_caffeinate_holds_for_the_turn_and_is_reaped(tmp_path, monkeypatch):
    log = fake_caffeinate(tmp_path, monkeypatch)
    p, caff, evs, rc = run_turn(tmp_path)
    assert evs[-1]["type"] == "result" and rc == 0
    assert caff is not None and caff.returncode is not None  # reaped by finish(): no zombie
    assert json.loads(log.read_text()) == ["-i", "-w", str(p.pid)]


def test_no_caffeinate_on_path_turn_runs_as_before(tmp_path, monkeypatch):
    from aiteams.orchestrator import claude_proc
    monkeypatch.setattr(claude_proc.sys, "platform", "darwin")
    monkeypatch.setattr(claude_proc.shutil, "which", lambda name: None)
    p, caff, evs, rc = run_turn(tmp_path)
    assert caff is None and evs[-1]["type"] == "result" and rc == 0


def test_caffeinate_spawn_failure_is_swallowed(tmp_path, monkeypatch):
    from aiteams.orchestrator import claude_proc
    fake_caffeinate(tmp_path, monkeypatch)
    real = asyncio.create_subprocess_exec

    async def spawn(*args, **kw):
        if str(args[0]).endswith("caffeinate"):
            raise OSError("exec format error")
        return await real(*args, **kw)
    monkeypatch.setattr(claude_proc.asyncio, "create_subprocess_exec", spawn)
    p, caff, evs, rc = run_turn(tmp_path)
    assert caff is None and evs[-1]["type"] == "result" and rc == 0


def test_no_caffeinate_off_macos(tmp_path, monkeypatch):
    from aiteams.orchestrator import claude_proc
    log = fake_caffeinate(tmp_path, monkeypatch)
    monkeypatch.setattr(claude_proc.sys, "platform", "linux")
    p, caff, evs, rc = run_turn(tmp_path)
    assert caff is None and rc == 0 and not log.exists()
