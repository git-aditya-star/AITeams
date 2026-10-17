"""Internal: the team-skills approval gate (F-5). Its own router so it stays apart from the rest of /internal."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .internal import _auth

skills = APIRouter(prefix="/internal")


@skills.get("/skill-approval")
def skill_approval(request: Request, skill: str):
    """ok only if the calling role has an approved approval titled exactly `Use team-skill: <skill>` (no expiry)."""
    _, proj, turn = _auth(request)
    title = "Use team-skill: " + skill
    ok = any(a["role"] == turn["role"] and a["title"] == title for a in proj.db.approvals("approved"))
    return {"ok": ok}
