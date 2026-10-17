"""Public routes for features, docs, approvals, commits, locks and the Inbox (prefix /api)."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from .. import workflow
from ..models import (ApprovalDecideBody, CommitDecideBody, FeatureFreshStartBody, FeaturePatchBody, NewTaskBody,
                      ResolveBody)
from .public import _project, _state

feat_api = APIRouter(prefix="/api")


def _feature(proj, fid: int) -> dict:
    feat = proj.db.feature(fid)
    if feat is None:
        raise HTTPException(404, "unknown feature")
    return feat


@feat_api.get("/p/{pid}/features")
def features(request: Request, pid: str, history: int = 0):
    return _project(request, pid).db.features(bool(history))


@feat_api.post("/p/{pid}/features")
async def new_task(request: Request, pid: str, body: NewTaskBody):
    proj = _project(request, pid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text required")
    title = (body.title or "").strip() or text.splitlines()[0][:60]
    return await workflow.new_feature(_state(request), proj, title, by="master", text=text, starter=body.starter)


@feat_api.get("/p/{pid}/features/history")
def features_history(request: Request, pid: str):
    return _project(request, pid).db.features_history()


@feat_api.get("/p/{pid}/features/{fid}")
def feature(request: Request, pid: str, fid: int):
    return _feature(_project(request, pid), fid)


@feat_api.post("/p/{pid}/features/{fid}/fresh-start")
async def fresh_start_feature(request: Request, pid: str, fid: int, body: FeatureFreshStartBody):
    proj = _project(request, pid)
    return await workflow.fresh_start_feature(_state(request), proj, _feature(proj, fid), body.mode)


@feat_api.patch("/p/{pid}/features/{fid}")
async def patch_feature(request: Request, pid: str, fid: int, body: FeaturePatchBody):
    proj = _project(request, pid)
    return await workflow.set_feature(_state(request), proj, _feature(proj, fid),
                                      body.model_dump(exclude_unset=True), "master")


@feat_api.get("/p/{pid}/features/{fid}/docs")
def docs(request: Request, pid: str, fid: int):
    proj = _project(request, pid)
    return workflow.docs_list(proj.db, proj.path, _feature(proj, fid))


@feat_api.get("/p/{pid}/features/{fid}/docs/{name}")
def doc(request: Request, pid: str, fid: int, name: str):
    proj = _project(request, pid)
    feat = _feature(proj, fid)
    if "/" in name or not name.endswith(".md"):
        raise HTTPException(400, "bad doc name")
    p = os.path.join(workflow.docs_dir(proj.path, feat), name)
    if not os.path.isfile(p):
        raise HTTPException(404, "no such doc")
    with open(p, encoding="utf-8", errors="replace") as f:
        return {"name": name, "text": f.read()}


@feat_api.get("/p/{pid}/approvals")
def approvals(request: Request, pid: str, state: str = "pending", feature: int | None = None):
    if state not in ("pending", "approved", "changes_requested", "all"):
        raise HTTPException(400, "bad state")
    return _project(request, pid).db.approvals(state, feature)


@feat_api.post("/p/{pid}/approvals/{aid}/decide")
async def decide_approval(request: Request, pid: str, aid: int, body: ApprovalDecideBody):
    proj = _project(request, pid)
    return await workflow.decide_approval(_state(request), proj, aid, body.decision, body.note, body.set_phase)


@feat_api.get("/p/{pid}/commits")
def commits(request: Request, pid: str, state: str = "all", feature: int | None = None):
    if state != "all" and state not in workflow.COMMIT_STATES:
        raise HTTPException(400, "bad state")
    return _project(request, pid).db.commits(state, feature)


@feat_api.get("/p/{pid}/commits/{cid}/diff")
async def commit_diff(request: Request, pid: str, cid: int):
    return await workflow.commit_diff(_project(request, pid), cid)


@feat_api.post("/p/{pid}/commits/{cid}/decide")
async def decide_commit(request: Request, pid: str, cid: int, body: CommitDecideBody):
    proj = _project(request, pid)
    return await workflow.decide_commit(_state(request), proj, cid, body.decision, body.message, body.note, body.force)


@feat_api.get("/p/{pid}/locks")
def locks(request: Request, pid: str):
    return _project(request, pid).db.locks()


@feat_api.delete("/p/{pid}/locks")
async def release_lock(request: Request, pid: str, path: str):
    return await workflow.master_release_lock(_state(request), _project(request, pid), path)


@feat_api.get("/p/{pid}/inbox")
def inbox(request: Request, pid: str):
    return workflow.inbox(_project(request, pid))


@feat_api.post("/p/{pid}/inbox/resolve")
async def resolve(request: Request, pid: str, body: ResolveBody):
    proj = _project(request, pid)
    if proj.db.message(body.message_id) is None:
        raise HTTPException(404, "unknown message")
    proj.db.resolve_message(body.message_id)
    await workflow.broadcast_inbox(proj)
    return {"ok": True}
