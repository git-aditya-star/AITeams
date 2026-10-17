"""After-turn bookkeeping: Stop lines, fail streak, context-based rotation, handoff/standup progress."""
from __future__ import annotations

import json
import time

from ..config import ROLES
from .turn import context_window_from


async def _dm(proj, role: str, text: str, turn_id: int | None = None) -> None:
    msg = proj.db.add_message("dm-" + role, "system", "System", text, [], turn_id)
    await proj.hub.broadcast("message", msg)


async def broadcast_team(st, proj) -> None:
    from ..workflow import team_status
    await proj.hub.broadcast("team", {"roles": team_status(st, proj)})


async def request_rotation(st, proj, role: str, reason: str, handoff: bool) -> bool:
    """Mark a rotation pending; queue a handoff turn when asked and the role has a session. False if already pending."""
    db = proj.db
    if role not in ROLES:
        return False
    rs = db.role_state(role)
    if rs["rotate_reason"]:
        return False
    handoff = bool(handoff and db.active_session(role))
    db.set_role_state(role, rotate_reason=reason, rotate_handoff=1 if handoff else 0)
    if handoff and not db.queued_wake(role, "handoff"):
        db.add_wake(role, "dm-" + role, [], "system", kind="handoff")
    await broadcast_team(st, proj)
    if st.scheduler:
        st.scheduler.notify()
    return True


async def after_turn(st, proj, runner) -> None:
    db, role, turn = proj.db, runner.role, runner.turn
    if not turn:  # skipped wake: nothing ran, but the UI must drop its "queued" state
        await broadcast_team(st, proj)
        return
    if runner.interrupt_reason == "shutdown":
        return  # the wake is re-queued by the scheduler and runs again after restart; no bookkeeping now
    turn = db.turn(turn["id"]) or turn
    kind, state = turn.get("kind") or "normal", turn.get("state")
    settings = st.settings
    reason = runner.interrupt_reason

    if reason == "master":
        await _dm(proj, role, "Turn stopped by the Master.", turn["id"])
        db.add_activity("stopped", f"{role}'s turn was stopped by the Master", role, conversation_id=turn.get("conversation_id"))
    elif reason == "manager":
        why = getattr(runner, "interrupt_note", None) or "no reason given"
        await _dm(proj, role, f"Turn interrupted by the Manager: {why}", turn["id"])
        db.add_activity("interrupted", f"Manager interrupted {role}: {why}", role, conversation_id=turn.get("conversation_id"))

    rs = db.role_state(role)
    if kind == "normal":
        if state == "failed":
            streak = rs["fail_streak"] + 1
            limit = int(settings.get("fail_streak_rotate", 3))
            if streak >= limit and not rs["rotate_reason"]:
                db.set_role_state(role, fail_streak=0)
                await request_rotation(st, proj, role, "errors", handoff=False)
                await _dm(proj, role, f"{limit} failed turns in a row; the next wake starts a fresh session.", turn["id"])
            else:
                db.set_role_state(role, fail_streak=streak)
        elif state in ("done", "interrupted") and rs["fail_streak"]:
            db.set_role_state(role, fail_streak=0)

    rs = db.role_state(role)
    if kind in ("normal", "standup") and state == "done" and not rs["rotate_reason"] and turn.get("context_tokens"):
        result = json.loads(turn["result_json"]) if turn.get("result_json") else None
        window = context_window_from(result, runner.model) or 1
        pct = turn["context_tokens"] / window
        if pct >= float(settings.get("rotate_pct", 0.6)):
            await request_rotation(st, proj, role, f"context {pct:.0%}", handoff=True)

    if kind == "handoff":
        db.set_role_state(role, rotate_handoff=0)
        if not db.handoff_in_turn(role, turn.get("started_at") or turn["created_at"]):
            await _dm(proj, role, "Handoff turn ended without `write_handoff`; the fresh session starts without a handoff.", turn["id"])

    if kind == "standup":
        await _standup_progress(st, proj, turn)
    elif kind == "standup_compile":
        wake = db.wake(turn["wake_id"]) if turn.get("wake_id") else None
        su = db.standup(wake["ref"]) if wake and wake.get("ref") else None
        if su and su["state"] == "compiling":
            db.update_standup(su["id"], state="failed", ended_at=time.time())
            msg = db.add_message("standup", "system", "System", f"Standup #{su['id']}: the Manager did not save a report.", [], turn["id"])
            await proj.hub.broadcast("message", msg)
            await proj.hub.broadcast("standup", db.standup(su["id"]))

    await broadcast_team(st, proj)


async def _standup_progress(st, proj, turn: dict) -> None:
    db = proj.db
    wake = db.wake(turn["wake_id"]) if turn.get("wake_id") else None
    su = db.standup(wake["ref"]) if wake and wake.get("ref") else None
    if not su or su["state"] != "collecting":
        return
    ended = {t["role"] for t in db.turns_for_wake_ref("standup", su["id"]) if t["state"] in ("done", "failed", "interrupted")}
    if not all(r in ended for r in su["roles"]):
        return
    su = db.update_standup(su["id"], state="compiling")
    db.add_wake("manager", "standup", [], "system", kind="standup_compile", ref=su["id"])
    await proj.hub.broadcast("standup", su)
    if st.scheduler:
        st.scheduler.notify()
