"""Public HTTP API under /api (Master token), the open routes (/api/health, /auth) and the per-project websocket."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config
from ..config import ROLES
from .. import workflow
from ..models import AnswerBody, ConversationBody, FreshStartBody, MessageBody, PauseBody, RegisterBody
from ..orchestrator import router as routing
from ..orchestrator import receipts
from ..runtime import Project

api = APIRouter(prefix="/api")  # wired with the master_auth dependency in daemon.create_app
open_api = APIRouter()  # no auth: health, sign-in link landing
wsr = APIRouter()

COOKIE = "aiteams_token"
AUTH_CODE_TTL = 60.0

SIGNIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>AITeams</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1f2937;background:#fff}
main{max-width:520px;margin:18vh auto 0;padding:0 24px}h1{font-size:18px;font-weight:600;margin:0 0 12px}
p{margin:0 0 8px;color:#4b5563}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f3f4f6;border-radius:4px;padding:1px 5px}
.note{color:#b45309}</style></head><body><main><h1>AITeams</h1>%s
<p>Run <code>aiteams</code> in your project folder to open a signed-in tab, or <code>aiteams token --link</code> for a sign-in link.</p>
</main></body></html>"""


def token_ok(st, tok: str | None) -> bool:
    return bool(tok) and hmac.compare_digest(str(tok).encode(), st.token.encode())


def master_auth(request: Request) -> None:
    """Bearer header or cookie must match the Master token (~/.aiteams/token)."""
    auth = request.headers.get("Authorization", "")
    tok = auth[7:].strip() if auth.startswith("Bearer ") else request.cookies.get(COOKIE)
    if not token_ok(_state(request), tok):
        raise HTTPException(401, "master token required")


def signin_page(note: str = "") -> HTMLResponse:
    body = f'<p class="note">{note}</p>' if note else ""
    return HTMLResponse(SIGNIN_HTML % body, status_code=200)


def _state(request):
    return request.app.state.app


def _project(request, pid: str) -> Project:
    proj = _state(request).projects.get(pid)
    if proj is None:
        raise HTTPException(404, "unknown project")
    return proj


# ---- open routes (no token) ------------------------------------------------
@open_api.get("/api/health")
def health(request: Request):
    st = _state(request)
    return {"ok": True, "pid": os.getpid(), "port": st.port, "started_at": st.started_at,
            "running_turns": len(st.scheduler.running) if st.scheduler else 0, **st.runtime()}


@open_api.get("/auth")
def auth_landing(request: Request, t: str = "", next: str = "/"):
    """Single-use sign-in link: sets the token cookie and redirects into the app."""
    st = _state(request)
    now = time.time()
    for code, exp in list(st.auth_codes.items()):
        if exp < now:
            st.auth_codes.pop(code, None)
    if not t or st.auth_codes.pop(t, None) is None:
        return signin_page("link expired, run `aiteams` again")
    target = next if next == "/" or next.startswith("/p/") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(COOKIE, st.token, httponly=True, samesite="strict", path="/")
    return resp


# ---- daemon-level -------------------------------------------------------
@api.post("/auth/link")
def auth_link(request: Request):
    st = _state(request)
    code = secrets.token_urlsafe(16)
    st.auth_codes[code] = time.time() + AUTH_CODE_TTL
    return {"code": code, "url": f"{st.daemon_url}/auth?t={code}"}


@api.post("/token/reload")
async def token_reload(request: Request):
    """Re-read ~/.aiteams/token (after `aiteams token --rotate`); every open tab is closed with 4401."""
    st = _state(request)
    st.token = config.master_token()
    st.auth_codes.clear()
    for p in st.projects.values():
        for ws in list(p.hub.clients):
            try:
                await ws.close(code=4401)
            except Exception:
                pass
            p.hub.disconnect(ws)
    return {"ok": True}


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


@api.get("/settings/options")
def settings_options():
    """F-9: the lists the role settings dialog offers (config is the single source of truth)."""
    return {"roles": config.ROLES, "models": [{"id": i, "label": lbl} for i, lbl in config.MODELS],
            "efforts": list(config.EFFORTS),
            "effort_support": {m: list(v) for m, v in config.EFFORT_SUPPORT.items()}}


@api.put("/settings")
def put_settings(request: Request, body: dict):
    st = _state(request)
    try:
        body = config.validate_role_settings(body, config.load_settings())
    except ValueError as e:
        raise HTTPException(400, str(e))
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
    feat = proj.db.create_feature(body.title.strip())
    conv = proj.db.conversation(feat["conversation_id"])
    await proj.hub.broadcast("feature", feat)
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
                       mentions: list[str], turn_id: int | None, ref: str | None = None) -> dict:
    conv = proj.db.conversation(conversation)
    if conv is None:
        raise HTTPException(404, f"unknown conversation: {conversation}")
    pick_line = None
    if conv["kind"] == "feature":  # F-22: a Lead mention in a feature means that feature's Lead
        mentions, pick_line = await workflow.resolve_lead_mentions(st, proj, proj.db.feature(conv["id"]), mentions)
    msg = proj.db.add_message(conv["id"], author, type_, text, mentions, turn_id, ref)
    wakes = routing.route(proj.db, msg, conv)
    for w in wakes:
        await receipts.mark(proj.db, proj.hub, [msg["id"]], w["role"], "queued")
    msg = proj.db.message(msg["id"])
    await proj.hub.broadcast("message", msg)
    if wakes and st.scheduler:
        st.scheduler.notify()
    if pick_line:  # after the message itself
        await workflow._post(st, proj, conv["id"], "system", pick_line, "System", [])
    return msg


@api.post("/p/{pid}/messages")
async def create_message(request: Request, pid: str, body: MessageBody):
    proj = _project(request, pid)
    mentions = routing.parse_mentions(body.text)
    return await post_message(_state(request), proj, body.conversation, "master", body.text, "Message", mentions, None)


@api.get("/p/{pid}/team")
def team(request: Request, pid: str):
    return {"roles": workflow.team_status(_state(request), _project(request, pid))}


@api.get("/p/{pid}/dashboard")
def dashboard(request: Request, pid: str):
    return workflow.dashboard(_state(request), _project(request, pid))


@api.post("/p/{pid}/team/{role}/stop")
async def stop_role(request: Request, pid: str, role: str):
    st = _state(request)
    proj = _project(request, pid)
    if role not in ROLES:
        raise HTTPException(404, "unknown role")
    if not (st.scheduler and st.scheduler.stop(proj.id, role, "master")):
        raise HTTPException(409, "not running")
    return {"ok": True}


@api.post("/p/{pid}/team/{role}/fresh-start")
async def fresh_start_role(request: Request, pid: str, role: str, body: FreshStartBody):
    from ..orchestrator.lifecycle import request_rotation
    st = _state(request)
    proj = _project(request, pid)
    if role not in ROLES:
        raise HTTPException(404, "unknown role")
    if not await request_rotation(st, proj, role, "fresh start", body.handoff):
        raise HTTPException(409, "a rotation is already pending")
    return proj.db.role_state(role)


@api.post("/p/{pid}/standup")
async def standup(request: Request, pid: str):
    return await workflow.start_standup(_state(request), _project(request, pid), "master")


@api.get("/p/{pid}/standups")
def standups(request: Request, pid: str, limit: int = 20):
    return _project(request, pid).db.standups(min(max(limit, 1), 100))


@api.get("/p/{pid}/standups/{sid}/report")
def standup_report(request: Request, pid: str, sid: int):
    return workflow.standup_report(_project(request, pid), sid)


@api.get("/p/{pid}/handoffs")
def handoffs(request: Request, pid: str, role: str | None = None, limit: int = 20):
    proj = _project(request, pid)
    out = []
    for h in proj.db.handoffs(role, min(max(limit, 1), 100)):
        try:
            with open(h["path"], encoding="utf-8", errors="replace") as f:
                h["text"] = f.read()
        except OSError:
            h["text"] = ""
        out.append(h)
    return out


@api.get("/p/{pid}/activity")
def activity(request: Request, pid: str, limit: int = 30):
    return _project(request, pid).db.activity(min(max(limit, 1), 200))


@api.get("/p/{pid}/turns")
def turns(request: Request, pid: str, role: str | None = None, limit: int = 50):
    return _project(request, pid).db.turns(role, min(max(limit, 1), 500))


@api.get("/p/{pid}/turns/{tid}/events")
def turn_events(request: Request, pid: str, tid: int, after_seq: int = 0):
    return _project(request, pid).db.turn_events(tid, after_seq)


@api.get("/p/{pid}/questions")
def questions(request: Request, pid: str, state: str = "pending"):
    if state not in ("pending", "answered", "expired", "withdrawn"):
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
    if q.get("kind") == "card":  # F-10: no held tool call; the answer is posted as a Decision that wakes the role
        return await workflow.answer_card(_state(request), proj, q, body.answers)
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
    # Accept before closing: a close before accept is a 403 handshake, which the browser sees as 1006 ("daemon down").
    if not token_ok(st, ws.cookies.get(COOKIE) or ws.query_params.get("token")):
        await ws.accept()
        await ws.close(code=4401)
        return
    proj = st.projects.get(pid)
    if proj is None:
        await ws.accept()
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
