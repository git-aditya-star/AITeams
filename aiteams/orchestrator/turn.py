"""One role turn: build prompt, spawn claude, stream events to DB/WS, classify the result."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from ..config import model_for
from . import receipts
from .claude_proc import ClaudeProc, build_cmd, child_env
from .prompt import build_wake_prompt, write_system_prompt

DEFAULT_CONTEXT_WINDOW = 200000


def usage_tokens(ev: dict) -> int | None:
    usage = ((ev.get("message") or {}).get("usage")) or {}
    if not usage:
        return None
    return int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) + \
        int(usage.get("cache_creation_input_tokens") or 0)


def context_window_from(result: dict | None, model: str | None = None) -> int:
    mu = (result or {}).get("modelUsage") or {}
    if model and model in mu and mu[model].get("contextWindow"):
        return int(mu[model]["contextWindow"])
    for v in mu.values():
        if v.get("contextWindow"):
            return int(v["contextWindow"])
    return DEFAULT_CONTEXT_WINDOW


def classify(result: dict | None, interrupted_by_us: bool) -> tuple[str, str | None]:
    if result is None:
        return ("interrupted", "no result (interrupted)") if interrupted_by_us else ("failed", "process ended without a result")
    if interrupted_by_us:
        return "interrupted", None
    sub = result.get("subtype")
    if sub == "success":
        return "done", None
    if sub == "error_max_turns":
        return "done", "max turns reached"
    msg = result.get("result") if isinstance(result.get("result"), str) else None
    errs = result.get("errors")
    return "failed", msg or (json.dumps(errs) if errs else sub) or "error"


class TurnRunner:
    def __init__(self, state, project, wake: dict):
        self.state, self.project, self.wake = state, project, wake
        self.db = project.db
        self.role = wake["role"]
        self.turn: dict | None = None
        self.proc: ClaudeProc | None = None
        self.interrupt_reason: str | None = None
        self.context_tokens: int | None = None
        self.model = model_for(state.settings, self.role)
        self.task: asyncio.Task | None = None

    # ---- public --------------------------------------------------------
    def interrupt(self, reason: str) -> None:
        self.interrupt_reason = reason
        if self.proc:
            self.proc.interrupt()

    async def run(self) -> dict:
        db, hub, role = self.db, self.project.hub, self.role
        db.set_wake_state(self.wake["id"], "started")
        session_id = db.active_session(role)
        resume = session_id is not None
        if session_id is None:
            session_id = db.new_session(role, str(uuid.uuid4()), "first turn")
        self.turn = db.create_turn(role, session_id, self.wake["origin"], self.wake["conversation_id"])
        tid = self.turn["id"]
        self.turn = db.update_turn(tid, state="running", started_at=time.time(), wake_id=self.wake["id"])
        await hub.broadcast("turn_state", self.turn)
        await receipts.mark(db, hub, self.wake["message_ids"], role, "seen")

        sys_file = write_system_prompt(self.project.path, self.project.name, role)
        prompt = build_wake_prompt(db, role, self.wake["message_ids"])
        result, rc = None, None
        for attempt in (1, 2):
            env = child_env(self.state.daemon_url, self.project.id, self.project.path, role, tid,
                            int(self.state.settings.get("ask_timeout_s", 600)))
            cmd = build_cmd(self.state.settings, role, env, sys_file, session_id, resume, self.model)
            self.proc = ClaudeProc(cmd, self.project.path, env)
            try:
                await self.proc.start()
            except OSError as e:
                return await self._end(None, None, "failed", f"could not start claude: {e}")
            db.update_turn(tid, pid=self.proc.pid)
            self.proc.send_user(prompt)
            result = await self._stream()
            rc = await self.proc.finish()
            resume_failed = (result is None and not self.proc.saw_init and rc not in (0, None)
                             and any("session" in s.lower() for s in self.proc.stderr))
            if attempt == 1 and resume and resume_failed and self.interrupt_reason is None:
                session_id = db.new_session(role, str(uuid.uuid4()), "resume failed")
                db.update_turn(tid, session_id=session_id)
                resume = False
                await self._system_message(
                    f"Could not resume session for {role}; started a fresh session. stderr: "
                    + " | ".join(self.proc.stderr[-3:])[:500])
                continue
            break
        state, error = classify(result, self.interrupt_reason is not None)
        if result is not None and result.get("num_turns") == 0 and state == "done":
            error = "no assistant output (num_turns=0); the wake may not have been processed"
        if result is None and self.interrupt_reason is None and self.proc and self.proc.stderr:
            error = (error or "") + ": " + " | ".join(self.proc.stderr[-3:])[:500]
        return await self._end(result, rc, state, error)

    # ---- internals -----------------------------------------------------
    async def _stream(self) -> dict | None:
        """Read events until `result` or EOF; SIGINT on turn_timeout_s."""
        assert self.proc and self.turn
        timeout = float(self.state.settings.get("turn_timeout_s", 1200))
        deadline = time.monotonic() + timeout
        it = self.proc.events().__aiter__()
        result = None
        zero_result = None  # a `result` with num_turns=0 before any assistant output (pending task notification)
        saw_assistant = False
        pending: asyncio.Future | None = None  # kept across timeouts: cancelling __anext__ would close the generator
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and self.interrupt_reason is None:
                self.interrupt("timeout")
                deadline = time.monotonic() + 15
                continue
            if pending is None:
                pending = asyncio.ensure_future(it.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=max(0.05, remaining))
            if not done:
                if self.interrupt_reason is not None:
                    pending.cancel()
                    return result  # SIGINT'd and still no result; finish() escalates
                continue
            try:
                ev = pending.result()
            except StopAsyncIteration:
                return result or zero_result
            finally:
                pending = None
            await self._on_event(ev)
            if ev.get("type") == "assistant":
                saw_assistant = True
            if ev.get("type") == "result":
                if not saw_assistant and ev.get("num_turns") == 0 and zero_result is None:
                    zero_result = ev  # claude flushed a queued notification; our message is still pending
                    continue
                result = ev
                return result

    async def _on_event(self, ev: dict) -> None:
        assert self.turn
        t = ev.get("type")
        if t == "assistant":
            n = usage_tokens(ev)
            if n:
                self.context_tokens = n
        elif t == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            self.db.add_rate_limit(info)
            await self.state.set_rate_limit(info)
            return
        stored = self.db.add_turn_event(self.turn["id"], ev)
        await self.project.hub.broadcast("turn_event", stored)

    async def _end(self, result: dict | None, rc: int | None, state: str, error: str | None) -> dict:
        assert self.turn
        tid = self.turn["id"]
        fields = dict(state=state, ended_at=time.time(), exit_code=rc, error=error,
                      context_tokens=self.context_tokens)
        if result is not None:
            fields["result_json"] = json.dumps(result)
            fields["cost_usd"] = result.get("total_cost_usd")
        self.turn = self.db.update_turn(tid, **fields)
        await self.project.hub.broadcast("turn_state", self.turn)
        return self.turn

    async def _system_message(self, text: str) -> None:
        msg = self.db.add_message("dm-" + self.role, "system", "System", text, [], self.turn["id"] if self.turn else None)
        await self.project.hub.broadcast("message", msg)
