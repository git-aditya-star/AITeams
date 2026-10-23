"""Internal: the "Ask me first" skill approval gate (F-5, generalised by F-23) and the Artifact gate (F-29 Part B). Its own
router so it stays apart from the rest of /internal."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import workflow
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


@skills.get("/artifact-check")
def artifact_check(request: Request, action: str = "publish", path: str = "", url: str = ""):
    """F-29 B: may this turn's role make this Artifact call? `{ok, reason}`; the hook denies with `reason`. The turn's own
    token says who is asking; see workflow.artifact_gate for the rules (it may write a pending row when it allows a publish)."""
    _, proj, turn = _auth(request)
    return workflow.artifact_gate(proj, turn, {"action": action, "file_path": path or None, "url": url or None})
