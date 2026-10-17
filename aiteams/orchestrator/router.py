"""The one routing rule: @mention or DM = wake. Untagged Master message in general/thread = wake manager."""
from __future__ import annotations

import re

from ..config import ROLES
from ..db import ProjectDB

MENTION_RE = re.compile(r"(?<![\w@])@([a-zA-Z]+)\b")


def parse_mentions(text: str, extra: list[str] | None = None) -> list[str]:
    """Normalised, de-duplicated role list from @tokens in the text plus an explicit list."""
    found: list[str] = []
    for m in MENTION_RE.findall(text or ""):
        r = m.lower()
        if r in ROLES and r not in found:
            found.append(r)
    for r in extra or []:
        r = str(r).lower().lstrip("@")
        if r in ROLES and r not in found:
            found.append(r)
    return found


def roles_to_wake(message: dict, conversation: dict) -> list[str]:
    author = message["author"]
    if author == "system":
        return []
    roles = [r for r in message.get("mentions", []) if r != author]
    if conversation["kind"] == "dm" and conversation["role"] and conversation["role"] != author:
        if conversation["role"] not in roles:
            roles.append(conversation["role"])
    if author == "master" and conversation["kind"] in ("channel", "thread") and not message.get("mentions"):
        if "manager" not in roles:
            roles.append("manager")
    return roles


def route(db: ProjectDB, message: dict, conversation: dict) -> list[dict]:
    """Create or merge wakes for a new message. Returns the affected wake rows."""
    origin = "master" if message["author"] == "master" else "role"
    wakes = []
    for role in roles_to_wake(message, conversation):
        existing = db.queued_wake(role)
        if existing:
            wakes.append(db.merge_wake(existing["id"], message["conversation_id"], [message["id"]], origin))
        else:
            wakes.append(db.add_wake(role, message["conversation_id"], [message["id"]], origin))
    return wakes
