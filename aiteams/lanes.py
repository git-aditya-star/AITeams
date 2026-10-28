"""F-41: lanes. Every role turn runs in a lane: "" is the role's main session (channels, DMs, standups, features without a
worktree of their own), a feature key such as "F-36" is the role's own session for that feature. One lane = one Claude
session = one turn at a time; different lanes of a role run side by side."""
from __future__ import annotations

from . import branches

MAIN = ""


def lane_for_feature(db, feature_id: int | None) -> str:
    """The lane work on a feature runs in: its key when the feature is open and has its OWN live worktree (a Branch card in
    `new` mode, tree still there); else the main lane. `current` and `shared` features stay on the main lane, so two
    sessions never edit one tree. Reads rows directly (never `db.feature`, whose stall block asks this)."""
    r = db.one("SELECT phase, archived FROM features WHERE id=?", feature_id) if feature_id is not None else None
    if r is None or r["phase"] == "done" or r["archived"]:
        return MAIN
    row = db.feature_branch(feature_id)
    return f"F-{feature_id}" if row and row["mode"] == "new" and branches.tree_for(db, feature_id) else MAIN


def lane_for(db, conversation_id: str | None) -> str:
    """The lane a wake from `conversation_id` runs in: `lane_for_feature` for a feature conversation, else the main lane."""
    r = db.one("SELECT id FROM features WHERE conversation_id=?", conversation_id) if conversation_id else None
    return lane_for_feature(db, r["id"]) if r else MAIN


def label(role: str, lane: str) -> str:
    """"backend" for the main lane, "backend · F-36" for a feature lane (a lock holder, a log line)."""
    return f"{role} · {lane}" if lane else role


def system_line(role_title: str, lane: str) -> str | None:
    """The line a lane's system prompt carries, None for the main lane."""
    if not lane:
        return None
    return (f"You are {role_title}, in your session for {lane}. Work only on {lane}; anything else goes to the right "
            "conversation and your main session picks it up. Migration-number claims, team questions and anything outside "
            f"{lane} go to `general`.")
