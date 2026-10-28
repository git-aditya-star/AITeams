"""After-turn bookkeeping: Stop lines, fail streak, context-based rotation, handoff/standup progress."""
from __future__ import annotations

import json
import time

from ..config import MAX_TURNS_ERROR, ROLES, role_name
from . import receipts
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


async def system_wake(st, proj, role: str, conversation_id: str, text: str, turn_id: int | None = None) -> dict:
    """F-37: post a System line in `conversation_id` that mentions `role` and wake that role with it. A System line never
    wakes anyone through the router, so the wake is queued here with origin `system`: merged into the role's queued
    normal wake if it has one (like router.route; a Master-origin wake keeps its priority), else a new one. Marks the
    message queued for the role, broadcasts it and notifies the scheduler. Returns the message."""
    db = proj.db
    msg = db.add_message(conversation_id, "system", "System", text, [role], turn_id)
    existing = db.queued_wake(role)
    if existing:
        db.merge_wake(existing["id"], conversation_id, [msg["id"]], "system")
    else:
        db.add_wake(role, conversation_id, [msg["id"]], "system")
    await receipts.mark(db, proj.hub, [msg["id"]], role, "queued")
    await proj.hub.broadcast("message", db.message(msg["id"]))
    if st.scheduler:
        st.scheduler.notify()
    return db.message(msg["id"])


async def _continue_capped(st, proj, role: str, turn: dict) -> None:
    """F-37: a normal turn that stopped at the --max-turns cap used to end silently (state done, nothing woken), with the
    work half done in the tree. Re-wake the role with a System line, up to `max_turn_continues` capped turns in a row;
    after that tell the Manager instead (and the Master, for the Manager's own stall, which wakes nobody)."""
    db = proj.db
    n, limit = db.cap_streak(role), int(st.settings.get("max_turn_continues", 2))
    cap = int(st.settings.get("max_turns", 100))
    conv = turn.get("conversation_id") or "dm-" + role
    name = role_name(role)
    if n <= limit:
        text = (f"{name}'s turn stopped at the {cap}-step limit before it finished; continuing automatically ({n} of {limit}). "
                f"{name}: check your files and this chat, finish what you were doing, then file or report.")
        await system_wake(st, proj, role, conv, text, turn["id"])
    elif role != "manager":
        text = f"{name} hit the {cap}-step limit {n} turns in a row; not continuing automatically. @manager please check."
        await system_wake(st, proj, "manager", conv, text, turn["id"])
    else:  # the Manager's own stall can't be handed to the Manager
        text = f"{name} hit the {cap}-step limit {n} turns in a row; not continuing automatically. @master please check."
        msg = db.add_message(conv, "system", "System", text, ["master"], turn["id"])
        await proj.hub.broadcast("message", db.message(msg["id"]))
    row = db.add_activity("max_turns", text.split(". ")[0] + ".", role, conversation_id=conv)
    await proj.hub.broadcast("activity", row)


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
        row = db.add_activity("stopped", f"{role}'s turn was stopped by the Master", role, conversation_id=turn.get("conversation_id"))
        await proj.hub.broadcast("activity", row)
    elif reason == "manager":
        why = getattr(runner, "interrupt_note", None) or "no reason given"
        await _dm(proj, role, f"Turn interrupted by the Manager: {why}", turn["id"])
        row = db.add_activity("interrupted", f"Manager interrupted {role}: {why}", role, conversation_id=turn.get("conversation_id"))
        await proj.hub.broadcast("activity", row)

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
    if kind in ("normal", "standup", "brainstorm") and state == "done" and not rs["rotate_reason"] and turn.get("context_tokens"):
        result = json.loads(turn["result_json"]) if turn.get("result_json") else None
        window = context_window_from(result, runner.model) or 1
        pct = turn["context_tokens"] / window
        if pct >= float(settings.get("rotate_pct", 0.6)):
            await request_rotation(st, proj, role, f"context {pct:.0%}", handoff=True)

    if kind == "normal" and state == "done" and not reason and turn.get("error") == MAX_TURNS_ERROR:
        await _continue_capped(st, proj, role, turn)  # after the rotation check: a handoff wake queued above runs first

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
    elif kind == "brainstorm":
        await _brainstorm_progress(st, proj, turn)
    elif kind == "brainstorm_compile":
        wake = db.wake(turn["wake_id"]) if turn.get("wake_id") else None
        bs = db.brainstorm(wake["ref"]) if wake and wake.get("ref") else None
        if bs and bs["state"] == "compiling":
            bs = db.update_brainstorm(bs["id"], state="failed", ended_at=time.time())
            feat = db.feature(bs["feature_id"])
            msg = db.add_message(feat["conversation_id"], "system", "System",
                                 f"Brainstorm #{bs['id']}: Product did not post a summary card; the round failed.",
                                 [], turn["id"], f"brainstorm:{bs['id']}")
            await proj.hub.broadcast("message", msg)
            await proj.hub.broadcast("brainstorm", bs)

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


async def _brainstorm_progress(st, proj, turn: dict) -> None:
    """Every listed role's brainstorm turn ended (any state) -> compiling; Product is woken to compile the card."""
    db = proj.db
    wake = db.wake(turn["wake_id"]) if turn.get("wake_id") else None
    bs = db.brainstorm(wake["ref"]) if wake and wake.get("ref") else None
    if not bs or bs["state"] != "collecting":
        return
    ended = {t["role"] for t in db.turns_for_wake_ref("brainstorm", bs["id"]) if t["state"] in ("done", "failed", "interrupted")}
    if not all(r in ended for r in bs["roles"]):
        return
    bs = db.update_brainstorm(bs["id"], state="compiling")
    db.add_wake("product", db.feature(bs["feature_id"])["conversation_id"], [], "system", kind="brainstorm_compile",
                ref=bs["id"])
    await proj.hub.broadcast("brainstorm", bs)
    if st.scheduler:
        st.scheduler.notify()
