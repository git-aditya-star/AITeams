"""Internal API for the MCP server (loopback only, must carry a running turn)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..models import InternalMessageBody, PermissionBody
from ..orchestrator import receipts
from ..orchestrator import router as routing
from .public import post_message

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
    return st, proj, turn


@internal.post("/messages")
async def post(request: Request, body: InternalMessageBody):
    st, proj, turn = _auth(request)
    role = turn["role"]
    mentions = routing.parse_mentions(body.text, body.mentions)
    type_ = body.type if body.type in ("Message", "Question", "Decision", "Concern", "Finding", "Handoff") else "Message"
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
    msgs = proj.db.messages(conv["id"], before, min(max(limit, 1), 100))
    return [{k: m[k] for k in ("id", "author", "type", "text", "mentions", "created_at")} for m in msgs]


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
