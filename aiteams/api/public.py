"""Public HTTP API under /api and the per-project websocket."""
from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from .. import config
from ..config import ROLES, model_for
from ..models import AnswerBody, ConversationBody, MessageBody, PauseBody, RegisterBody
from ..orchestrator import router as routing
from ..orchestrator import receipts
from ..orchestrator.turn import context_window_from
from ..runtime import Project

api = APIRouter(prefix="/api")
wsr = APIRouter()


def _state(request):
    return request.app.state.app


def _project(request, pid: str) -> Project:
    proj = _state(request).projects.get(pid)
    if proj is None:
        raise HTTPException(404, "unknown project")
    return proj


# ---- daemon-level -------------------------------------------------------
@api.get("/health")
def health(request: Request):
    st = _state(request)
    return {"ok": True, "pid": os.getpid(), "port": st.port, "started_at": st.started_at,
            "running_turns": len(st.scheduler.running) if st.scheduler else 0}


@api.get("/projects")
def projects(request: Request):
    return [p.to_dict() for p in _state(request).projects.values()]


@api.post("/projects/register")
def register(request: Request, body: RegisterBody):
    path = os.path.realpath(os.path.expanduser(body.path))
    if not os.path.isdir(path):
        raise HTTPException(400, f"not a directory: {path}")
    proj, created = _state(request).register(path)
    return {"id": proj.id, "name": proj.name, "created": created, "is_git": proj.is_git}


@api.get("/settings")
def get_settings(request: Request):
    return _state(request).settings


@api.put("/settings")
def put_settings(request: Request, body: dict):
    st = _state(request)
    st.settings = config.save_settings(body)
    if st.scheduler:
        st.scheduler.notify()
    return st.settings


@api.post("/shutdown")
async def shutdown(request: Request):
    st = _state(request)
    if st.request_shutdown:
        asyncio.get_running_loop().call_later(0.2, st.request_shutdown)
    return {"ok": True}


# ---- per project ---------------------------------------------------------
@api.get("/p/{pid}")
def project(request: Request, pid: str):
    return _project(request, pid).to_dict(_state(request))


@api.get("/p/{pid}/conversations")
def conversations(request: Request, pid: str):
    return _project(request, pid).db.conversations()


@api.post("/p/{pid}/conversations")
async def create_conversation(request: Request, pid: str, body: ConversationBody):
    proj = _project(request, pid)
    conv = proj.db.create_thread(body.title.strip())
    await proj.hub.broadcast("conversation", conv)
    return conv


@api.get("/p/{pid}/messages")
def messages(request: Request, pid: str, conversation: str, before: int | None = None, limit: int = 50):
    proj = _project(request, pid)
    conv = proj.db.conversation(conversation)
    if conv is None:
        raise HTTPException(404, "unknown conversation")
    return proj.db.messages(conv["id"], before, min(max(limit, 1), 200))


async def post_message(st, proj: Project, conversation: str, author: str, text: str, type_: str,
                       mentions: list[str], turn_id: int | None) -> dict:
    conv = proj.db.conversation(conversation)
    if conv is None:
        raise HTTPException(404, f"unknown conversation: {conversation}")
    msg = proj.db.add_message(conv["id"], author, type_, text, mentions, turn_id)
    wakes = routing.route(proj.db, msg, conv)
    for w in wakes:
        await receipts.mark(proj.db, proj.hub, [msg["id"]], w["role"], "queued")
    msg = proj.db.message(msg["id"])
    await proj.hub.broadcast("message", msg)
    if wakes and st.scheduler:
        st.scheduler.notify()
    return msg


@api.post("/p/{pid}/messages")
async def create_message(request: Request, pid: str, body: MessageBody):
    proj = _project(request, pid)
    mentions = routing.parse_mentions(body.text)
    return await post_message(_state(request), proj, body.conversation, "master", body.text, "Message", mentions, None)


@api.get("/p/{pid}/team")
def team(request: Request, pid: str):
    st = _state(request)
    proj = _project(request, pid)
    db = proj.db
    out = []
    for role in ROLES:
        turns = db.turns(role, limit=20)
        last = turns[0] if turns else None
        running = (proj.id, role) in (st.scheduler.running if st.scheduler else {})
        state = "running" if running else ("queued" if db.queued_wake(role) else "idle")
        ctx = next((t["context_tokens"] for t in turns if t.get("context_tokens")), 0) or 0
        model = model_for(st.settings, role)
        window = next((context_window_from(json.loads(t["result_json"]), None)
                       for t in turns if t.get("result_json")), context_window_from(None))
        out.append({"role": role, "state": state, "session_id": db.active_session(role), "last_turn": last,
                    "context_tokens": ctx, "context_window": window,
                    "context_pct": (ctx / window) if window else 0.0,
                    "cost_today": db.cost_today(role), "model": model})
    return {"roles": out}


@api.get("/p/{pid}/turns")
def turns(request: Request, pid: str, role: str | None = None, limit: int = 50):
    return _project(request, pid).db.turns(role, min(max(limit, 1), 500))


@api.get("/p/{pid}/turns/{tid}/events")
def turn_events(request: Request, pid: str, tid: int, after_seq: int = 0):
    return _project(request, pid).db.turn_events(tid, after_seq)


@api.get("/p/{pid}/questions")
def questions(request: Request, pid: str, state: str = "pending"):
    if state not in ("pending", "answered", "expired"):
        raise HTTPException(400, "bad state")
    return _project(request, pid).db.questions(state)


@api.post("/p/{pid}/questions/{qid}/answer")
async def answer(request: Request, pid: str, qid: int, body: AnswerBody):
    proj = _project(request, pid)
    q = proj.db.question(qid)
    if q is None:
        raise HTTPException(404, "unknown question")
    if q["state"] != "pending":
        raise HTTPException(409, f"question is {q['state']}")
    q = proj.db.answer_question(qid, body.answers, "answered")
    fut = proj.pending_questions.pop(qid, None)
    if fut and not fut.done():
        fut.set_result(body.answers)
    await proj.hub.broadcast("question", q)
    return q


@api.post("/p/{pid}/pause")
async def pause(request: Request, pid: str, body: PauseBody):
    st = _state(request)
    proj = _project(request, pid)
    proj.db.set_kv("paused", body.paused)
    await proj.hub.broadcast("paused", {"paused": body.paused})
    if st.scheduler:
        st.scheduler.notify()
    return {"paused": body.paused}


# ---- websocket -------------------------------------------------------------
@wsr.websocket("/ws/p/{pid}")
async def ws_project(ws: WebSocket, pid: str):
    st = ws.app.state.app
    proj = st.projects.get(pid)
    if proj is None:
        await ws.close(code=4404)
        return
    await proj.hub.connect(ws)
    try:
        await ws.send_text(json.dumps({"event": "hello", "data": {"paused": proj.paused, "rate_limited": st.rate_limited,
                                                                   "ts": time.time()}}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        proj.hub.disconnect(ws)
