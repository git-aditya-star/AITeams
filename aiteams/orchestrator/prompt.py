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
    head = f"[{t}] #{m['id']} {m['author']} ({m['type']})"
    if mentions:
        head += " " + mentions
    line = f"{head}: {m['text']}"
    if m.get("reactions"):
        line += "  [" + " · ".join(f"{r['emoji']} {', '.join(r['roles'])}" for r in m["reactions"]) + "]"
    return line


def focus_conversation(messages: list[dict]) -> str | None:
    """Conversation of the newest Master message in the digest, else of the newest message."""
    if not messages:
        return None
    masters = [m for m in messages if m["author"] == "master"]
    return (masters or messages)[-1]["conversation_id"]


def live_message_ids(db: ProjectDB, message_ids: list[int]) -> list[int]:
    """Drop ids at or below their conversation's fresh-start marker."""
    return [m["id"] for m in db.messages_by_ids(message_ids) if m["id"] > db.reset_after(m["conversation_id"])]


def build_wake_prompt(db: ProjectDB, role: str, message_ids: list[int], handoff: dict | None = None) -> str:
    msgs = db.messages_by_ids(message_ids)
    by_conv: dict[str, list[dict]] = {}
    for m in msgs:
        by_conv.setdefault(m["conversation_id"], []).append(m)
    out = []
    if handoff:
        info = db.session_info(role)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(handoff["created_at"]))
        out += [f"# Fresh session #{info['seq']} — handoff from your previous session "
                f"(written {when}, reason: {handoff.get('reason') or 'rotation'})",
                _read(Path(handoff["path"])).strip() or "(handoff file missing)", ""]
    out.append(f"# New messages for {role.capitalize()}")
    for cid, ms in by_conv.items():
        out.append("\n## " + _conv_label(db, cid))
        out += [_fmt(m) for m in ms]
    focus = focus_conversation(msgs)
    if focus:
        feat = db.feature(focus) if (db.conversation(focus) or {}).get("kind") == "feature" else None
        if feat:
            from ..workflow import feature_header
            out.append("\n" + feature_header(db, db.project_path, feat))
        out.append(f"\n# Conversation: {_conv_label(db, focus)} — last {LAST_N} messages")
        out += [_fmt(m) for m in db.messages(focus, limit=LAST_N, after=db.reset_after(focus))]
    out += [
        "",
        "# Instructions",
        f"Act on the new messages. Reply with `post_message` (conversation id `{focus or 'general'}` unless"
        " another one fits). @mention only who must act. Your final text is not shown in chat."
        " Re-read a doc if it was edited since you last read it."
        " End your turn when nothing is left to do.",
    ]
    return "\n".join(out) + "\n"


def _conv_label(db: ProjectDB, cid: str) -> str:
    conv = db.conversation(cid) or {"title": cid, "kind": "?"}
    if conv["kind"] == "feature":
        feat = db.feature(cid)
        if feat:
            return f"{conv['title']} (feature, phase {feat['phase']}, id `{cid}`)"
    return f"{conv['title']} ({conv['kind']}, id `{cid}`)"


def build_handoff_prompt(db: ProjectDB, role: str, reason: str | None) -> str:
    info = db.session_info(role)
    return "\n".join([
        f"# Handoff turn for {role.capitalize()} (session #{info['seq']})",
        f"Reason: {reason or 'rotation'}. This session is being retired; your next wake starts a fresh session"
        " that only knows what you write now.",
        "",
        "Call `write_handoff(text)` ONCE with, in Markdown:",
        "- what you were doing (feature key, files, the goal)",
        "- the exact state: done / half-done / failing tests, with file paths",
        "- decisions and gotchas your future self must not re-learn",
        "- next steps, in order",
        "- open questions and who owes you an answer",
        "",
        "Do no other work: no edits, no messages, no commits. Then end your turn.",
    ]) + "\n"


def build_standup_prompt(db: ProjectDB, role: str, standup: dict) -> str:
    last = db.last_standup()
    since_ts = last["created_at"] if last else 0.0
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(since_ts)) if since_ts else "the start"
    turns = db.turns_since(role, since_ts)
    lines = [f"# Standup #{standup['id']} called by {standup['requested_by']}",
             f"Post exactly ONE message in conversation `standup` (type `Message`, mention nobody) with three bullets:",
             f"**Done** (since {since}), **Next**, **Blocked** (or 'nothing'). Be concrete: feature keys, files, commits.",
             "Then end your turn. Do no other work.", "", f"## Your turns since {since}"]
    if turns:
        for t in turns:
            when = time.strftime("%H:%M", time.localtime(t["created_at"]))
            lines.append(f"- [{when}] {_conv_label(db, t['conversation_id'])} — {t['state']}"
                         + (f", ${t['cost_usd']:.2f}" if t.get("cost_usd") else ""))
    else:
        lines.append("- none")
    open_commits = [c for c in db.commits("all", limit=50) if c["role"] == role and c["state"] in ("lead_review", "master_review")]
    if open_commits:
        lines.append("\n## Your open commit requests")
        lines += [f"- #{c['id']} {c['state']}: {c['message'].splitlines()[0]}" for c in open_commits]
    return "\n".join(lines) + "\n"


def build_compile_prompt(db: ProjectDB, project_path: str, standup: dict, team_rows: list[dict], feature_rows: list[dict]) -> str:
    from ..workflow import _ago
    last = db.last_standup()
    since_ts = last["created_at"] if last else 0.0
    lines = [f"# Standup #{standup['id']}: compile the report",
             "Replies from the team are below. Write ONE report in Markdown and call `save_standup(text)` once:",
             "- per active feature: status in one line, who is on it, blocked on what",
             "- team: who is idle, who is overloaded, sessions that were rotated",
             "- what the Master should decide or unblock (approvals, questions, stale items)",
             "Keep it under 40 lines. Then end your turn.", "", "## Replies"]
    replies = [m for m in db.messages("standup", limit=100) if m["id"] > (standup.get("start_message_id") or 0)
               and m["author"] != "system"]
    lines += [_fmt(m) for m in replies] or ["- none"]
    lines.append("\n## Team status")
    for t in team_rows:
        cur = (t.get("current") or {}).get("label") or "-"
        lines.append(f"- {t['role']}: {t['state']}, context {t['context_pct']:.0%}, session #{t['session_seq']}, "
                     f"on {cur}" + (f", rotation pending ({t['rotation']})" if t.get("rotation") else ""))
    lines.append("\n## Active features")
    for f in feature_rows:
        w = f.get("waits_on")
        lines.append(f"- {f['key']} {f['title']}: phase {f['phase']}, owner {f['owner'] or 'none'}, "
                     f"waits on {w['who'] + ' (' + w['kind'] + ')' if w else 'nobody'}, last activity {_ago(f['last_activity'])}"
                     + (", STALE" if f.get("stale") else ""))
    if not feature_rows:
        lines.append("- none")
    commits = [c for c in db.commits("all", limit=50) if c["updated_at"] > since_ts and c["state"] in ("committed", "rejected")]
    if commits:
        lines.append("\n## Commits since the last standup")
        lines += [f"- #{c['id']} {c['state']} ({c['role']}): {c['message'].splitlines()[0]}" for c in commits]
    apps = db.approvals("pending")
    if apps:
        lines.append("\n## Pending approvals")
        lines += [f"- #{a['id']} {a['title']} ({a['role']})" for a in apps]
    return "\n".join(lines) + "\n"
