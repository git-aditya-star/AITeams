"""One role turn: build prompt, spawn claude, stream events to DB/WS, classify the result."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid

from ..config import LEADS, ask_plugin_names, effort_for, model_for
from ..plugins import check_plugins
from . import receipts
from .claude_proc import ClaudeProc, build_cmd, child_env, display_argv
from .prompt import (build_brainstorm_compile_prompt, build_brainstorm_prompt, build_compile_prompt,
                     build_handoff_prompt, build_standup_prompt, build_wake_prompt, live_message_ids,
                     write_system_prompt)

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
        self.interrupt_note: str | None = None
        self.kind: str = wake.get("kind") or "normal"
        self.context_tokens: int | None = None
        self.artifact_calls: dict[str, dict] = {}  # F-29 B: Artifact tool_use id -> its input, to read the result's path
        self.model = model_for(state.settings, self.role)
        self.effort = effort_for(state.settings, self.role)
        self.task: asyncio.Task | None = None

    # ---- public --------------------------------------------------------
    def interrupt(self, reason: str, note: str | None = None) -> None:
        self.interrupt_reason = reason
        self.interrupt_note = note
        if self.proc:
            self.proc.interrupt()

    async def run(self) -> dict:
        db, hub, role = self.db, self.project.hub, self.role
        db.set_wake_state(self.wake["id"], "started")
        kind = self.kind
        message_ids = live_message_ids(db, self.wake["message_ids"]) if kind == "normal" else []
        if kind == "normal" and not message_ids:
            db.set_wake_state(self.wake["id"], "done")  # every message fell behind a fresh-start marker
            return {"state": "skipped", "id": None}
        handoff = None
        rs = db.role_state(role)
        session_id = db.active_session(role)
        if kind == "handoff" and (session_id is None or not rs["rotate_reason"]):
            db.set_wake_state(self.wake["id"], "done")
            db.set_role_state(role, rotate_handoff=0)
            return {"state": "skipped", "id": None}
        reason = "first turn"
        if rs["rotate_reason"] and not rs["rotate_handoff"] and kind == "normal":
            # Rotation is consumed by the next NORMAL turn only, so the handoff lands in a prompt that shows it.
            db.set_role_state(role, rotate_reason=None, rotate_handoff=0)
            if session_id is not None:
                session_id, reason, handoff = None, rs["rotate_reason"], db.latest_handoff(role)
        resume = session_id is not None
        if session_id is None:
            session_id = db.new_session(role, str(uuid.uuid4()), reason)
            if reason != "first turn":
                seq = db.session_info(role)["seq"]
                await self._system_message(f"Fresh session #{seq} ({reason})")
                row = db.add_activity("fresh_session", f"{role} started session #{seq} ({reason})", role,
                                      conversation_id="dm-" + role)
                await hub.broadcast("activity", row)
        turn_token = secrets.token_urlsafe(24)  # proves "turn N of role R" to /internal; only this child sees it
        self.turn = db.create_turn(role, session_id, self.wake["origin"], self.wake["conversation_id"], kind, turn_token)
        tid = self.turn["id"]
        self.turn = db.update_turn(tid, state="running", started_at=time.time(), wake_id=self.wake["id"])
        await hub.broadcast("turn_state", self.turn)
        if kind == "normal":
            await receipts.mark(db, hub, message_ids, role, "seen")

        from ..workflow import lead_open_features  # late import: workflow imports this package
        mine = lead_open_features(db, role) if role in LEADS else None  # F-22: a Lead's prompt names its features
        sys_file = write_system_prompt(self.project.path, self.project.name, role, mine)
        self.project.delivered[tid] = db.max_message_id()  # F-16: the prompt shows everything up to here
        prompt = self._prompt(kind, message_ids, handoff)
        db.update_turn(tid, prompt=prompt)
        settings = dict(self.state.settings)
        if kind != "normal":
            settings["max_turns"] = min(int(settings.get("max_turns", 40)), 10)
        result, rc = None, None
        for attempt in (1, 2):
            env = child_env(self.state.daemon_url, self.project.id, self.project.path, role, tid,
                            int(self.state.settings.get("ask_timeout_s", 600)), turn_token, ask_plugin_names(settings))
            cmd = build_cmd(settings, role, env, sys_file, session_id, resume, self.model, self.effort)
            await self._on_event({"type": "aiteams_cmd", "model": self.model, "effort": self.effort,
                                  "argv": display_argv(cmd)})
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
    def _prompt(self, kind: str, message_ids: list[int], handoff: dict | None) -> str:
        db, role = self.db, self.role
        if kind == "handoff":
            return build_handoff_prompt(db, role, db.role_state(role)["rotate_reason"])
        if kind in ("standup", "standup_compile"):
            su = db.standup(self.wake.get("ref") or 0) or {"id": self.wake.get("ref"), "requested_by": "?", "start_message_id": 0}
            if kind == "standup":
                return build_standup_prompt(db, role, su)
            from ..workflow import dashboard_features, team_status
            return build_compile_prompt(db, self.project.path, su, team_status(self.state, self.project),
                                        dashboard_features(self.state, self.project))
        if kind in ("brainstorm", "brainstorm_compile"):
            bs = db.brainstorm(self.wake.get("ref") or 0)
            if bs is None:
                return f"# Brainstorm #{self.wake.get('ref')} no longer exists. Do nothing and end your turn.\n"
            if kind == "brainstorm":
                return build_brainstorm_prompt(db, self.project.path, role, bs)
            return build_brainstorm_compile_prompt(db, self.project.path, bs)
        return build_wake_prompt(db, role, message_ids, handoff)

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
        elif t == "system" and ev.get("subtype") == "init":
            await self._check_plugins(ev)
            await self._check_artifact_tool(ev)
        if t == "assistant":
            self._note_artifact_calls(ev)
        elif t == "user":
            await self._capture_artifacts(ev)
        stored = self.db.add_turn_event(self.turn["id"], ev)
        await self.project.hub.broadcast("turn_event", stored)

    async def _check_artifact_tool(self, init: dict) -> None:
        """F-29 B: the switch that gives role turns the Artifact tool is undocumented. If it is set and the tool is still
        missing from init, say so once per daemon run in `general` (local mocks still work). Never fails the turn."""
        from ..workflow import ARTIFACT_MISSING  # late import: workflow imports this package
        try:
            tools = init.get("tools")
            if (self.proc and self.proc.env.get("CLAUDE_CODE_ARTIFACT") and isinstance(tools, list)
                    and "Artifact" not in tools and not self.state.artifact_missing_notified):
                self.state.artifact_missing_notified = True
                msg = self.db.add_message("general", "system", "System", ARTIFACT_MISSING, [], None)
                await self.project.hub.broadcast("message", msg)
        except Exception:
            pass

    def _note_artifact_calls(self, ev: dict) -> None:
        for block in (ev.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Artifact":
                inp = block.get("input")
                self.artifact_calls[str(block.get("id"))] = inp if isinstance(inp, dict) else {}

    async def _capture_artifacts(self, ev: dict) -> None:
        """F-29 B: a `Published <path> at https://claude.ai/artifact/<id> (Version n` tool result of an Artifact call is
        recorded (pending row filled, or the page's row bumped) and answered with a link card. Best effort."""
        from ..workflow import ARTIFACT_RESULT_RE, record_artifact  # late import: workflow imports this package
        try:
            for block in (ev.get("message") or {}).get("content") or []:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                call_id = str(block.get("tool_use_id"))
                if call_id not in self.artifact_calls:
                    continue
                content = block.get("content")
                text = content if isinstance(content, str) else " ".join(
                    c.get("text", "") for c in content or [] if isinstance(c, dict))
                m = ARTIFACT_RESULT_RE.match(text)
                if m:
                    await record_artifact(self.state, self.project, self.role, self.turn["id"] if self.turn else None,
                                          self.artifact_calls.pop(call_id), m.group(1), m.group(2), int(m.group(3)))
        except Exception:
            pass

    async def _check_plugins(self, init: dict) -> None:
        """F-23: flag On plugins the turn did not load. Best effort: it must never fail the turn."""
        try:
            await check_plugins(self.state, self.project, self.role, init)
        except Exception:
            pass

    async def _end(self, result: dict | None, rc: int | None, state: str, error: str | None) -> dict:
        assert self.turn
        tid = self.turn["id"]
        fields = dict(state=state, ended_at=time.time(), exit_code=rc, error=error,
                      context_tokens=self.context_tokens)
        if result is not None:
            fields["result_json"] = json.dumps(result)
            fields["cost_usd"] = result.get("total_cost_usd")
        self.turn = self.db.update_turn(tid, **fields)
        try:
            self.db.drop_pending_artifacts(tid)  # F-29 B: an allowed publish whose capture never came is given back
        except Exception:
            pass
        await self.project.hub.broadcast("turn_state", self.turn)
        return self.turn

    async def _system_message(self, text: str) -> None:
        msg = self.db.add_message("dm-" + self.role, "system", "System", text, [], self.turn["id"] if self.turn else None)
        await self.project.hub.broadcast("message", msg)
