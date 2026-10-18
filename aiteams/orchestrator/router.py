"""The one routing rule: @mention or DM = wake. Untagged Master message in general/feature = wake manager."""
from __future__ import annotations

import re

from ..config import ROLES, canonical_role
from ..db import ProjectDB

# F-22: `@name` or `@name-<digits>` (@lead-2); @lead-1 is an alias for @lead. A hyphen followed by anything else
# still ends the name, as before (@lead-review mentions @lead).
MENTION_RE = re.compile(r"(?<![\w@])@([a-zA-Z]+(?:-\d+)?)\b")


def parse_mentions(text: str, extra: list[str] | None = None) -> list[str]:
    """Normalised, de-duplicated role list from @tokens in the text plus an explicit list."""
    found: list[str] = []
    for m in MENTION_RE.findall(text or ""):
        r = canonical_role(m)
        if (r in ROLES or r == "master") and r not in found:
            found.append(r)
    for r in extra or []:
        r = canonical_role(r)
        if (r in ROLES or r == "master") and r not in found:
            found.append(r)
    return found


def roles_to_wake(message: dict, conversation: dict) -> list[str]:
    author = message["author"]
    if author == "system":
        return []
    roles = [r for r in message.get("mentions", []) if r != author and r in ROLES]
    if conversation["kind"] == "dm" and conversation["role"] and conversation["role"] != author:
        if conversation["role"] not in roles:
            roles.append(conversation["role"])
    if author == "master" and conversation["kind"] in ("channel", "thread", "feature") and not message.get("mentions"):
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
