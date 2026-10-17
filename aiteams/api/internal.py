"""Internal API for the MCP server (loopback only, must carry a running turn and its turn token)."""
from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request

from .. import workflow
from ..config import ROLES
from ..db import EMOJI
from ..models import (CheckpointBody, HandoffBody, InternalApprovalBody, InternalCommitBody, InternalFeatureBody,
                      InternalFeaturePatchBody, InternalMessageBody, InternalReviewBody, InterruptBody, LockBody,
                      PermissionBody, ReactionBody, StandupSaveBody)
from ..orchestrator.lifecycle import request_rotation
from ..orchestrator import receipts
from ..orchestrator import router as routing
from .public import post_message

MESSAGE_TYPES = ("Message", "Question", "Decision", "Concern", "Finding", "Handoff", "Blocker", "Standup")

internal = APIRouter(prefix="/internal")

EXPIRED_MESSAGE = ("The Master did not answer within {m} minutes. Post the question to the conversation with "
                   "post_message(type='Question') and end your turn.")


def _auth(request: Request):
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "loopback only")
    st = request.app.state.app
    pid = request.headers.get("X-AITeams-Project", "")
    tid = request.headers.get("X-AITeams-Turn", "")
    proj = st.projects.get(pid)
    if proj is None or not tid.isdigit():
        raise HTTPException(403, "bad project/turn")
    turn = proj.db.turn(int(tid))
    if turn is None or turn["state"] != "running":
        raise HTTPException(403, "turn is not running")
    tok = turn.get("token") or ""
    hdr = request.headers.get("X-AITeams-Token") or ""
    if not (tok and hmac.compare_digest(tok.encode(), hdr.encode("utf-8", "replace"))):
        raise HTTPException(403, "bad turn token")
    return st, proj, turn


@internal.post("/messages")
async def post(request: Request, body: InternalMessageBody):
    st, proj, turn = _auth(request)
    role = turn["role"]
    mentions = routing.parse_mentions(body.text, body.mentions)
    type_ = body.type if body.type in MESSAGE_TYPES else "Message"
    msg = await post_message(st, proj, body.conversation, role, body.text, type_, mentions, turn["id"])
    wake = proj.db.wake(turn["wake_id"]) if turn.get("wake_id") else None
    if wake:
        digest = proj.db.messages_by_ids(wake["message_ids"])
        acked = [m["id"] for m in digest if m["conversation_id"] == msg["conversation_id"]]
        await receipts.mark(proj.db, proj.hub, acked, role, "acked")
    return {"id": msg["id"], "conversation_id": msg["conversation_id"]}


@internal.get("/messages")
def get(request: Request, conversation: str, before: int | None = None, limit: int = 20):
    _, proj, _ = _auth(request)
    conv = proj.db.conversation(conversation)
    if conv is None:
        raise HTTPException(404, f"unknown conversation: {conversation}")
    msgs = proj.db.messages(conv["id"], before, min(max(limit, 1), 100), after=int(conv.get("reset_after") or 0))
    return [{k: m[k] for k in ("id", "author", "type", "text", "mentions", "created_at", "reactions")} for m in msgs]


@internal.post("/reactions")
async def react(request: Request, body: ReactionBody):
    """Toggle the turn's role reaction. Broadcasts `reaction`; never wakes anyone, posts nothing, decides nothing."""
    _, proj, turn = _auth(request)
    if body.emoji not in EMOJI:
        raise HTTPException(400, "emoji must be one of " + " ".join(EMOJI))
    if proj.db.message(body.message_id) is None:
        raise HTTPException(404, f"unknown message #{body.message_id}")
    added = proj.db.toggle_reaction(body.message_id, turn["role"], body.emoji)
    reactions = proj.db.reactions_for([body.message_id]).get(body.message_id, [])
    await proj.hub.broadcast("reaction", {"message_id": body.message_id, "reactions": reactions})
    return {"message_id": body.message_id, "added": added, "reactions": reactions}


@internal.get("/conversations")
def convs(request: Request):
    _, proj, _ = _auth(request)
    return [{k: c[k] for k in ("id", "title", "kind")} for c in proj.db.conversations()]


@internal.post("/permission")
async def permission(request: Request, body: PermissionBody):
    st, proj, turn = _auth(request)
    if body.tool_name != "AskUserQuestion":
        return {"behavior": "allow", "updatedInput": body.input}
    q = proj.db.create_question(turn["id"], turn["role"], body.tool_use_id, body.input)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    proj.pending_questions[q["id"]] = fut
    await proj.hub.broadcast("question", q)
    timeout = float(st.settings.get("ask_timeout_s", 600))
    try:
        answers = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        proj.pending_questions.pop(q["id"], None)
        q = proj.db.answer_question(q["id"], None, "expired")
        await proj.hub.broadcast("question", q)
        return {"behavior": "deny", "message": EXPIRED_MESSAGE.format(m=int(timeout // 60) or 1)}
    return {"behavior": "allow", "updatedInput": {**body.input, "answers": answers}}


# ---- features / approvals / commits / locks (M1b) --------------------------------
def _feature_line(f: dict) -> str:
    return (f"{f['key']} · {f['phase']} · owner {f['owner'] or 'none'} · {f['title']} · conv {f['conversation_id']}"
            + (" · archived" if f["archived"] else ""))


@internal.get("/features")
def features(request: Request, history: int = 0):
    _, proj, _ = _auth(request)
    return {"features": [_feature_line(f) for f in proj.db.features(bool(history))]}


@internal.get("/features/{ident}")
def feature(request: Request, ident: str):
    _, proj, _ = _auth(request)
    feat = workflow.resolve_feature(proj.db, ident)
    if feat is None:
        raise HTTPException(404, "unknown feature")
    return {"header": workflow.feature_header(proj.db, proj.path, feat, with_sizes=True)}


@internal.post("/features")
async def create_feature(request: Request, body: InternalFeatureBody):
    st, proj, turn = _auth(request)
    feat = await workflow.new_feature(st, proj, body.title, body.phase, body.owner, by=turn["role"])
    return {"key": feat["key"], "conversation_id": feat["conversation_id"]}


@internal.patch("/features/{ident}")
async def set_feature(request: Request, ident: str, body: InternalFeaturePatchBody):
    st, proj, turn = _auth(request)
    feat = workflow.resolve_feature(proj.db, ident)
    if feat is None:
        raise HTTPException(404, "unknown feature")
    feat = await workflow.set_feature(st, proj, feat, body.model_dump(exclude_unset=True), turn["role"])
    return {"header": workflow.feature_header(proj.db, proj.path, feat)}


@internal.post("/approvals")
async def request_approval(request: Request, body: InternalApprovalBody):
    st, proj, turn = _auth(request)
    a = await workflow.request_approval(st, proj, turn["role"], turn["id"], body.feature, body.title, body.summary)
    return {"id": a["id"], "state": a["state"]}


@internal.post("/commits")
async def request_commit(request: Request, body: InternalCommitBody):
    st, proj, turn = _auth(request)
    c = await workflow.request_commit(st, proj, turn["role"], turn["id"], body.feature, body.files, body.message)
    return {"id": c["id"], "state": c["state"], "files": c["files"]}


@internal.post("/commits/{cid}/review")
async def review_commit(request: Request, cid: int, body: InternalReviewBody):
    st, proj, turn = _auth(request)
    c = await workflow.review_commit(st, proj, turn["role"], cid, body.decision, body.note)
    return {"id": c["id"], "state": c["state"]}


@internal.get("/locks")
def locks(request: Request):
    _, proj, _ = _auth(request)
    return proj.db.locks()


@internal.post("/locks/acquire")
async def acquire_lock(request: Request, body: LockBody):
    _, proj, turn = _auth(request)
    return await workflow.acquire_lock(proj, turn["role"], turn["id"], body.path)


@internal.post("/locks/release")
async def release_lock(request: Request, body: LockBody):
    st, proj, turn = _auth(request)
    return await workflow.release_lock(st, proj, turn["role"], body.path, body.force)


# ---- handoffs / standup / manager controls (M1c) -----------------------------------
@internal.post("/handoff")
async def write_handoff(request: Request, body: HandoffBody):
    st, proj, turn = _auth(request)
    return await workflow.write_handoff(st, proj, turn["role"], turn, body.text)


@internal.get("/team")
def team(request: Request):
    st, proj, _ = _auth(request)
    rows = workflow.team_status(st, proj)
    lines = []
    for t in rows:
        cur = (t.get("current") or {}).get("label") or "-"
        last = t["last_turn"]
        lines.append(f"{t['role']}: {t['state']} · context {t['context_pct']:.0%} · session #{t['session_seq']} · on {cur}"
                     + (f" · last turn {last['state']}" if last else "") + (f" · rotation pending ({t['rotation']})" if t["rotation"] else ""))
    return {"team": lines}


def _manager_only(turn: dict, body_role: str | None = None) -> None:
    if turn["role"] != "manager":
        raise HTTPException(403, "Manager only")
    if body_role is not None:
        if body_role not in ROLES:
            raise HTTPException(400, "unknown role")
        if body_role == "manager":
            raise HTTPException(400, "not on yourself")


@internal.post("/standup")
async def run_standup(request: Request):
    st, proj, turn = _auth(request)
    _manager_only(turn)
    return await workflow.start_standup(st, proj, "manager")


@internal.post("/standup/save")
async def save_standup(request: Request, body: StandupSaveBody):
    st, proj, turn = _auth(request)
    return await workflow.save_standup(st, proj, turn["role"], turn, body.text)


@internal.post("/interrupt")
async def interrupt(request: Request, body: InterruptBody):
    st, proj, turn = _auth(request)
    _manager_only(turn, body.role)
    ok = bool(st.scheduler and st.scheduler.stop(proj.id, body.role, "manager", body.reason or None))
    return {"ok": ok, "running": ok}


@internal.post("/checkpoint")
async def checkpoint(request: Request, body: CheckpointBody):
    st, proj, turn = _auth(request)
    _manager_only(turn, body.role)
    reason = "checkpoint" + (f": {body.reason}" if body.reason else "")
    if not await request_rotation(st, proj, body.role, reason, body.handoff):
        raise HTTPException(409, "a rotation is already pending for that role")
    msg = proj.db.add_message("dm-" + body.role, "system", "System",
                              f"The Manager requested a checkpoint ({body.reason or 'no reason given'}); "
                              + ("a handoff turn is queued." if body.handoff else "the next wake starts a fresh session."), [], turn["id"])
    await proj.hub.broadcast("message", msg)
    return proj.db.role_state(body.role)
