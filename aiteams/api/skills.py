"""Internal: the "Ask me first" skill approval gate (F-5, generalised by F-23). Its own router so it stays apart from the
rest of /internal."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .internal import _auth

skills = APIRouter(prefix="/internal")


@skills.get("/skill-approval")
def skill_approval(request: Request, skill: str):
    """ok only if the calling role has an approved approval titled `Use skill: <plugin>:<skill>` (no expiry; trimmed,
    case-insensitive). For team-skills the F-5 title `Use team-skill: <skill>` still counts."""
    _, proj, turn = _auth(request)
    skill = skill.strip().lower()
    titles = {"use skill: " + skill}
    plugin, _, name = skill.partition(":")
    if plugin.strip() == "team-skills":
        titles.add("use team-skill: " + name.strip())
    ok = any(a["role"] == turn["role"] and (a["title"] or "").strip().lower() in titles for a in proj.db.approvals("approved"))
    return {"ok": ok}
