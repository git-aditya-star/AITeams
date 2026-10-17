"""System prompt file (role + team-rules + project header + repo CLAUDE.md) and the wake prompt (digest)."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..config import ROLES
from ..db import ProjectDB

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"
LAST_N = 20


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def role_prompt_text(project_path: str, role: str) -> str:
    override = Path(project_path) / ".aiteams" / "roles" / f"{role}.md"
    return _read(override) or _read(ROLES_DIR / f"{role}.md")


def team_rules_text(project_path: str) -> str:
    override = Path(project_path) / ".aiteams" / "roles" / "team-rules.md"
    return _read(override) or _read(ROLES_DIR / "team-rules.md")


def git_branch(project_path: str) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else "(not a git repo)"
    except (OSError, subprocess.SubprocessError):
        return "(unknown)"


def build_system_prompt(project_path: str, project_name: str, role: str) -> str:
    parts = [
        f"# You are the {role.capitalize()} of the AITeams team on project \"{project_name}\"",
        f"- Project path: {project_path}",
        f"- Git branch: {git_branch(project_path)}",
        f"- Roles on the team: {', '.join(ROLES)} (you are `{role}`; the human is the Master)",
        "",
        role_prompt_text(project_path, role).strip(),
        "",
        "# Team rules",
        team_rules_text(project_path).strip(),
    ]
    claude_md = _read(Path(project_path) / "CLAUDE.md").strip()
    if claude_md:
        parts += ["", "# Project CLAUDE.md", claude_md]
    return "\n".join(parts) + "\n"


def write_system_prompt(project_path: str, project_name: str, role: str) -> str:
    d = Path(project_path) / ".aiteams" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{role}.system.md"
    p.write_text(build_system_prompt(project_path, project_name, role))
    return str(p)


def _fmt(m: dict) -> str:
    t = time.strftime("%H:%M", time.localtime(m["created_at"]))
    mentions = " ".join("@" + r for r in m.get("mentions", []))
    head = f"[{t}] {m['author']} ({m['type']})"
    if mentions:
        head += " " + mentions
    return f"{head}: {m['text']}"


def focus_conversation(messages: list[dict]) -> str | None:
    """Conversation of the newest Master message in the digest, else of the newest message."""
    if not messages:
        return None
    masters = [m for m in messages if m["author"] == "master"]
    return (masters or messages)[-1]["conversation_id"]


def build_wake_prompt(db: ProjectDB, role: str, message_ids: list[int]) -> str:
    msgs = db.messages_by_ids(message_ids)
    by_conv: dict[str, list[dict]] = {}
    for m in msgs:
        by_conv.setdefault(m["conversation_id"], []).append(m)
    out = [f"# New messages for {role.capitalize()}"]
    for cid, ms in by_conv.items():
        conv = db.conversation(cid) or {"title": cid, "kind": "?"}
        out.append(f"\n## {conv['title']} ({conv['kind']}, id `{cid}`)")
        out += [_fmt(m) for m in ms]
    focus = focus_conversation(msgs)
    if focus:
        conv = db.conversation(focus) or {"title": focus, "kind": "?"}
        out.append(f"\n# Conversation: {conv['title']} ({conv['kind']}, id `{focus}`) — last {LAST_N} messages")
        out += [_fmt(m) for m in db.messages(focus, limit=LAST_N)]
    out += [
        "",
        "# Instructions",
        f"Act on the new messages. Reply with `post_message` (conversation id `{focus or 'general'}` unless"
        " another one fits). @mention only who must act. Your final text is not shown in chat."
        " End your turn when nothing is left to do.",
    ]
    return "\n".join(out) + "\n"
