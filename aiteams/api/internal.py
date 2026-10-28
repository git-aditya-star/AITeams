"""Internal API for the MCP server (loopback only, must carry a running turn and its turn token)."""
from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request

from .. import workflow
from ..config import LEADS, ROLES, role_name
from ..db import EMOJI
from ..models import (AskMasterBody, CheckpointBody, HandoffBody, InternalApprovalBody, InternalBranchBody,
                      InternalBrainstormBody, InternalCommitBody, InternalFeatureBody, InternalFeaturePatchBody, InternalMessageBody,
                      InternalReviewBody, InternalWithdrawBody, InterruptBody, LockBody, PermissionBody, ReactionBody,
                      StandupSaveBody)
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
    pfeat = workflow.feature_of_conversation(proj.db, body.conversation)
    await workflow.guard_lead(st, proj, role, pfeat, claim=False)  # F-22: not the other Lead's feature
    if type_ == "Decision":  # F-16: refuse once if the conversation moved on since the role last looked
        conv = proj.db.conversation(body.conversation)
        await workflow.guard(proj, turn, {conv["id"] if conv else body.conversation}, set())
    await workflow.guard_lead(st, proj, role, pfeat)  # F-22: a feature with no Lead is claimed by the acting Lead
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


@internal.get("/new_messages")
async def new_messages(request: Request, shown: str | None = None):
    """F-16: messages posted since this turn last looked, as a text block ("" if none); the MCP server appends it to
    every tool result. `shown`: a conversation the tool just printed (get_messages), delivered without listing."""
    _, proj, turn = _auth(request)
    return {"text": await workflow.new_messages(proj, turn, shown)}


@internal.get("/conversations")
def convs(request: Request):
    _, proj, _ = _auth(request)
    return [{k: c[k] for k in ("id", "title", "kind")} for c in proj.db.conversations()]


@internal.post("/permission")
async def permission(request: Request, body: PermissionBody):
    st, proj, turn = _auth(request)
    if body.tool_name == "Artifact" and isinstance(body.input, dict):  # F-29 B: defence in depth, the hook decides first
        why = workflow.artifact_static_denial(body.input)
        if why:
            return {"behavior": "deny", "message": why}
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
    return (f"{f['key']} · {f['phase']} · owner {f['owner'] or 'none'} · lead "
            f"{role_name(f['lead']) if f.get('lead') else 'not assigned'} · {f['title']} · conv {f['conversation_id']}"
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
    fields = body.model_dump(exclude_unset=True)
    if "lead" not in fields:  # F-22: a Lead acts only in its own features (a `lead` field is the Manager's: 403 below)
        await workflow.guard_lead(st, proj, turn["role"], feat)
    feat = await workflow.set_feature(st, proj, feat, fields, turn["role"])
    return {"header": workflow.feature_header(proj.db, proj.path, feat)}


@internal.post("/questions")
async def ask_master(request: Request, body: AskMasterBody):
    """F-10: a non-blocking question card to the Master (the role ends its turn; the answer wakes it)."""
    st, proj, turn = _auth(request)
    feat = workflow.resolve_feature(proj.db, body.feature)
    await workflow.guard_lead(st, proj, turn["role"], feat, claim=False)  # F-22: not the other Lead's feature
    if body.purpose == "a_vs_b":  # F-16 guard: the A vs B framing must reflect the latest exchange
        await workflow.guard(proj, turn, {feat["conversation_id"]} if feat else set(), set())
    await workflow.guard_lead(st, proj, turn["role"], feat)  # F-22: a feature with no Lead is claimed
    q = await workflow.ask_master(st, proj, turn["role"], turn["id"], body.feature, body.questions, body.intro,
                                  body.purpose)
    return {"id": q["id"], "conversation_id": q["conversation_id"], "message_id": q["message_id"]}


@internal.post("/withdraw")
async def withdraw_request(request: Request, body: InternalWithdrawBody):
    """F-34: pull back an approval, a commit request or a question card (the sender, or the feature's Lead)."""
    st, proj, turn = _auth(request)
    item = await workflow.withdraw_request(st, proj, turn["role"], body.kind, body.id, body.reason)
    return {"kind": body.kind, "id": item["id"], "state": item["state"], "note": item["note"]}


@internal.post("/brainstorm")
async def start_brainstorm(request: Request, body: InternalBrainstormBody):
    """F-10: Product or the Manager starts a brainstorm round on a feature."""
    st, proj, turn = _auth(request)
    if turn["role"] not in workflow.BRAINSTORM_STARTERS:
        raise HTTPException(403, "only Product or the Manager start a brainstorm")
    feat = workflow.resolve_feature(proj.db, body.feature)
    if feat is None:
        raise HTTPException(400, "feature required")
    bs = await workflow.start_brainstorm(st, proj, feat, body.topic, turn["role"])
    return {"id": bs["id"], "feature": feat["key"], "roles": bs["roles"], "state": bs["state"]}


@internal.post("/approvals")
async def request_approval(request: Request, body: InternalApprovalBody):
    st, proj, turn = _auth(request)
    if workflow.is_design_title(body.title):  # F-22: only the feature's Lead requests its design approval
        await workflow.guard_lead(st, proj, turn["role"], workflow.resolve_feature(proj.db, body.feature))
    a = await workflow.request_approval(st, proj, turn["role"], turn["id"], body.feature, body.title, body.summary)
    return {"id": a["id"], "state": a["state"]}


@internal.post("/branches")
async def propose_branch(request: Request, body: InternalBranchBody):
    """F-39: the feature's Lead proposes its branch/worktree; the Master confirms it on a Branch: card."""
    st, proj, turn = _auth(request)
    if turn["role"] not in LEADS:
        raise HTTPException(403, "only the feature's Lead proposes its branch")
    feat = workflow.resolve_feature(proj.db, body.feature)
    if feat is None:
        raise HTTPException(404, "unknown feature")
    await workflow.guard_lead(st, proj, turn["role"], feat)  # F-22: not the other Lead's feature; an unled one is claimed
    a = await workflow.propose_branch(st, proj, turn["role"], turn["id"], body.feature, body.mode, body.branch, body.base,
                                      body.shared_from, body.why)
    return {"id": a["id"], "state": a["state"], "payload": a["payload"]}


@internal.post("/commits")
async def request_commit(request: Request, body: InternalCommitBody):
    st, proj, turn = _auth(request)
    feat = workflow.resolve_feature(proj.db, body.feature)  # F-16 guard: the feature and the requests it requires
    await workflow.guard(proj, turn, {feat["conversation_id"] if feat else f"dm-{turn['role']}"}, set(body.requires))
    c = await workflow.request_commit(st, proj, turn["role"], turn["id"], body.feature, body.files, body.message,
                                      body.requires)
    return {"id": c["id"], "state": c["state"], "files": c["files"], "requires": c["requires"]}


@internal.get("/commits/{cid}/files")
def commit_files(request: Request, cid: int):
    """F-11 R2: {ids, files} of the request's open commit group (or the request alone), for scripts/review_tree.py."""
    _, proj, _ = _auth(request)
    return workflow.group_files(proj.db, cid)


@internal.post("/commits/{cid}/review")
async def review_commit(request: Request, cid: int, body: InternalReviewBody):
    st, proj, turn = _auth(request)
    if turn["role"] in LEADS:
        c0 = proj.db.commit(cid)  # F-22: review the requests of your own features only
        cfeat = proj.db.feature(c0["feature_id"]) if c0 and c0["feature_id"] else None
        await workflow.guard_lead(st, proj, turn["role"], cfeat, claim=False)
        # F-16 guard: N's conversation and N's own commit events (e.g. "superseded")
        await workflow.guard(proj, turn, *workflow.commit_targets(proj.db, cid))
        await workflow.guard_lead(st, proj, turn["role"], cfeat)
    c = await workflow.review_commit(st, proj, turn["role"], cid, body.decision, body.note, turn["id"])
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
