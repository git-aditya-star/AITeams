"""F-38: unstick conversations when the whole team goes idle.

A feature is *stalled* when its newest word is waiting on a role (a commit in Lead review, a message that @mentions a
role, a role's Handoff/Question/Blocker/Concern that mentions nobody) and nothing is running. The check is an event,
not a timer: the scheduler calls `check_idle` when a project's last turn ends with nothing queued, and once at daemon
start. Each stalled feature climbs a ladder kept in the `stalls` table, one rung per idle:
  1. nudge the role (a System line + a normal wake)   2. tell the Manager (one list per idle)   3. stuck (no more wakes)
`recheck` clears a row as soon as anyone moves the conversation on, so nothing loops by itself. Long turns (running or
queued past `stuck_turn_s`) are flagged once to the Manager and never stopped."""
from __future__ import annotations

import time

from . import workflow
from .config import ROLES, role_name
from .orchestrator import lifecycle

NUDGED, MANAGER_TOLD, STUCK = 1, 2, 3
STALL_KINDS = ("review", "message", "unaddressed")


def find(proj, feat: dict) -> dict | None:
    """The role an open feature is waiting on, or None: workflow._waits_on without the running/queued rules (the caller
    decides idleness). Anything waiting on the Master, and a wait on oneself, is never a stall. `key` = (who, ref)."""
    if feat["phase"] == "done" or feat["archived"]:
        return None
    w = workflow._waits_on(None, proj, feat, {}, {})  # st is unused by _waits_on
    if not w or w["who"] not in ROLES or w["kind"] not in STALL_KINDS:
        return None
    return {**w, "key": (w["who"], w["ref"])}


def _reason(db, s: dict) -> str:
    kind, n = s["kind"], int(s["ref"].split(":")[1])
    if kind == "review":
        return f"commit request #{n} waits on its review"
    if kind == "message":
        return f"#{n} asks for action, with no reply"
    m = db.message(n) or {}
    return f"#{n} ({m.get('type', 'message')}) mentions nobody"


async def _frame(proj, fid: int) -> None:
    await proj.hub.broadcast("feature", proj.db.feature(fid))


async def on_idle(st, proj) -> list[dict]:
    """Climb the ladder once for every open feature (the project is idle). Returns [{feature, step, who}] for each change."""
    db, changes, told = proj.db, [], []
    open_ids = set()
    for feat in sorted(db.features(), key=lambda f: f["id"]):  # oldest first: the lists read in order
        open_ids.add(feat["id"])
        s, row = find(proj, feat), db.stall_row(feat["id"])
        if s is None:
            if db.delete_stall(feat["id"]):
                await _frame(proj, feat["id"])
            continue
        who, reason = s["who"], _reason(db, s)
        if row is None or (row["who"], row["ref"]) != s["key"]:
            db.set_stall(feat["id"], who, s["kind"], s["ref"], NUDGED)
            await lifecycle.system_wake(st, proj, who, feat["conversation_id"], f"Nudged @{who}: {reason}.")
            step = NUDGED
        elif row["step"] == NUDGED:
            db.set_stall(feat["id"], who, s["kind"], s["ref"], MANAGER_TOLD)
            told.append(f"- {feat['key']} · waiting on {role_name(who)} · {reason}")
            step = MANAGER_TOLD
        elif row["step"] == MANAGER_TOLD:
            db.set_stall(feat["id"], who, s["kind"], s["ref"], STUCK)
            msg = db.add_message(feat["conversation_id"], "system", "System",
                                 f"Stuck: still waiting on {role_name(who)} after a nudge and the Manager. "
                                 "No more automatic wakes.", [], None)
            await proj.hub.broadcast("message", db.message(msg["id"]))
            step = STUCK
        else:
            continue
        changes.append({"feature": feat["key"], "step": step, "who": who})
        await _frame(proj, feat["id"])
    for row in db.stall_rows():  # a feature that left the open list (done, archived) drops its row
        if row["feature_id"] not in open_ids:
            db.delete_stall(row["feature_id"])
    if told:
        await lifecycle.system_wake(st, proj, "manager", "dm-manager",
                                    "These conversations are still waiting after a nudge:\n" + "\n".join(told))
    return changes


async def recheck(st, proj, feature_id: int | None) -> None:
    """A no-op unless the feature has a ladder row. Otherwise recompute: if its stall is gone or is a different wait
    (someone acted), delete the row and tell the UI."""
    row = proj.db.stall_row(feature_id) if feature_id is not None else None
    if row is None:
        return
    feat = proj.db.feature(feature_id)
    s = find(proj, feat) if feat else None
    if s is None or s["key"] != (row["who"], row["ref"]):
        proj.db.delete_stall(feature_id)
        await _frame(proj, feature_id)


def is_idle(st, proj) -> bool:
    """No turn running for the project, nothing queued, and the project neither paused nor rate-limited."""
    if proj.paused or proj.schema_ahead or st.rate_limit_active():
        return False
    running = st.scheduler.running if st.scheduler else {}
    return not any(k[0] == proj.id for k in running) and not proj.db.queued_wakes()


async def check_idle(st, proj) -> list[dict]:
    return await on_idle(st, proj) if is_idle(st, proj) else []


# ---- long turns: flagged, never stopped -------------------------------------------------------------------------------
def age(st, proj, role: str, now: float | None = None, lane: str = "") -> float | None:
    """Seconds the role's running turn has been running, else its oldest queued wake has waited; None if neither.
    F-41: of one lane of the role ("" = its main session)."""
    now = now or time.time()
    runner = st.scheduler.running.get((proj.id, role, lane)) if st.scheduler else None
    if runner is not None:
        t = proj.db.turn(runner.turn["id"]) if runner.turn else None
        return now - t["started_at"] if t and t.get("started_at") else None
    waits = [w["created_at"] for w in proj.db.queued_wakes() if w["role"] == role and (w.get("lane") or "") == lane]
    return now - min(waits) if waits else None


def is_stuck(st, proj, role: str, now: float | None = None, lane: str = "") -> bool:
    a = age(st, proj, role, now, lane)
    return a is not None and a > float(st.settings.get("stuck_turn_s", 1200))


async def flag_long_turns(st, proj, flagged: set, now: float | None = None) -> list[str]:
    """Flag each turn running, and each wake queued, for more than `stuck_turn_s`, once (`flagged` remembers them):
    a System line in dm-manager that wakes the Manager (unless the slow role is the Manager), an activity row, and a
    team frame. Nothing is stopped."""
    db, now = proj.db, now or time.time()
    limit = float(st.settings.get("stuck_turn_s", 1200))
    items = []
    for (pid, role, lane), runner in (st.scheduler.running.items() if st.scheduler else []):
        t = db.turn(runner.turn["id"]) if pid == proj.id and runner.turn else None
        if t and t.get("started_at"):
            items.append((("turn", t["id"]), role, "running", now - t["started_at"], lane))
    items += [(("wake", w["id"]), w["role"], "queued", now - w["created_at"], w.get("lane") or "") for w in db.queued_wakes()]
    lines = []
    for key, role, verb, secs, lane in items:
        if secs <= limit or (proj.id, *key) in flagged:
            continue
        flagged.add((proj.id, *key))
        who = role_name(role) + (f" · {lane}" if lane else "")  # F-41: a feature lane names its feature
        text = f"{who}'s turn has been {verb} for {int(secs // 60)}m; it was not stopped."
        if role != "manager":
            await lifecycle.system_wake(st, proj, "manager", "dm-manager", text)
        else:
            msg = db.add_message("dm-manager", "system", "System", text, [], None)
            await proj.hub.broadcast("message", db.message(msg["id"]))
        await proj.hub.broadcast("activity", db.add_activity("stuck_turn", text, role, conversation_id="dm-manager"))
        lines.append(text)
    if lines:
        await lifecycle.broadcast_team(st, proj)
    return lines
