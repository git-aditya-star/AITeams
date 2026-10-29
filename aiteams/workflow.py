"""Features, approvals, commit requests, locks and the Inbox: logic shared by the public and internal routers."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time

from fastapi import HTTPException

from .config import LEADS, ROLES, canonical_role, effort_for, model_for, model_label, role_name, rotation_limit
from . import branches, commit_graph, lanes
from .db import PHASES, WAKE_KINDS, ProjectDB
from .runtime import Project


def _activity(proj: Project, kind: str, text: str, role=None, feature_id=None, conversation_id=None, ref=None):
    row = proj.db.add_activity(kind, text, role, feature_id, conversation_id, ref)
    return proj.hub.broadcast("activity", row)

COMMIT_STATES = ("lead_review", "master_review", "rejected", "committed", "failed", "withdrawn")


def _post(st, proj, conversation, author, text, type_, mentions, turn_id=None, ref=None):
    from .api.public import post_message  # late import: public imports nothing from here
    return post_message(st, proj, conversation, author, text, type_, mentions, turn_id, ref)


def _ago(ts: float | None) -> str:
    if not ts:
        return "?"
    d = int(time.time() - ts)
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


# ---- features --------------------------------------------------------------
def docs_dir(project_path: str, feat: dict) -> str:
    return os.path.join(project_path, ".aiteams", "features", feat["key"])


# F-29: a mock is a file sitting directly in the feature's docs folder. Only these extensions are served raw.
MOCK_KINDS = {".html": "html", ".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image"}
MOCK_TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".svg": "image/svg+xml"}
MOCK_MAX_BYTES = 10 * 1024 * 1024


def mock_kind(name: str) -> str | None:
    return MOCK_KINDS.get(os.path.splitext(name)[1].lower())


def docs_file(project_path: str, feat: dict, name: str, allowed, refused: str) -> tuple[str, os.stat_result]:
    """The one resolver for a file in a feature's docs folder (the Docs text endpoint and the raw mock endpoint), or the
    HTTPException to answer. 400: a name with a path in it (`/`, `\\`, NUL, `.`, `..`), a name `allowed(name)` rejects (the
    message is `refused`), a symlink of any kind (dangling or staying inside the folder too), or a file whose real path is
    not exactly <realpath project>/.aiteams/features/F-n/<name> (a symlinked docs folder included). 404: not a regular file.
    The caller adds its own limits (the mock size cap)."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise HTTPException(400, "bad file name")
    if not allowed(name):
        raise HTTPException(400, refused)
    p = os.path.join(docs_dir(project_path, feat), name)
    try:
        stt = os.lstat(p)
    except OSError:
        raise HTTPException(404, "no such file")
    if stat.S_ISLNK(stt.st_mode):
        raise HTTPException(400, "symlinks are not served")
    if not stat.S_ISREG(stt.st_mode):
        raise HTTPException(404, "no such file")
    if os.path.realpath(p) != os.path.join(docs_root(project_path, feat), name):
        raise HTTPException(400, "the file is outside the feature's docs folder")
    return p, stt


def docs_root(project_path: str, feat: dict) -> str:
    """Where the feature's docs folder must really be: <realpath project>/.aiteams/features/F-n."""
    return os.path.join(os.path.realpath(project_path), ".aiteams", "features", feat["key"])


def docs_list(db: ProjectDB, project_path: str, feat: dict) -> list[dict]:
    """The feature's docs: its `.md` files, then its mocks (F-29: html and images, kind `html` / `image`), each group
    sorted by name, then its published claude.ai artifacts (kind `artifact`, with `url`, `version` and the local
    `path`). Only regular files count as files: a symlink (F-30: `.md` too), a directory or a file in a subfolder is
    never listed, and a docs folder that is itself a symlink out of the project lists no files, because docs_file
    refuses all of them."""
    out = _docs_files(db, project_path, feat)
    for ar in db.artifacts(feat["id"]):
        try:
            size = os.stat(os.path.join(project_path, ar["path"])).st_size
        except OSError:
            size = 0
        out.append({"name": ar["title"] or os.path.basename(ar["path"]), "kind": "artifact", "size": size,
                    "updated_at": ar["updated_at"], "editor": ar["role"], "url": ar["url"], "version": ar["version"],
                    "path": ar["path"], "id": ar["id"]})
    return out


def _docs_files(db: ProjectDB, project_path: str, feat: dict) -> list[dict]:
    d = docs_dir(project_path, feat)
    rel_prefix = os.path.join(".aiteams", "features", feat["key"]) + os.sep
    edits = db.file_edits_for(rel_prefix)
    out = []
    if os.path.realpath(d) != docs_root(project_path, feat):
        return out
    try:
        all_names = os.listdir(d)
    except OSError:
        return out
    names = [(n, "md") for n in sorted(n for n in all_names if n.endswith(".md"))]
    names += [(n, mock_kind(n)) for n in sorted(all_names) if not n.endswith(".md") and mock_kind(n)]
    for n, kind in names:
        try:
            stt = os.lstat(os.path.join(d, n))
        except OSError:
            continue
        if not stat.S_ISREG(stt.st_mode):
            continue
        e = edits.get(rel_prefix + n)
        out.append({"name": n, "kind": kind, "size": stt.st_size, "updated_at": e["updated_at"] if e else stt.st_mtime,
                    "editor": e["role"] if e else None})
    return out


def mock_path(project_path: str, feat: dict, name: str) -> str:
    """F-29: the file behind GET .../files/{name}: docs_file's refusals, only html, png, jpg, jpeg and svg, and 413 over
    MOCK_MAX_BYTES."""
    p, stt = docs_file(project_path, feat, name, lambda n: mock_kind(n) is not None,
                       "only html, png, jpg, jpeg and svg files are served")
    if stt.st_size > MOCK_MAX_BYTES:
        raise HTTPException(413, f"the file is over {MOCK_MAX_BYTES // (1024 * 1024)} MB")
    return p


# ---- claude.ai artifacts (F-29 Part B) ---------------------------------------------------------------------
# A role turn has the real Artifact tool (CLAUDE_CODE_ARTIFACT=1). The hook asks artifact_gate before every call: a NEW
# page needs the Master's approved `Artifact: <file>` approval (one approval, one artifact), an UPDATE must pass the
# recorded url, a recorded page may be read back, and everything else (delete, list, open, pin, sharing) is the Master's.
ARTIFACT_DENIED = ("the Master manages artifacts on claude.ai: you can publish a page they approved, update it by its url, "
                   "or read a recorded page back. Delete, list, open, pin and sharing are theirs.")
# An allowlist, not a denylist: the tool takes many more params (paths, root, asset_ids, capabilities, force ...), and any of
# them could send more than the Master approved or give the page runtime access to their data. Same table in the hook.
ARTIFACT_PARAMS = {"publish": ("file_path", "url", "title", "description", "icon", "label"), "read": ("url", "page")}
ARTIFACT_RESULT_RE = re.compile(r"^\s*Published (.+?) at (https://claude\.ai/artifact/\S+) \(Version (\d+)")
ARTIFACT_MISSING = ("Claude artifacts are unavailable: this Claude Code version doesn't offer the Artifact tool in "
                    "headless runs. Local mocks still work.")


def artifact_action(inp: dict) -> str:
    return str(inp.get("action") or "publish").strip().lower()


def artifact_static_denial(inp: dict) -> str | None:
    """The part of the gate that needs no state: only `publish` and `read`, and only their allowlisted params (a key with a
    truthy value outside the list is denied by name; an empty value counts as unused)."""
    action = artifact_action(inp)
    allowed = ARTIFACT_PARAMS.get(action)
    if allowed is None:
        return ARTIFACT_DENIED
    extra = [k for k, v in inp.items() if v and k != "action" and k not in allowed]
    if extra:
        return f"the Artifact {action} may pass only {', '.join(allowed)}: remove {', '.join(extra)}."
    return None


def artifact_target(proj: Project, file_path) -> tuple[dict, str, str]:
    """(feature, file name, project-relative path) of the page a call names, or ValueError(reason): one `.html` regular
    file, not a symlink, directly in an existing feature's docs folder (docs_file's rules)."""
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("pass the page's file_path: one .html file in the feature's docs folder (.aiteams/features/F-n/)")
    ap = os.path.normpath(file_path if os.path.isabs(file_path) else os.path.join(proj.path, file_path))
    key, name = os.path.basename(os.path.dirname(ap)), os.path.basename(ap)
    feat = proj.db.feature(key) if re.fullmatch(r"F-\d+", key) else None
    if os.path.islink(ap):
        raise ValueError("a symlink can't be published")
    if feat is None or os.path.realpath(ap) != os.path.join(docs_root(proj.path, feat), name):
        raise ValueError("only an .html page directly in a feature's docs folder (.aiteams/features/F-n/) can be published")
    try:
        docs_file(proj.path, feat, name, lambda n: n.lower().endswith(".html"), "only an .html page can be published")
    except HTTPException as e:
        raise ValueError(str(e.detail))
    return feat, name, f".aiteams/features/{feat['key']}/{name}"


def artifact_gate(proj: Project, turn: dict, inp: dict) -> dict:
    """{ok, reason} for one Artifact call from `turn`'s role. A publish that is allowed as NEW writes a pending row (url
    NULL, this turn) in the same step, so a second new publish of the page is refused until the capture fills it in; a
    turn that ends without its capture gives the row back (and a pending row of a turn that is no longer running is stale)."""
    db = proj.db
    why = artifact_static_denial(inp)
    if why:
        return {"ok": False, "reason": why}
    url = str(inp.get("url") or "").strip()
    if artifact_action(inp) == "read":
        if url and db.artifact_by_url(url):
            return {"ok": True, "reason": ""}
        return {"ok": False, "reason": "read only a page AITeams recorded: pass the url of one of this team's artifacts."}
    try:
        feat, name, rel = artifact_target(proj, inp.get("file_path"))
    except ValueError as e:
        return {"ok": False, "reason": str(e)}
    with db.lock:
        rec = db.artifact_by_path(feat["id"], rel)
        if rec and rec["url"] is None:
            t = db.turn(rec["turn_id"]) if rec["turn_id"] else None
            if t and t["state"] == "running":
                return {"ok": False, "reason": f"a publish of {name} is already in progress; wait for its result."}
            db.delete_artifact(rec["id"])  # its turn ended or died without a capture: give it back
            rec = db.artifact_by_path(feat["id"], rel)
        if url:
            if rec and rec["url"] == url:
                return {"ok": True, "reason": ""}  # an update of the page the Master approved
            hint = f"pass url: {rec['url']} to update it." if rec else \
                f"publish it without url once the Master approves 'Artifact: {name}'."
            return {"ok": False, "reason": f"that url isn't the artifact of {name}: {hint}"}
        if rec:
            return {"ok": False, "reason": f"{name} is already published at {rec['url']}: pass url: {rec['url']} to update "
                                           "it. A second new artifact needs a new page and a new approval."}
        norm = lambda t: " ".join((t or "").lower().split())  # noqa: E731  (case and spacing don't matter, like F-21's titles)
        approved = [a for a in db.approvals("approved", feat["id"]) if norm(a["title"]) == norm(f"artifact: {name}")]
        step = (f"request_approval(feature, 'Artifact: {name}', what, if_approved, tradeoffs, doc='{name}'), end your turn, "
                "and publish after the approving Decision.")
        if not approved:
            return {"ok": False, "reason": f"the Master hasn't approved publishing {name}: {step}"}
        now_sha = _sha(proj.path, rel)
        match = next((a for a in approved if now_sha and db.get_kv(f"artifact_sha:{a['id']}") == now_sha), None)
        if match is None:
            return {"ok": False, "reason": f"{name} changed after the Master approved it (or the approval pinned no page): "
                                           f"ask again with the page as it is now: {step}"}
        db.create_artifact(feat["id"], rel, turn["role"], match["id"], turn["id"])
    return {"ok": True, "reason": ""}


def html_title(path: str) -> str | None:
    """The page's <title>, if it has a readable one."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
    except OSError:
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = " ".join(html.unescape(m.group(1)).split())[:120] if m else ""
    return title or None


async def record_artifact(st, proj: Project, role: str, turn_id: int | None, inp: dict | None, result_path: str,
                          url: str, version: int) -> dict | None:
    """Capture one `Published <path> at <url> (Version n` tool result: fill the pending row (a new page) or bump the
    row with that url (an update), then post the link card in the feature conversation. None when the path isn't one of
    our pages (nothing is recorded)."""
    db = proj.db
    target = None
    for fp in ((inp or {}).get("file_path"), result_path):
        try:
            target = artifact_target(proj, fp)
            break
        except ValueError:
            continue
    if target is None:
        return None
    feat, name, rel = target
    title = html_title(os.path.join(proj.path, rel)) or name
    with db.lock:
        row = db.artifact_by_url(url)
        if row:
            if row["version"] == version and row["turn_id"] == turn_id:
                return row  # the same result seen twice
            row, word = db.update_artifact(row["id"], version=version, title=title, role=role, turn_id=turn_id), "Updated"
        else:
            pend = db.artifact_by_path(feat["id"], rel)
            if pend and pend["url"] is None:
                row = db.update_artifact(pend["id"], url=url, title=title, version=version, turn_id=turn_id)
            else:
                row = db.create_artifact(feat["id"], rel, role, None, turn_id, url, title, version)
            word = "Published"
    text = f"{word} {title}" + (f" (v{version})" if word == "Updated" else "") + f"\n\n{url}"
    await _post(st, proj, feat["conversation_id"], role, text, "Message", [], turn_id, f"artifact:{row['id']}")
    return row


def design_state(db: ProjectDB, feat: dict) -> str:
    """F-21: approved | pending | changes | rejected | none, from the feature's latest `Design:` approval."""
    return db.design_info(feat["id"], feat["created_at"])["state"]


DESIGN_WORDS = {"approved": "approved", "pending": "pending", "changes": "changes requested", "rejected": "rejected",
                "none": "not requested"}


def design_line(db: ProjectDB, feat: dict) -> str:
    d = db.design_info(feat["id"], feat["created_at"])
    text = DESIGN_WORDS[d["state"]] + (f" (#{d['approval_id']})" if d["approval_id"] else "")
    return "- design: " + text + (" · not gated" if not d["gated"] and d["state"] == "none" else "")


def branch_line(db: ProjectDB, feat: dict) -> str | None:
    """F-39: the header's `- branch:` line, or None for a feature that isn't gated and has no branch (older features)."""
    b = db.branch_info(feat["id"], feat["created_at"])
    if b["state"] == "approved":
        if b["tree"]:
            frm = f" from {b['base']}" if b["base"] else (f" (shared with F-{b['shared_from']})" if b["shared_from"] else "")
            return (f"- branch: {b['branch']}{frm} · tree {branches.tilde(b['tree'])} "
                    f"(edit and test there; docs stay in .aiteams/features/{feat['key']}/)")
        return f"- branch: current folder ({b['branch']})"
    if b["gated"]:
        return "- branch: not confirmed (no code until the Master approves a Branch card)"
    return None


def feature_header(db: ProjectDB, project_path: str, feat: dict, with_sizes: bool = False) -> str:
    lines = [f"# Feature {feat['key']}: {feat['title']}",
             f"- phase: {feat['phase']} · owner: {feat['owner'] or 'none'} · conversation id: {feat['conversation_id']}"
             f" · lead: {role_name(feat['lead']) if feat.get('lead') else 'not assigned'}"
             + (" · ARCHIVED" if feat["archived"] else "")]
    lines.append(design_line(db, feat))
    if (bl := branch_line(db, feat)):
        lines.append(bl)
    docs = docs_list(db, project_path, feat)
    rel = os.path.join(".aiteams", "features", feat["key"]) + "/"
    if docs:
        parts = [f"{d['name']} ({d['editor'] or 'unknown'}, {_ago(d['updated_at'])}" + (f", {d['size']} B)" if with_sizes else ")")
                 for d in docs]
        lines.append(f"- docs ({rel}): " + " · ".join(parts))
    else:
        lines.append(f"- docs ({rel}): none yet")
    apps = db.approvals("pending", feat["id"])
    if apps:
        lines.append("- open approvals: " + " · ".join(f"#{a['id']} \"{a['title']}\" (pending)" for a in apps))
    commits = db.commits("all", feat["id"], limit=5)
    if commits:
        parts = []
        for c in commits:
            s = f"#{c['id']} {c['state']}"
            s += f" {c['sha']}" if c["sha"] else f" ({c['role']}, {len(c['files'])} files)"
            parts.append(s)
        lines.append("- commits: " + " · ".join(parts))
    lines.append("- voices: " + ", ".join(voices(db, feat)))
    locks = db.locks()
    if locks:
        lines.append("- locks now: " + " · ".join(f"{l['path']} ({lanes.label(l['role'], l.get('lane') or '')})" for l in locks))
    return "\n".join(lines)


async def new_feature(st, proj: Project, title: str, phase: str = "intake", owner: str | None = None,
                      by: str = "master", text: str | None = None, starter: str | None = None) -> dict:
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    if phase not in PHASES:
        raise HTTPException(400, f"phase must be one of {', '.join(PHASES)}")
    if owner and owner not in ROLES:
        raise HTTPException(400, "unknown owner role")
    if by == "master" and starter is not None and starter not in ROLES:
        raise HTTPException(400, "unknown starter role")
    feat = proj.db.create_feature(title[:200], phase, owner)
    os.makedirs(docs_dir(proj.path, feat), exist_ok=True)
    await _activity(proj, "feature_created", f"{feat['key']} {feat['title']} created by {'Master' if by == 'master' else by}",
                    None if by == "master" else by, feat["id"], feat["conversation_id"])
    await proj.hub.broadcast("conversation", proj.db.conversation(feat["conversation_id"]))
    await proj.hub.broadcast("feature", feat)
    if by == "master":
        from .orchestrator.router import parse_mentions
        body = text or title
        mentions = parse_mentions(body)
        if starter and starter in ROLES and starter not in mentions:
            body = f"@{starter} {body}"
            mentions = parse_mentions(body)
        await _post(st, proj, feat["conversation_id"], "master", body, "Message", mentions)
    else:
        await _post(st, proj, feat["conversation_id"], "system", f"Created by {by}", "System", [])
    return proj.db.feature(feat["id"])


async def set_feature(st, proj: Project, feat: dict, fields: dict, by: str) -> dict:
    changes = {}
    if fields.get("phase") is not None:
        if fields["phase"] not in PHASES:
            raise HTTPException(400, f"phase must be one of {', '.join(PHASES)}")
        if fields["phase"] != feat["phase"]:
            changes["phase"] = fields["phase"]
    if "owner" in fields and fields["owner"] != feat["owner"]:
        if fields["owner"] and fields["owner"] not in ROLES:
            raise HTTPException(400, "unknown owner role")
        changes["owner"] = fields["owner"] or None
    if "lead" in fields:  # F-22: the Manager assigns a feature's Lead; the Master can change it from the header
        if by not in ("master", "manager"):
            raise HTTPException(403, "only the Manager assigns a feature's Lead")
        lead = canonical_role(fields["lead"]) or None
        if lead is None and by != "master":
            raise HTTPException(400, "lead must be lead-1 or lead-2")
        if lead is not None and lead not in LEADS:
            raise HTTPException(400, "lead must be lead-1 or lead-2")
        if lead != feat.get("lead"):
            changes["lead"] = lead
    if fields.get("title") and fields["title"].strip() != feat["title"]:
        changes["title"] = fields["title"].strip()[:200]
    if fields.get("archived") is not None and bool(fields["archived"]) != feat["archived"]:
        changes["archived"] = bool(fields["archived"])
    if not changes:
        return feat
    feat = proj.db.update_feature(feat["id"], **changes)
    if changes.get("phase") == "done" or changes.get("archived"):
        await _broadcast_team(st, proj)  # F-41: its lane sessions just retired
    who = "Master" if by == "master" else by
    lines = []
    for k, v in changes.items():
        if k == "archived":
            lines.append(f"{who} {'archived' if v else 'unarchived'} {feat['key']}")
        elif k == "lead":
            lines.append(f"Lead set to {role_name(v) if v else 'not assigned'} by {who}")
        else:
            lines.append(f"{who} set {k} {v if v is not None else 'none'}")
    await _post(st, proj, feat["conversation_id"], "system", "\n".join(lines), "System", [])
    if changes.get("lead"):  # the new Lead gets a wake in the feature; locks don't move
        await _post(st, proj, feat["conversation_id"], "master" if by == "master" else by,
                    f"@{changes['lead']} {feat['key']} is yours now.", "Message", [changes["lead"]])
    if "phase" in changes:
        await _activity(proj, "phase", f"{feat['key']} phase → {changes['phase']} ({who})",
                        None if by == "master" else by, feat["id"], feat["conversation_id"])
    await proj.hub.broadcast("feature", feat)
    return feat


async def _broadcast_team(st, proj: Project) -> None:
    await proj.hub.broadcast("team", {"roles": team_status(st, proj)})


def lead_open_features(db: ProjectDB, role: str) -> list[str]:
    """F-22: keys of the open (not done, not archived) features whose Lead is `role`, oldest first."""
    return [f"F-{r['id']}" for r in db.q("SELECT id FROM features WHERE lead=? AND phase!='done' AND archived=0"
                                         " ORDER BY id", role)]


def _lead_busy(st, proj: Project, role: str) -> bool:
    running = st.scheduler.running if st.scheduler else {}  # F-41: busy in any lane
    return any(k[:2] == (proj.id, role) for k in running) or any(proj.db.queued_wake(role, k, None) for k in WAKE_KINDS)


async def feature_lead(st, proj: Project, feat: dict) -> str:
    """F-22: the Lead of a feature. A feature with no Lead yet (the Manager hasn't assigned one) gets one now, so
    nothing waits on the Manager's turn: an idle Lead if there is one, else whichever has fewer open features,
    then Lead 1. The pick is stored and announced in the feature."""
    pick, line = await _pick_lead(st, proj, feat)
    if line:
        await _post(st, proj, feat["conversation_id"], "system", line, "System", [])
    return pick


async def _pick_lead(st, proj: Project, feat: dict) -> tuple[str, str | None]:
    """feature_lead without the announcement: (the Lead, the System line to post or None if it was already set)."""
    db = proj.db
    fresh = db.feature(feat["id"]) or feat
    if fresh.get("lead") in LEADS:
        return fresh["lead"], None
    busy = {r: _lead_busy(st, proj, r) for r in LEADS}
    pool = [r for r in LEADS if not busy[r]] or list(LEADS)
    load = {r: len(lead_open_features(db, r)) for r in LEADS}
    pick = min(pool, key=lambda r: (load[r], LEADS.index(r)))
    other = next(r for r in LEADS if r != pick)
    why = (f"{role_name(other)} was busy" if not busy[pick] and busy[other]
           else "fewer open features" if load[pick] != load[other] else "default")
    feat = db.update_feature(fresh["id"], lead=pick)
    await proj.hub.broadcast("feature", feat)
    return pick, f"{role_name(pick)} picked automatically ({why})."


def feature_of_conversation(db: ProjectDB, conversation_id: str | None) -> dict | None:
    """The feature whose conversation this is (None for DMs, channels and unknown ids)."""
    conv = db.conversation(conversation_id) if conversation_id else None
    return db.feature(conv["id"]) if conv and conv["kind"] == "feature" else None


async def resolve_lead_mentions(st, proj: Project, feat: dict | None, mentions: list[str]) -> tuple[list[str], str | None]:
    """F-22: in a feature, any Lead mention (@lead, @lead-1, @lead-2) means the feature's Lead. The resolved role is
    what gets stored and woken, so the UI and the router agree. Elsewhere mentions are left as they are. Returns
    (mentions, the System line announcing an automatic pick or None): the caller posts it after the message itself,
    so a new feature's first line is the Master's request."""
    if feat is None or not any(m in LEADS for m in mentions):
        return mentions, None
    lead, line = await _pick_lead(st, proj, feat)
    out: list[str] = []
    for m in mentions:
        m = lead if m in LEADS else m
        if m not in out:
            out.append(m)
    return out, line


async def guard_lead(st, proj: Project, role: str, feat: dict | None, claim: bool = True) -> None:
    """F-22: a Lead acts only in its own features. 409 for a feature whose Lead is the other one (reading is never
    guarded); a feature with no Lead yet is claimed by the first Lead that acts in it, with a System line. Call it
    with claim=False first where a refusal must change nothing (before the F-16 guard), then again to claim."""
    if role not in LEADS or feat is None:
        return
    fresh = proj.db.feature(feat["id"]) or feat
    if fresh.get("lead") is None:
        if not claim:
            return
        fresh = proj.db.update_feature(fresh["id"], lead=role)
        await _post(st, proj, fresh["conversation_id"], "system",
                    f"{role_name(role)} claimed {fresh['key']} (it had no Lead yet).", "System", [])
        await proj.hub.broadcast("feature", fresh)
    elif fresh["lead"] != role:
        raise HTTPException(409, f"{fresh['key']} belongs to {role_name(fresh['lead'])}; you can read it but not act "
                                 "in it. Ask @manager to reassign it if needed.")


def is_branch_title(title: str | None) -> bool:
    """F-39: a Branch card's approval title starts with `Branch:` (trimmed, case-insensitive), like db.branch_info."""
    return (title or "").strip().lower().startswith("branch:")


def is_pr_title(title: str | None) -> bool:
    return (title or "").strip().lower().startswith("pr:")


def is_design_title(title: str | None) -> bool:
    """A design approval's title starts with `Design:` (trimmed, case-insensitive), like db.design_info."""
    return (title or "").strip().lower().startswith("design:")


def resolve_feature(db: ProjectDB, ident) -> dict | None:
    if ident is None or str(ident).strip() == "":
        return None
    feat = db.feature(ident)
    if feat is None:
        raise HTTPException(404, f"unknown feature: {ident}")
    return feat


# ---- F-10: voices and question cards -----------------------------------------
CARD_PURPOSES = ("ask", "interview", "concern", "a_vs_b", "brainstorm")


def voices(db: ProjectDB, feat: dict) -> list[str]:
    """Roles with a voice on a feature: authors of / mentioned in its conversation (non-system), plus Product and
    the feature's Lead; in ROLES order."""
    who = {"product", feat.get("lead") or "lead"}
    for r in db.q("SELECT author, mentions FROM messages WHERE conversation_id=? AND author != 'system'",
                  feat["conversation_id"]):
        who.add(r["author"])
        try:
            who.update(m for m in json.loads(r["mentions"] or "[]") if isinstance(m, str))
        except ValueError:
            pass
    return [r for r in ROLES if r in who]


def validate_card(questions) -> list[dict]:
    """AskUserQuestion shape, 1-6 questions with 2-4 options each. Returns a normalized copy; HTTP 400 otherwise."""
    def bad(msg):
        raise HTTPException(400, msg)
    if not isinstance(questions, list) or not 1 <= len(questions) <= 6:
        bad("a card has 1 to 6 questions")
    out, texts = [], set()
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            bad(f"question {i} must be an object")
        text = str(q.get("question") or "").strip()
        header = str(q.get("header") or "").strip()
        if not text:
            bad(f"question {i} has no text")
        if text in texts:
            bad(f"question {i} repeats an earlier question")
        texts.add(text)
        if not header or len(header) > 12:
            bad(f"question {i} needs a header of 1 to 12 characters")
        opts = q.get("options")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
            bad(f"question {i} needs 2 to 4 options (the UI adds Other itself)")
        labels, clean = set(), []
        for o in opts:
            label = str((o or {}).get("label") or "").strip() if isinstance(o, dict) else ""
            if not label or label in labels:
                bad(f"question {i} has an empty or repeated option label")
            if label.lower() == "other":
                bad(f"question {i}: don't add an Other option, the UI always adds it")
            if q.get("multiSelect") and "," in label:
                bad(f"question {i}: option labels of a multi-select question can't contain a comma "
                    "(the picked labels are joined with commas)")
            labels.add(label)
            clean.append({"label": label, "description": str(o.get("description") or "")})
        out.append({"question": text, "header": header, "multiSelect": bool(q.get("multiSelect")), "options": clean})
    return out


def _card_text(intro: str, questions: list[dict]) -> str:
    lines = [intro.strip(), ""] if intro.strip() else []
    for i, q in enumerate(questions, 1):
        lines.append(f"**{i}. {q['question']}**" + (" (pick any)" if q["multiSelect"] else ""))
        lines += [f"- {o['label']}" + (f": {o['description']}" if o["description"] else "") for o in q["options"]]
    return "\n".join(lines).strip()


async def ask_master(st, proj: Project, role: str, turn_id: int | None, feature, questions, intro: str = "",
                     purpose: str = "ask") -> dict:
    """Post a non-blocking question card to the Master; the asking role ends its turn and is woken by the answer."""
    db = proj.db
    if purpose not in CARD_PURPOSES:
        raise HTTPException(400, f"purpose must be one of {', '.join(CARD_PURPOSES)}")
    qs = validate_card(questions)
    feat = resolve_feature(db, feature)
    if purpose == "concern":
        if feat is None:
            raise HTTPException(400, "a concern card needs a feature")
        if role not in voices(db, feat):
            raise HTTPException(403, f"{role} has no voice on {feat['key']} (voices: {', '.join(voices(db, feat))})")
    if purpose == "a_vs_b":
        lead = (feat.get("lead") if feat else None) or "lead"  # F-22: the feature's Lead
        owners = [lead, "product"] + ([feat["owner"]] if feat and feat["owner"] not in (None, "product", *LEADS) else [])
        if role not in owners:
            raise HTTPException(403, f"only the owner ({', '.join(owners[:-1])} or {owners[-1]}) sends an A vs B card; "
                                     "post the owner a Concern saying you still disagree")
    bs = None
    if purpose == "brainstorm":
        bs = _compiling_brainstorm(db, role, turn_id)
        if feat is not None and feat["id"] != bs["feature_id"]:
            raise HTTPException(400, f"brainstorm #{bs['id']} belongs to {db.feature(bs['feature_id'])['key']}")
        feat = db.feature(bs["feature_id"])
    conv = feat["conversation_id"] if feat else f"dm-{role}"
    intro = (intro or "").strip()
    q = db.create_card(turn_id, role, {"questions": qs}, purpose, intro, feat["id"] if feat else None, conv)
    msg = await _post(st, proj, conv, role, _card_text(intro, qs), "Concern" if purpose == "concern" else "Question",
                      [], turn_id, f"question:{q['id']}")
    db.x("UPDATE questions SET message_id=? WHERE id=?", msg["id"], q["id"])
    q = db.question(q["id"])
    await proj.hub.broadcast("question", q)
    await broadcast_inbox(proj)
    if bs:
        bs = db.update_brainstorm(bs["id"], state="done", question_id=q["id"], ended_at=time.time())
        await proj.hub.broadcast("brainstorm", bs)
    return q


def _compiling_brainstorm(db: ProjectDB, role: str, turn_id: int | None) -> dict:
    """The round a `purpose=brainstorm` card closes: only Product, only in its brainstorm compile turn."""
    turn = db.turn(turn_id) if turn_id else None
    wake = db.wake(turn["wake_id"]) if turn and turn.get("wake_id") else None
    bs = db.brainstorm(wake["ref"]) if wake and wake.get("ref") and turn.get("kind") == "brainstorm_compile" else None
    if role != "product" or bs is None:
        raise HTTPException(403, "brainstorm cards come only from Product's brainstorm compile turn")
    if bs["state"] != "compiling":
        raise HTTPException(409, f"brainstorm #{bs['id']} is {bs['state']}")
    return bs


# F-34: kind -> (label in chat, hub frame / activity kind)
WITHDRAW_KINDS = {"approval": ("Approval", "approval"), "commit": ("Commit request", "commit"), "card": ("Card", "question")}
WITHDRAW_REASON_MAX = 200


async def withdraw_request(st, proj: Project, role: str, kind: str, rid: int, reason: str) -> dict:
    """F-34: a role pulls back a request it sent (or, as the feature's Lead, any in its feature) that the Master hasn't
    decided: an approval, a commit request or a question card. It leaves the Inbox, keeps its row as `withdrawn` with the
    reason, and a System line says who withdrew it and why. The checks and the store run without an await in between,
    so nothing can change under them."""
    db = proj.db
    reason = (reason or "").strip()
    if not reason or "\n" in reason or "\r" in reason:
        raise HTTPException(400, "give a one-line reason (no line breaks); it is shown in chat")
    reason = reason[:WITHDRAW_REASON_MAX]
    if kind not in WITHDRAW_KINDS:
        raise HTTPException(400, "kind must be approval|commit|card")
    item = {"approval": db.approval, "commit": db.commit, "card": db.question}[kind](rid)
    if item is None or (kind == "card" and item.get("kind") != "card"):
        raise HTTPException(404, f"unknown {kind} #{rid}")
    feat = db.feature(item["feature_id"]) if item.get("feature_id") else None
    lead = feat["lead"] if feat else None  # the feature's own Lead; never the F-22 claim path
    if role not in (item["role"], lead):
        who = item["role"] if not lead or lead == item["role"] else f"{item['role']} or {lead}"
        raise HTTPException(403, f"only {who} can withdraw {kind} #{rid}")
    if item["state"] not in (PENDING_COMMIT_STATES if kind == "commit" else ("pending",)):
        raise HTTPException(409, f"{kind} #{rid} is {item['state']}")
    if kind == "commit":
        if rid in proj.committing:
            raise HTTPException(409, f"commit #{rid} is being committed")
        for d in db.linked_commits():
            if d["state"] in PENDING_COMMIT_STATES and rid in d["requires"]:
                raise HTTPException(409, f"#{d['id']} requires #{rid}: withdraw or re-file #{d['id']} first")
        item = db.update_commit(rid, state="withdrawn", note=reason)
    elif kind == "approval":
        item = db.resolve_approval(rid, "withdrawn", reason)
    else:
        item = db.withdraw_question(rid, reason)
    label, frame = WITHDRAW_KINDS[kind]
    conv = (item.get("conversation_id") if kind == "card" else None) or (
        feat["conversation_id"] if feat else f"dm-{item['role']}")
    text = f"{label} #{rid} withdrawn by {role}: {reason}" + ("" if reason[-1] in ".!?" else ".")
    # the sender is mentioned only when someone else (the Lead) withdrew its request, so it knows
    await _post(st, proj, conv, "system", text, "System", [item["role"]] if role != item["role"] else [], None,
                f"{COMMIT_EVENT}{rid}" if kind == "commit" else None)
    await _activity(proj, frame, f"{label} #{rid} withdrawn by {role}", item["role"], item.get("feature_id"), conv,
                    f"{frame}:{rid}")
    await proj.hub.broadcast(frame, item)
    if kind == "approval":
        await _broadcast_design(proj, item)  # a withdrawn redesign request changes the feature's design chip
    await broadcast_inbox(proj)
    await _recheck(st, proj, item.get("feature_id"))
    return item


def _answer_line(q: dict, answer: str) -> str:
    labels = {o["label"] for o in q["options"]}
    parts = [p.strip() for p in answer.split(",")] if q["multiSelect"] else [answer.strip()]
    known = answer.strip() in labels or (q["multiSelect"] and all(p in labels for p in parts if p))
    return answer.strip() if known else f"(Other) {answer.strip()}"


async def answer_card(st, proj: Project, q: dict, answers: dict[str, str]) -> dict:
    """Store the Master's answers and post them as a Decision that mentions (and wakes) the asking role."""
    picked = []
    for item in q["payload"].get("questions", []):
        ans = answers.get(item["question"])
        if ans is None:
            ans = answers.get(item["header"])
        if not (ans or "").strip():
            raise HTTPException(400, f"answer every question: \"{item['header'] or item['question']}\" has no answer")
        picked.append((item, ans))
    q = proj.db.answer_question(q["id"], answers, "answered")
    lines = [f"@{q['role']} Answers to card #{q['id']}:"]
    lines += [f"- **{item['header'] or item['question']}**: {_answer_line(item, ans)}" for item, ans in picked]
    await _post(st, proj, q["conversation_id"], "master", "\n".join(lines), "Decision", [q["role"]])
    await proj.hub.broadcast("question", q)
    await broadcast_inbox(proj)
    return q


# ---- approvals ---------------------------------------------------------------
async def _broadcast_design(proj: Project, a: dict) -> None:
    """F-25: a `Design:` approval changes its feature's `design` chip, so send the feature (the UI reads it from there)."""
    if a["feature_id"] and is_design_title(a["title"]):
        await proj.hub.broadcast("feature", proj.db.feature(a["feature_id"]))


# F-40: every approval a role requests carries a three-part brief the Master reads on the card
BRIEF_WHAT_MAX, BRIEF_IF_MAX, BRIEF_TRADEOFF_MAX, BRIEF_TRADEOFFS = 200, 300, 200, 3
BRIEF_DOC_KINDS = "a .md file, an .html page or an image"


def is_done_title(title: str | None) -> bool:
    """F-40: approving a `Done:` request moves its feature to `done` (the card's phase dropdown is gone)."""
    return (title or "").strip().lower().startswith("done:")


def _brief_doc(proj: Project, feat: dict | None, doc) -> str | None:
    """The brief's optional `doc`: a file in the feature's docs folder, as a bare name or `.aiteams/features/F-n/<name>`
    (also with the project path in front), stored as the bare name. It must exist; anything else is a 400."""
    doc = (doc or "").strip() if isinstance(doc, str) else doc
    if doc is None or doc == "":
        return None
    if not isinstance(doc, str):
        raise HTTPException(400, "doc must be a file name in the feature's docs folder")
    if feat is None:
        raise HTTPException(400, "doc needs a feature: it is a file in the feature's docs folder")
    rel = f".aiteams/features/{feat['key']}/"
    name = doc[2:] if doc.startswith("./") else doc
    for prefix in (rel, os.path.join(proj.path, rel), os.path.join(os.path.realpath(proj.path), rel)):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if "/" in name or "\\" in name:
        raise HTTPException(400, f"doc must be a file in {feat['key']}'s docs folder ({rel}<name>), got {doc!r}")
    try:
        docs_file(proj.path, feat, name, lambda n: n.lower().endswith(".md") or mock_kind(n) is not None,
                  f"doc must be {BRIEF_DOC_KINDS}")
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(400, f"doc {name!r} doesn't exist in {feat['key']}'s docs folder ({rel})")
        raise HTTPException(400, f"doc {name!r}: {e.detail}")
    return name


def check_brief(proj: Project, feat: dict | None, parts: dict) -> dict:
    """Validate a raw brief `{what, if_approved, tradeoffs, doc}` and return it clean:
    what (one line, <= 200), if_approved (<= 300), tradeoffs (1 to 3 items, each non-empty, <= 200), doc (optional, see
    _brief_doc). A 400 names the missing or invalid part."""
    def text(v) -> str:
        return v.strip() if isinstance(v, str) else ""
    what, if_approved = text(parts.get("what")), text(parts.get("if_approved"))
    tradeoffs = parts.get("tradeoffs")
    if isinstance(tradeoffs, str):
        tradeoffs = [tradeoffs]
    if not isinstance(tradeoffs, list):
        tradeoffs = []
    missing = [n for n, v in (("what", what), ("if_approved", if_approved), ("tradeoffs", tradeoffs)) if not v]
    if missing:
        raise HTTPException(400, "brief needs What / If you approve / Trade-offs (1-3): missing " + ", ".join(missing)
                                 + " (pass what, if_approved and tradeoffs; summary is no longer used)")
    if "\n" in what or "\r" in what:
        raise HTTPException(400, "brief what must be one line")
    if len(what) > BRIEF_WHAT_MAX:
        raise HTTPException(400, f"brief what is too long: {len(what)} characters, at most {BRIEF_WHAT_MAX}")
    if len(if_approved) > BRIEF_IF_MAX:
        raise HTTPException(400, f"brief if_approved is too long: {len(if_approved)} characters, at most {BRIEF_IF_MAX}")
    if len(tradeoffs) > BRIEF_TRADEOFFS:
        raise HTTPException(400, f"brief tradeoffs has {len(tradeoffs)} items, at most {BRIEF_TRADEOFFS}")
    items = []
    for i, t in enumerate(tradeoffs, 1):
        t = text(t)
        if not t:
            raise HTTPException(400, f"brief tradeoffs item {i} is empty (use 'none' if there are none)")
        if len(t) > BRIEF_TRADEOFF_MAX:
            raise HTTPException(400, f"brief tradeoffs item {i} is too long: {len(t)} characters, at most {BRIEF_TRADEOFF_MAX}")
        items.append(t)
    return {"what": what, "if_approved": if_approved, "tradeoffs": items, "doc": _brief_doc(proj, feat, parts.get("doc"))}


def render_brief(brief: dict, feat: dict | None = None) -> str:
    """The brief as Markdown: it fills `summary` (what roles read in chat and History searches) and the chat message."""
    lines = [f"**What:** {brief['what']}", f"**If you approve:** {brief['if_approved']}", "**Trade-offs:**"]
    lines += [f"- {t}" for t in brief["tradeoffs"]]
    if brief.get("doc"):
        lines.append(f"**Doc:** `.aiteams/features/{feat['key']}/{brief['doc']}`" if feat else f"**Doc:** `{brief['doc']}`")
    return "\n".join(lines)


async def request_approval(st, proj: Project, role: str, turn_id: int | None, feature, title: str, summary: str = "",
                           payload: dict | None = None, brief: dict | None = None) -> dict:
    """F-40: `brief` is the raw `{what, if_approved, tradeoffs, doc}` a role passed; it is validated here (a missing or bad
    part is a 400 that names it) and `summary` is rendered from it. `brief=None` is the legacy path for daemon-internal
    callers (the Branch card): `summary` is stored as given. The /internal route always passes a brief."""
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    feat = resolve_feature(proj.db, feature)
    if brief is not None:
        brief = check_brief(proj, feat, brief)
        summary = render_brief(brief, feat)
    a = proj.db.create_approval(feat["id"] if feat else None, role, turn_id, title[:200], summary or "", payload, brief)
    if feat and title.lower().startswith("artifact:"):  # F-29 B: the Master approves THIS page; pin its content
        name = " ".join(title[len("artifact:"):].split())
        if name and "/" not in name and "\\" not in name:
            proj.db.set_kv(f"artifact_sha:{a['id']}", _sha(proj.path, f".aiteams/features/{feat['key']}/{name}"))
    conv = feat["conversation_id"] if feat else f"dm-{role}"
    text = f"**Approval requested: {title}**\n\n{summary or ''}".rstrip()
    msg = await _post(st, proj, conv, role, text, "Approval", ["master"], turn_id, f"approval:{a['id']}")
    proj.db.x("UPDATE approvals SET message_id=? WHERE id=?", msg["id"], a["id"])
    a = proj.db.approval(a["id"])
    await proj.hub.broadcast("approval", a)
    await _broadcast_design(proj, a)
    await broadcast_inbox(proj)
    return a


async def propose_branch(st, proj: Project, role: str, turn_id: int | None, feature, mode: str, branch: str | None = None,
                         base: str | None = None, shared_from: str | None = None, why: str = "") -> dict:
    """F-39: the feature's Lead proposes how the feature works on git (a new branch in its own worktree, the current branch,
    or another feature's worktree). It lands in the Master's Inbox as the approval `Branch: F-n`; commit requests wait for
    it. The daemon fills in the current branch, the other live worktrees and the warnings, and validates the choice
    (a bad name, an existing branch, an unknown base are a 400 with a plain reason)."""
    db = proj.db
    feat = resolve_feature(db, feature)
    if feat is None:
        raise HTTPException(404, "unknown feature")
    mode = (mode or "new").strip()
    row = db.feature_branch(feat["id"])
    if row:
        raise HTTPException(409, f"{feat['key']} already works on {row['branch']}")
    if any(a["state"] == "pending" and is_branch_title(a["title"]) for a in db.approvals("pending", feat["id"])):
        raise HTTPException(409, f"a Branch card for {feat['key']} is already waiting for the Master; withdraw it to change it")
    repo = proj.path
    if mode == "new":
        branch = (branch or "").strip() or branches.default_branch_name(feat)
        base = (base or "").strip() or branches.default_base(repo)
    res = await asyncio.to_thread(branches.check, repo, db, mode, branch, base, shared_from, True)  # fetches origin first
    if res["errors"]:
        raise HTTPException(400, "; ".join(res["errors"]))
    cur = await asyncio.to_thread(branches.current_branch, repo)
    payload = {"mode": mode, "branch": branch if mode == "new" else None, "base": base if mode == "new" else None,
               "shared_from": shared_from if mode == "shared" else None, "why": (why or "").strip(), "current": cur,
               "worktrees": branches.live_trees(db, feat["id"]), "warnings": res["warnings"],
               "tree": branches.planned_tree(repo, db, feat, mode, shared_from)}
    what = {"new": f"a new branch {branch} from {base}", "current": f"the current branch ({cur})",
            "shared": f"{shared_from}'s worktree"}[mode]
    summary = f"Proposed: work on {feat['key']} in {what}." + (f"\n\n{payload['why']}" if payload["why"] else "")
    return await request_approval(st, proj, role, turn_id, feat["key"], f"Branch: {feat['key']}", summary, payload)


async def request_pr(st, proj: Project, role: str, turn_id: int | None, feature, destination: str | None, title: str,
                     body: str = "") -> dict:
    """F-39 B3: the feature's Lead asks the Master to open a pull request for the feature's branch. It lands in the Inbox as
    the approval `PR: <title>` (payload {source, destination, title, body, commits}); approving pushes the branch to origin and
    opens the PR on GitHub (`decide_approval`). Refused (400) without a confirmed branch, when the destination is the branch
    itself or unknown, or when the branch has no commits the destination lacks; refused (409) when the feature already has a
    PR open or merged, or a PR card is waiting. Sent after QA passes: that is process, not enforced."""
    db = proj.db
    feat = resolve_feature(db, feature)
    if feat is None:
        raise HTTPException(404, "unknown feature")
    row = db.feature_branch(feat["id"])
    if row is None:
        raise HTTPException(400, f"{feat['key']} has no confirmed branch yet; send a Branch card (propose_branch) first")
    if row["pr_state"] in ("open", "merged"):
        raise HTTPException(409, f"{feat['key']} already has a pull request ({row['pr_state']}): {row['pr_url']}")
    if any(is_pr_title(a["title"]) for a in db.approvals("pending", feat["id"])):
        raise HTTPException(409, f"a PR card for {feat['key']} is already waiting for the Master; withdraw it to change it")
    title = " ".join((title or "").split())
    if not title:
        raise HTTPException(400, "title required")
    await asyncio.to_thread(branches.fetch_origin, branches.work_dir(proj, feat["id"]))  # best effort: compare with origin as it is now
    try:
        pv = await asyncio.to_thread(branches.pr_preview, proj, feat, destination)
    except branches.BranchError as e:
        raise HTTPException(400, str(e))
    if pv["errors"]:
        raise HTTPException(400, "; ".join(pv["errors"]))
    body = (body or "").strip()
    payload = {"source": pv["source"], "destination": pv["destination"], "title": title, "body": body,
               "commits": pv["commits"]}
    n = len(pv["commits"])
    summary = (f"Open a pull request on GitHub: {pv['source']} → {pv['destination']} ({n} commit{'s' if n != 1 else ''})."
               + (f"\n\n{body}" if body else ""))
    return await request_approval(st, proj, role, turn_id, feat["key"], f"PR: {title}", summary, payload)


async def decide_approval(st, proj: Project, aid: int, decision: str, note: str | None, set_phase: str | None,
                          payload: dict | None = None) -> dict:
    a = proj.db.approval(aid)
    if a is None:
        raise HTTPException(404, "unknown approval")
    if a["state"] != "pending":
        raise HTTPException(409, f"approval is {a['state']}")
    if decision not in ("approve", "changes", "reject"):
        raise HTTPException(400, "decision must be approve|changes|reject")
    if set_phase and set_phase not in PHASES:
        raise HTTPException(400, "bad set_phase")
    if payload is not None and not (is_branch_title(a["title"]) or is_pr_title(a["title"])):
        raise HTTPException(400, "a payload is only accepted on Branch: and PR: approvals")
    feat = proj.db.feature(a["feature_id"]) if a["feature_id"] else None
    if decision == "approve" and not set_phase and is_done_title(a["title"]):
        set_phase = "done"  # F-40: the card has no phase dropdown; an explicit set_phase (API, old clients) still wins
    applied = merged = None
    if decision == "approve" and is_branch_title(a["title"]) and feat:
        # F-39: create the worktree BEFORE resolving, so a failure (an existing path, a bad base) is a 409 to the Master
        # with git's message and the card stays pending
        merged = {**(a["payload"] or {}), **{k: v for k, v in (payload or {}).items() if k in branches.EDITABLE}}
        try:
            applied = await asyncio.to_thread(branches.apply_branch, proj, feat, merged)
        except branches.BranchError as e:
            raise HTTPException(409, str(e))
    pr = pr_merged = None
    if decision == "approve" and is_pr_title(a["title"]) and feat:
        # F-39 B3: push the branch and open the pull request BEFORE resolving, so a missing origin, gh or login is a 409 with
        # the reason and the card stays pending for a retry
        pr_merged = {**(a["payload"] or {}), **{k: v for k, v in (payload or {}).items() if k in branches.PR_EDITABLE}}
        try:
            pr = await asyncio.to_thread(branches.open_pr, proj, feat, pr_merged)
        except branches.BranchError as e:
            raise HTTPException(409, str(e))
    state = {"approve": "approved", "changes": "changes_requested", "reject": "rejected"}[decision]
    a = proj.db.resolve_approval(aid, state, note)
    extra = ""
    if decision == "approve" and set_phase and feat and set_phase != feat["phase"]:
        feat = proj.db.update_feature(feat["id"], phase=set_phase)
        await proj.hub.broadcast("feature", feat)  # its `design` is already the new one
        if set_phase == "done":
            await _broadcast_team(st, proj)  # F-41: its lane sessions just retired
        extra = f" (phase → {set_phase})"
    else:
        await _broadcast_design(proj, a)
    if applied:
        proj.db.set_approval_payload(aid, await asyncio.to_thread(branches.approved_payload, proj, feat, merged, applied))
        proj.db.set_feature_branch(feat["id"], **{k: v for k, v in applied.items() if k != "warnings"})
        await proj.hub.broadcast("feature", proj.db.feature(feat["id"]))
    if pr:
        proj.db.set_approval_payload(aid, branches.approved_pr_payload(pr_merged, pr))
        proj.db.set_feature_branch(feat["id"], pr_url=pr["url"], pr_state="open", pr_destination=pr["destination"])
        await proj.hub.broadcast("feature", proj.db.feature(feat["id"]))
    if decision == "approve":
        text = (f"Approved: {a['title']}{extra}" + (f"\n\n{note}" if note else "")
                + (f"\n\n{branches.lands_on(feat['key'], applied)}" if applied else "")
                + (f"\n\nPull request {'already open' if pr['reused'] else 'opened'}: {pr['url']} "
                   f"({pr['source']} → {pr['destination']})" if pr else ""))
    elif decision == "reject":  # F-25: "no, stop"; Changes requested means revise and ask again
        text = f"Rejected: {a['title']}" + (f"\n\n{note}" if note else "")
    else:
        text = f"Changes requested on {a['title']}: {note or '(no note)'}"
    conv = feat["conversation_id"] if feat else f"dm-{a['role']}"
    await _post(st, proj, conv, "master", f"@{a['role']} {text}", "Decision", [a["role"]])
    word = {"approve": "Approved", "changes": "Changes requested", "reject": "Rejected"}[decision]
    await _activity(proj, "approval", f"{word}: {a['title']}", a["role"], a["feature_id"], conv, f"approval:{aid}")
    await proj.hub.broadcast("approval", a)
    await broadcast_inbox(proj)
    return a


# ---- F-39 B3: pull requests after they are opened ------------------------------
PR_POLL_S = 600  # the poller looks at every open pull request this often (and once at startup)
log = logging.getLogger("aiteams.prs")


def _feature_open(db: ProjectDB, fid: int) -> bool:
    r = db.one("SELECT phase, archived FROM features WHERE id=?", fid)
    return r is not None and r["phase"] != "done" and not r["archived"]


async def _pr_merged(st, proj: Project, feat: dict, row: dict) -> None:
    """The PR was merged on GitHub: its worktree goes (never --force, never while another open feature still works in it), the
    feature leaves its lane, and the chat says what happened. The branch is never deleted (GitHub does that)."""
    db, tree, url = proj.db, row["tree"], row["pr_url"]
    db.set_feature_branch(feat["id"], pr_state="merged")
    lead, conv, note, dirty = feat.get("lead") or "lead", feat["conversation_id"], "", []
    if tree and row["tree_state"] == "live":
        sharers = [f"F-{r['feature_id']}" for r in db.feature_branches()
                   if r["tree"] == tree and r["feature_id"] != feat["id"] and r["tree_state"] == "live"
                   and _feature_open(db, r["feature_id"])]
        if sharers:
            note = f"The worktree {branches.tilde(tree)} stays: {', '.join(sharers)} still work in it."
        else:
            result, dirty = await asyncio.to_thread(branches.remove_tree, proj.path, tree)
            for r in db.feature_branches():  # every row on this tree: they share the one folder
                if r["tree"] == tree:
                    db.set_feature_branch(r["feature_id"], tree_state=result)
            note = (f"Worktree {branches.tilde(tree)} removed; the branch {row['branch']} stays (GitHub deletes it)."
                    if result == "removed" else f"Worktree {branches.tilde(tree)} kept: it has changes git won't delete.")
    await _post(st, proj, conv, "system", f"Pull request merged: {url}" + (f"\n\n{note}" if note else ""), "System", [])
    if dirty:
        await _post(st, proj, conv, "system",
                    f"{feat['key']}'s pull request merged, but its worktree {branches.tilde(tree)} was kept because git won't "
                    f"delete it:\n" + "\n".join(f"- `{x}`" for x in dirty[:20])
                    + f"\n\nCommit or discard these, then run `git worktree remove {tree}`.", "Finding", [lead])
    if branches.tree_for(db, feat["id"]) is None:  # no tree of its own any more: its lane session closes, wakes go to main
        gone = db.retire_lane(feat["key"])
        if gone["roles"] or gone["moved"]:
            await _broadcast_team(st, proj)
    await proj.hub.broadcast("feature", db.feature(feat["id"]))


async def refresh_pr(st, proj: Project, feat: dict) -> dict:
    """One look at the feature's open pull request on GitHub (the poller and the header's refresh button): merged → cleanup,
    closed → the Lead is told, open → nothing. Raises branches.BranchError when gh can't answer. Returns the branch row."""
    db = proj.db
    row = db.feature_branch(feat["id"])
    if not row or row["pr_state"] != "open" or not row["pr_url"]:
        return row or {}
    state = await asyncio.to_thread(branches.pr_remote_state, proj.path, row["pr_url"])
    row = db.feature_branch(feat["id"])
    if state == "OPEN" or row["pr_state"] != "open":  # or a second refresh already handled it
        return row
    if state == "MERGED":
        await _pr_merged(st, proj, feat, row)
    else:
        db.set_feature_branch(feat["id"], pr_state="closed")
        await _post(st, proj, feat["conversation_id"], "system",
                    f"Pull request closed without merging: {row['pr_url']}. The worktree stays.", "System",
                    [feat.get("lead") or "lead"])
        await proj.hub.broadcast("feature", db.feature(feat["id"]))
    return db.feature_branch(feat["id"])


async def poll_prs_once(st) -> None:
    for proj in list(st.projects.values()):
        if proj.schema_ahead:
            continue
        try:
            rows = [r for r in proj.db.feature_branches() if r["pr_state"] == "open" and r["pr_url"]]
        except Exception:
            log.exception("PR poll: could not read the feature branches of %s", proj.id)
            continue
        for r in rows:
            try:
                await refresh_pr(st, proj, proj.db.feature(r["feature_id"]))
            except Exception as e:  # a failed look is retried next round, never posted
                log.warning("PR poll: %s %s: %s", proj.id, r["pr_url"], e)


async def poll_prs(st, interval: float | None = None) -> None:
    """The daemon's own task (not the scheduler's): look at every open pull request once at startup, then every `interval`
    (PR_POLL_S, read at each round)."""
    while True:
        await poll_prs_once(st)
        await asyncio.sleep(PR_POLL_S if interval is None else interval)


# ---- commits -----------------------------------------------------------------
def work_root(proj: Project, feature_id: int | None) -> str:
    """F-39 B2: the folder a feature's request paths are resolved against, checked and committed in: its live worktree, else
    the project (no feature, `current` mode, an older feature). `project_path` below is that root wherever it is passed."""
    return branches.tree_for(proj.db, feature_id) or proj.path


def normalize_path(project_path: str, p: str, main: str | None = None) -> str:
    """`project_path` is the root the request resolves against (a feature's tree, else the project). `main` is the project
    folder: on a treed feature a path inside it is refused (the work lives in the tree)."""
    p = (p or "").strip()
    if not p:
        raise HTTPException(400, "empty path")
    full = p if os.path.isabs(p) else os.path.join(project_path, p)
    rp = os.path.realpath(full)
    root = os.path.realpath(project_path)
    if main is not None and os.path.realpath(main) != root:
        mp = os.path.realpath(main)
        if rp == mp or rp.startswith(mp + os.sep):
            rel = os.path.relpath(rp, mp)
            raise HTTPException(400, f"{p} is in the main folder, but this feature commits from its worktree {root}: list "
                                     f"{os.path.join(root, rel)} or {rel}")
    if not (rp == root or rp.startswith(root + os.sep)):
        # F-36: a file outside the project is committed in its own repo, so it is stored as its absolute realpath
        if ".git" in rp.split(os.sep):
            raise HTTPException(400, f"{p} is inside a .git directory and cannot be committed")
        if _repo_top(rp if os.path.isdir(rp) else os.path.dirname(rp)) is None:
            raise HTTPException(400, f"{p} is not inside a git repo; only files in a git repo can be committed")
        return rp
    rel = os.path.relpath(rp, root)
    if rel == ".aiteams" or rel.startswith(".aiteams" + os.sep):
        raise HTTPException(400, f"{p} is under .aiteams/ and cannot be committed")
    return rel


def _git(project_path: str, *args, check: bool = False) -> subprocess.CompletedProcess:
    # errors="replace": a diff of a non-UTF-8 file must not crash the endpoint
    return subprocess.run(["git", *args], cwd=project_path, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, check=check)


def _repo_top(directory: str) -> str | None:
    """F-36: realpath of the top level of the git work tree holding `directory` (or its nearest existing ancestor,
    for a file that was deleted), or None when it is not inside one."""
    d = directory
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    try:
        r = _git(d, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        return None
    top = r.stdout.strip()
    return os.path.realpath(top) if r.returncode == 0 and top else None


def _where(project_path: str, f: str) -> tuple[str, str]:
    """F-36: (the directory git runs in, the path as git sees it there) for a request file. A project-relative path
    runs in the project; an absolute one (outside the project) in the top level of its own repo."""
    if not os.path.isabs(f):
        return project_path, f
    top = _repo_top(os.path.dirname(f))
    return (top, os.path.relpath(f, top)) if top else (os.path.dirname(f), os.path.basename(f))


def _sha(project_path: str, rel: str) -> str | None:
    p = os.path.join(project_path, rel)
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _changed(root: str, c: dict) -> list[str]:
    """F-45: the files of a commit request whose content under `root` differs from the state in its `hashes` (taken at
    the Lead's approval, or at filing for a Lead's own request). A missing file is its own state (None)."""
    return [f for f in c["files"] if _sha(root, f) != c["hashes"].get(f)]


def _tracked(project_path: str, f: str) -> bool:
    repo, rel = _where(project_path, f)
    return _git(repo, "ls-files", "--error-unmatch", "--", rel).returncode == 0


def _ignored(project_path: str, f: str) -> bool:
    repo, rel = _where(project_path, f)
    return _git(repo, "check-ignore", "-q", "--", rel).returncode == 0


def _repo_groups(project_path: str, files: list[str]) -> list[tuple[str, list[str]]]:
    """F-36: [(repo top level, its files as git sees them there)]: the project's repo first (when a file of it is
    listed), the others sorted by path. A project-relative file belongs to the project's repo; an outside file to the
    repo that holds it, and when that is the project's own repo the two merge."""
    root = os.path.realpath(project_path)
    mine = _repo_top(root) or root
    groups: dict[str, list[str]] = {}
    for f in files:
        if os.path.isabs(f):
            repo, rel = _where(project_path, f)
        else:
            repo, rel = mine, os.path.relpath(os.path.join(root, f), mine)
        groups.setdefault(repo, [])
        if rel not in groups[repo]:
            groups[repo].append(rel)
    return sorted(groups.items(), key=lambda g: (g[0] != mine, g[0]))


UNFILED_SHOWN = 15


def _dirty_files(repo: str) -> set[str]:
    """Absolute realpaths of the files `git status` of `repo` reports (modified, deleted, new; renames by their new path)."""
    try:
        r = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all")
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode != 0:
        return set()
    top = _repo_top(repo) or repo
    out, parts = set(), iter(r.stdout.split("\0"))
    for entry in parts:
        if len(entry) < 4:
            continue
        out.add(os.path.realpath(os.path.join(top, entry[3:])))
        if entry[0] in "RC":  # the next field is the rename's old path
            next(parts, None)
    return out


def unfiled_edits(db: ProjectDB, project_path: str, role: str) -> list[str]:
    """F-42: the files `role` was the last to edit that are still dirty in git (modified, deleted or new) and are not in an
    open commit request: work a rotated session would otherwise forget. Paths as the role knows them: project-relative, or
    absolute for a file in a feature's worktree. A request's files resolve against its feature's tree (F-39 B2). Gitignored
    files (docs, notes) are never dirty. One `git status` per repo involved; a git failure lists nothing."""
    listed: set[str] = set()
    for state in PENDING_COMMIT_STATES:
        for c in db.commits(state, limit=1000):
            base = branches.tree_for(db, c["feature_id"]) or project_path
            listed |= {os.path.realpath(f if os.path.isabs(f) else os.path.join(base, f)) for f in c["files"]}
    root = os.path.realpath(project_path)
    wt = branches.worktrees_root(project_path)
    repos: dict[str, set[str]] = {}  # the dirty files of each repo, read once
    out = []
    for p in db.file_edits_by(role):
        full = os.path.realpath(p if os.path.isabs(p) else os.path.join(project_path, p))
        if full in listed:
            continue
        if full == root or full.startswith(root + os.sep):
            repo = root
        elif full.startswith(wt + os.sep):  # <worktrees root>/f<n>/...: that feature's tree
            repo = os.path.join(wt, full[len(wt) + 1:].split(os.sep, 1)[0])
        else:
            repo = _repo_top(os.path.dirname(full)) or os.path.dirname(full)
        if repo not in repos:
            repos[repo] = _dirty_files(repo)
        if full in repos[repo]:
            out.append(p)
    return out


def unfiled_text(paths: list[str]) -> str:
    """The bullet list for `unfiled_edits` ("" when there are none); capped so it can't bloat a prompt."""
    if not paths:
        return ""
    lines = [f"- {p}" for p in paths[:UNFILED_SHOWN]]
    if len(paths) > UNFILED_SHOWN:
        lines.append(f"- … and {len(paths) - UNFILED_SHOWN} more")
    return "\n".join(lines)


def _diff_hint(project_path: str, files: list[str], root: str | None = None) -> str:
    """The Lead's review commands for a request: `git diff HEAD -- <project files>`, and one `git -C <repo> diff HEAD --
    <files>` for each other repo it touches (F-36: an outside file is listed by its absolute path). F-39 B2: `root` is
    the feature's worktree, whose files are diffed with `git -C <tree>` (the Lead's cwd is the project)."""
    pr = os.path.realpath(project_path)
    mine = _repo_top(pr) or pr
    parts = []
    for repo, paths in _repo_groups(root or project_path, files):
        parts.append(f"`git diff HEAD -- {' '.join(paths)}`" if repo == mine
                     else f"`git -C {repo} diff HEAD -- {' '.join(paths)}`")
    return " and ".join(parts)


def _commit_text(c: dict, feat: dict | None, hint: str | None = None) -> str:
    lines = [f"**Commit request #{c['id']}** ({len(c['files'])} file{'s' if len(c['files']) != 1 else ''})"]
    lines += [f"- {f}" for f in c["files"]]
    if c.get("requires"):
        lines.append("Requires " + ", ".join(f"#{n}" for n in c["requires"]))
    lines += ["", c["message"]]
    if c["state"] == "lead_review":
        lines += ["", f"@lead please review: {hint or '`git diff HEAD -- ' + ' '.join(c['files']) + '`'}, then "
                      f"`review_commit({c['id']}, 'approve'|'reject', note)`."]
    else:
        lines += ["", "Waiting on the Master."]
    return "\n".join(lines)


async def request_commit(st, proj: Project, role: str, turn_id: int | None, feature, files: list[str], message: str,
                         requires: list[int] | None = None) -> dict:
    if not files:
        raise HTTPException(400, "list at least one file")
    message = (message or "").strip()
    if not message:
        raise HTTPException(400, "commit message required")
    feat = resolve_feature(proj.db, feature)
    if feat:  # F-21: no code commits on a feature until the Master has approved its design
        d = proj.db.design_info(feat["id"], feat["created_at"])
        if d["gated"] and d["state"] != "approved":
            raise HTTPException(409, f"{feat['key']}'s design isn't approved yet ({DESIGN_WORDS[d['state']]}). The Lead "
                                     f"requests 'Design: {feat['title']}' with the design.md link; commit requests wait "
                                     "until the Master approves it.")
    if feat:  # F-39: nor before the Master has confirmed the feature's branch
        b = proj.db.branch_info(feat["id"], feat["created_at"])
        if b["gated"] and b["state"] != "approved":
            raise HTTPException(409, f"{feat['key']} has no confirmed branch yet; the Lead sends a Branch card "
                                     "(propose_branch) first")
    req_lane = ((proj.db.turn(turn_id) or {}).get("lane") or "") if turn_id else ""  # F-41: the filing turn's lane
    reqs: list[int] = []
    for n in requires or []:
        r = proj.db.commit(int(n)) if str(n).lstrip("#").isdigit() else None
        if r is None or _superseded(r) or r["state"] == "withdrawn":
            raise HTTPException(400, f"requires #{n}: no such open commit request"
                                     + (" (it was withdrawn)" if r is not None and r["state"] == "withdrawn"
                                        else f" (it was {r['note']})" if r is not None else ""))
        if r["id"] not in reqs:
            reqs.append(r["id"])
    root = work_root(proj, feat["id"] if feat else None)  # F-39 B2: paths are relative to the feature's worktree
    row = proj.db.feature_branch(feat["id"]) if feat else None
    if row and row["tree"] and row["tree_state"] == "live" and root == proj.path:
        raise HTTPException(409, f"{feat['key']} works in {row['tree']}, but that folder is gone; ask the Lead to sort out "
                                 "its worktree before filing")
    rels, hashes = [], {}
    for f in files:
        rel = await asyncio.to_thread(normalize_path, root, f, proj.path)  # F-36: an outside file asks its repo
        if rel in rels:
            continue
        if rel != "." and await asyncio.to_thread(_ignored, root, rel):
            raise HTTPException(400, f"{rel} is gitignored (build output is not committed)")
        full = os.path.join(root, rel)
        if rel == "." or os.path.isdir(full):
            raise HTTPException(400, f"{rel} is a directory; list files one by one")
        if not os.path.isfile(full) and not await asyncio.to_thread(_tracked, root, rel):
            raise HTTPException(400, f"{rel} does not exist and is not tracked")
        # F-36: no locks outside the project; F-39 B2: a worktree file's lock key is its absolute realpath (the hook's)
        lock_key = None if os.path.isabs(rel) else rel if root == proj.path else os.path.realpath(full)
        holder = proj.db.one("SELECT role, lane FROM locks WHERE path=?", lock_key) if lock_key else None
        if holder and (holder["role"], holder["lane"] or "") != (role, req_lane):  # F-41: another lane of the role is another holder
            raise HTTPException(409, f"{rel} is locked by {lanes.label(holder['role'], holder['lane'] or '')}; "
                                     "ask them or @lead to unlock it first")
        rels.append(rel)
        hashes[rel] = _sha(root, rel)
    state = "master_review" if role in LEADS else "lead_review"
    db = proj.db
    # a re-file supersedes the same role's pending requests in this feature whose files it covers, and (F-11) a
    # rejected one that open requests still require; it takes their place in the group
    olds = [o for st_ in PENDING_COMMIT_STATES for o in db.commits(st_, limit=1000)]
    required = {n for d in db.linked_commits() if d["state"] in PENDING_COMMIT_STATES for n in d["requires"]}
    olds += [o for o in db.commits("rejected", limit=1000) if o["id"] in required and not _superseded(o)]
    olds = [o for o in olds if o["role"] == role and o["feature_id"] == (feat["id"] if feat else None)
            and set(o["files"]) <= set(rels)]
    old_ids = {o["id"] for o in olds}
    for o in olds:  # inherit what the predecessor needed, except requests that were rejected (re-file them instead)
        for n in o["requires"]:
            r = db.commit(n)
            if n not in reqs and n not in old_ids and r and r["state"] not in ("rejected", "withdrawn"):
                reqs.append(n)
    reqs = [n for n in reqs if n not in old_ids]
    c = db.create_commit(feat["id"] if feat else None, role, turn_id, rels, message, hashes, state, reqs)
    conv = feat["conversation_id"] if feat else f"dm-{role}"
    mentions = ["lead"] if state == "lead_review" else []
    hint = await asyncio.to_thread(_diff_hint, proj.path, rels, root) if state == "lead_review" else None
    msg = await _post(st, proj, conv, role, _commit_text(c, feat, hint), "Commit", mentions, turn_id, f"commit:{c['id']}")
    c = db.update_commit(c["id"], message_id=msg["id"])
    await proj.hub.broadcast("commit", c)
    for o in olds:
        o = db.update_commit(o["id"], state="rejected", note=f"superseded by #{c['id']}")
        await _post(st, proj, conv, "system", f"Commit #{o['id']} superseded by #{c['id']}.", "System", [],
                    turn_id, ref=f"{COMMIT_EVENT}{o['id']}")
        await proj.hub.broadcast("commit", o)
    for d in db.linked_commits():  # dependents of a superseded request now require its successor
        if d["id"] != c["id"] and d["state"] in PENDING_COMMIT_STATES and old_ids & set(d["requires"]):
            new = []
            for n in d["requires"]:
                n = c["id"] if n in old_ids else n
                if n not in new:
                    new.append(n)
            await proj.hub.broadcast("commit", db.update_commit(d["id"], requires=json.dumps(new)))
    await broadcast_inbox(proj)
    await _recheck(st, proj, c["feature_id"])
    return c


# ---- F-11: commit groups ------------------------------------------------------------
_superseded = commit_graph.superseded


def commit_group(db: ProjectDB, cid: int) -> list[dict]:
    """The requests linked to `cid` through `requires` (either direction), rejected ones left out, in dependency
    order (a request after everything it requires; ties by id). A request with no links is a group of one."""
    start = db.commit(cid)
    return commit_graph.component(start, db.linked_commits(), db.commit) if start else []


def _blocked(db: ProjectDB, group: list[dict]) -> str | None:
    """F-11 (Lead #605): a member requiring a rejected, not superseded request can't land, alone or with its group."""
    hits = commit_graph.rejected_requirements(group, db.commit)
    if not hits:
        return None
    m, n = hits[0]
    return f"#{m['id']} requires #{n}, which was rejected: re-file #{n}, or re-file #{m['id']} without it"


def _open_others(group: list[dict], cid: int) -> list[dict]:
    return [m for m in group if m["id"] != cid and m["state"] != "committed"]


def inbox_groups(db: ProjectDB) -> list[dict]:
    """F-11: one Inbox item per open group of 2+ requests with at least one in master_review."""
    out, seen = [], set()
    for c in sorted(db.commits("master_review"), key=lambda c: c["id"]):
        if c["id"] in seen:
            continue
        g = commit_group(db, c["id"])
        seen.update(m["id"] for m in g)
        if len(g) < 2:
            continue
        members = [{**m, "feature": db.feature(m["feature_id"])["key"] if m["feature_id"] else None} for m in g]
        pending = [m for m in members if m["state"] != "committed"]
        blocked = _blocked(db, g)
        out.append({"id": min(m["id"] for m in g), "feature": next((m["feature"] for m in members if m["feature"]), None),
                    "members": members, "files": len({f for m in pending for f in m["files"]}),
                    "ready": not blocked and all(m["state"] == "master_review" for m in pending), "blocked": blocked})
    return out


def group_files(db: ProjectDB, cid: int) -> dict:
    """F-11 R2: the request's open group (or the request alone), in dependency order, for the review script."""
    c = db.commit(cid)
    if c is None:
        raise HTTPException(404, "unknown commit")
    g = [m for m in commit_group(db, cid) if m["state"] != "committed"] or [c]
    files = []
    for m in g:
        files += [f for f in m["files"] if f not in files]
    ids = [m["id"] for m in g]
    # F-39 B2: the worktree and branch the files live in, for the review script (None: the project's own folder, or the
    # group spans features that work in different places, which `mixed` says)
    where = {(branches.tree_for(db, m["feature_id"]) if m["feature_id"] else None,
              (db.feature_branch(m["feature_id"]) or {}).get("branch") if m["feature_id"] else None) for m in g}
    tree, branch = where.pop() if len(where) == 1 else (None, None)
    return {"ids": ids, "files": files, "overlap": group_overlap(db, set(ids), files), "tree": tree, "branch": branch,
            "mixed": len(where) > 0}


def group_overlap(db: ProjectDB, ids: set[int], files: list[str]) -> list[dict]:
    """F-22: the other open requests (Lead or Master review) that list a file of this request or group, one item per
    request, naming its feature and that feature's Lead, for review_tree.py's `overlap:` lines."""
    mine, out = set(files), []
    for state in PENDING_COMMIT_STATES:
        for o in db.commits(state):
            shared = [f for f in o["files"] if f in mine]
            if o["id"] in ids or not shared:
                continue
            feat = db.feature(o["feature_id"]) if o["feature_id"] else None
            out.append({"commit_id": o["id"], "role": o["role"], "state": o["state"], "files": shared,
                        "feature": feat["key"] if feat else None,
                        "lead": feat["lead"] if feat else None,
                        "lead_name": role_name(feat["lead"]) if feat and feat["lead"] else None})
    return sorted(out, key=lambda x: x["commit_id"])


async def review_commit(st, proj: Project, role: str, cid: int, decision: str, note: str | None,
                        turn_id: int | None = None) -> dict:
    if role not in LEADS:
        raise HTTPException(403, "only a Lead reviews commits")
    c = proj.db.commit(cid)
    if c is None:
        raise HTTPException(404, "unknown commit")
    if c["state"] != "lead_review":
        raise HTTPException(409, f"commit #{cid} is {c['state']}")
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve|reject")
    feat = proj.db.feature(c["feature_id"]) if c["feature_id"] else None
    conv = feat["conversation_id"] if feat else f"dm-{c['role']}"
    if decision == "approve":
        # F-45: the Master may commit only what the Lead approved, so the files' state now replaces the filing-time hashes
        root, _ = _commit_ctx(proj, c)
        hashes = {f: _sha(root, f) for f in c["files"]}
        c = proj.db.update_commit(cid, state="master_review", note=note or None, hashes=json.dumps(hashes))
        await _post(st, proj, conv, "system", f"{role_name(role)} approved commit #{cid}; waiting on the Master.", "System", [],
                    turn_id, ref=f"{COMMIT_EVENT}{cid}")
    else:
        c = proj.db.update_commit(cid, state="rejected", note=note or None)
        await _post(st, proj, conv, role, f"@{c['role']} Commit #{cid} rejected: {note or '(no note)'}", "Decision", [c["role"]])
        await _activity(proj, "commit", f"Commit #{cid} rejected by {role_name(role)}", c["role"], c["feature_id"], conv,
                        f"commit:{cid}")
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
    await _recheck(st, proj, c["feature_id"])
    return c


BUILT_PREFIX = "aiteams/static/assets/"
PENDING_COMMIT_STATES = ("lead_review", "master_review")


def _file_stats(text: str) -> tuple[str, bool, int, int]:
    """Status, binary flag and +/- line counts of one file's `git diff` text (counts only lines inside hunks)."""
    if not text:
        return "unchanged", False, 0, 0
    status, binary, adds, dels, in_hunk = "modified", False, 0, 0, False
    for line in text.split("\n"):  # not splitlines(): \x0c, \x85, U+2028 etc. are line content in a diff
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk:
            if line.startswith("+"):
                adds += 1
            elif line.startswith("-"):
                dels += 1
        elif line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "deleted"
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            binary = True
    return status, binary, adds, dels


def _diff_sync(project_path: str, c: dict) -> dict:
    files, parts = [], []
    for f in c["files"]:
        full = os.path.join(project_path, f)
        exists = os.path.isfile(full)
        repo, rel = _where(project_path, f)  # F-36: an outside file is diffed in its own repo
        tracked = _git(repo, "ls-files", "--error-unmatch", "--", rel).returncode == 0
        if tracked:
            text = _git(repo, "diff", "HEAD", "--", rel).stdout
        else:
            text = _git(repo, "diff", "--no-index", "--", os.devnull, rel).stdout if exists else ""
        parts.append(text)
        status, binary, adds, dels = _file_stats(text)
        if not tracked and not exists:
            status = "missing"
        files.append({"path": f, "status": status, "binary": binary, "additions": adds, "deletions": dels,
                      "size": os.path.getsize(full) if exists else None, "built": f.startswith(BUILT_PREFIX),
                      "text": text})
    stale = _changed(project_path, c)
    totals = {"files": len(files), "additions": sum(x["additions"] for x in files),
              "deletions": sum(x["deletions"] for x in files)}
    return {"diff": "".join(parts), "stale": stale, "files": files, "totals": totals}


def _overlap(db, c: dict) -> list[dict]:
    """Other pending requests (Lead or Master review) that list a file of `c`."""
    mine, out = set(c["files"]), []
    for state in PENDING_COMMIT_STATES:
        for o in db.commits(state):
            if o["id"] == c["id"]:
                continue
            feature = db.feature(o["feature_id"])["key"] if o["feature_id"] else None
            out += [{"path": f, "commit_id": o["id"], "role": o["role"], "state": o["state"], "feature": feature}
                    for f in o["files"] if f in mine]
    return sorted(out, key=lambda x: (c["files"].index(x["path"]), x["commit_id"]))


async def commit_diff(proj: Project, cid: int) -> dict:
    c = proj.db.commit(cid)
    if c is None:
        raise HTTPException(404, "unknown commit")
    out = await asyncio.to_thread(_diff_sync, work_root(proj, c["feature_id"]), c)
    feature = proj.db.feature(c["feature_id"])["key"] if c["feature_id"] else None
    return {**out, "commit": {**c, "feature": feature}, "overlap": _overlap(proj.db, c)}


def _head(repo: str) -> str:
    """The repo's HEAD commit, or "" while it has no commits yet."""
    r = _git(repo, "rev-parse", "--verify", "-q", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _rollback(repo: str, orig: str, files: list[str]) -> None:
    """Put a repo's HEAD and the index of `files` back where they were; the working tree is never touched. A repo that
    had no commits yet loses its first one (its branch ref is deleted)."""
    if orig:
        _git(repo, "reset", "-q", "--soft", orig)
        _git(repo, "reset", "-q", orig, "--", *files)
    else:
        _git(repo, "update-ref", "-d", "HEAD")
        _git(repo, "reset", "-q", "--", *files)


def _commit_repo(repo: str, files: list[str], message: str) -> tuple[bool, str]:
    add = _git(repo, "add", "--", *files)
    if add.returncode != 0:
        return False, add.stderr.strip() or "git add failed"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(message.rstrip() + "\n")
        path = f.name
    try:
        com = _git(repo, "commit", "-F", path, "--only", "--", *files)
    finally:
        os.unlink(path)
    if com.returncode != 0:
        return False, (com.stderr or com.stdout).strip() or "git commit failed"
    return True, _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


def _branch_problem(root: str, expect: tuple[str, str, str] | None) -> str | None:
    """F-39 B2: before committing, the folder the work lives in must still be on the feature's recorded branch (a role that
    ran `git checkout` in the tree, or a main folder moved off a `current` branch). `expect` = (feature key, branch, label)."""
    if not expect:
        return None
    key, branch, label = expect
    cur = branches.current_branch(root)
    return None if cur == branch else f"{label} is on {cur or 'a detached HEAD'}, {key}'s branch is {branch}"


def _commit_ctx(proj: Project, c: dict) -> tuple[str, tuple[str, str, str] | None]:
    """F-39 B2: (the root a request's paths live in, what its feature's branch must be) for a commit request. A feature
    with an approved branch is checked in its worktree, or in the main folder for `current` mode; no feature or no branch
    row means no check and the project as the root."""
    root = work_root(proj, c["feature_id"])
    row = proj.db.feature_branch(c["feature_id"]) if c["feature_id"] else None
    if not row:
        return root, None
    label = "the main folder" if root == proj.path else "the tree"
    return root, (f"F-{c['feature_id']}", row["branch"], label)


def _commit_sync(project_path: str, files: list[str], message: str,
                 expect: tuple[str, str, str] | None = None) -> tuple[bool, str]:
    """F-36: one commit per repo the files live in, the project's repo first, with the same message. If any repo fails,
    every repo this call touched is rolled back, so nothing is left half-committed. Returns (ok, the shas as text: the
    project's `a1b2c3d`, then `<repo dir>@<sha>` for each other repo; or the failure output). `project_path` is the
    root the files resolve against (F-39 B2: the feature's worktree), checked against `expect` first."""
    bad = _branch_problem(project_path, expect)
    if bad:
        return False, bad
    groups = _repo_groups(project_path, files)
    snaps = {repo: _head(repo) for repo, _ in groups}
    mine = _repo_top(project_path) or os.path.realpath(project_path)
    shas = []
    for repo, paths in groups:
        ok, out = _commit_repo(repo, paths, message)
        if not ok:
            for r, p in groups:  # the repos done before this one, and this one's staged files
                _rollback(r, snaps[r], p)
            return False, out
        shas.append(out if repo == mine else f"{os.path.basename(repo)}@{out}")
    return True, " ".join(shas)


# F-20 (Master's card #16): a successful commit wakes only the Lead (in a feature: that feature's Lead, F-22), who
# briefs the next step; the filer sees its
# "Committed" line in its next turn's news. Rejections and failures still wake the filer too (_decision_mentions).
COMMITTED_MENTIONS = ["lead"]


def _decision_mentions(requester: str) -> list[str]:
    """F-14: the Master's commit decisions wake the requester and the Lead, who sequences the next step."""
    return [requester] if requester in LEADS else [requester, "lead"]  # in a feature, "lead" means its Lead


async def _send_back(st, proj: Project, members: list[dict], stale: dict[str, list[str]]) -> dict[str, list[str]]:
    """F-45: files changed after the Lead approved a request, so nothing is committed. Every member still waiting on the
    Master (the whole commit group; a single request is a group of one) goes back to lead_review with the changed paths
    in its note, leaves the Inbox, and the Lead and each requester are told, once per conversation. `stale` maps a member's
    id (as text) to its changed paths; it is returned as the 409's `sent_back`. No git write happens here.
    The line is the Master's `Decision` like the other commit decisions, because a System line wakes nobody."""
    db = proj.db
    back = [m for m in members if m["state"] == "master_review"]
    for m in back:
        own = stale.get(str(m["id"]))
        what = ", ".join(own) if own else "; ".join(f"#{i} {', '.join(p)}" for i, p in stale.items()) + " (same group)"
        db.update_commit(m["id"], state="lead_review", note=f"changed after review: {what}")
    by_conv: dict[str, list[dict]] = {}
    for m in back:
        feat = db.feature(m["feature_id"]) if m["feature_id"] else None
        by_conv.setdefault(feat["conversation_id"] if feat else f"dm-{m['role']}", []).append(m)
    changed = ", ".join(f"`{p}`" for p in dict.fromkeys(p for ps in stale.values() for p in ps))
    group = " (group: " + ", ".join(f"#{m['id']}" for m in back) + ")" if len(back) > 1 else ""
    for conv, ms in by_conv.items():
        who: list[str] = []
        for m in ms:
            who += [r for r in _decision_mentions(m["role"]) if r not in who]
        ids = ", ".join(f"#{m['id']}" for m in ms)
        await _post(st, proj, conv, "master", " ".join("@" + r for r in who)
                    + f" Commit {ids} went back to Lead review: {changed} changed after approval.{group}", "Decision", who)
    for m in back:
        await proj.hub.broadcast("commit", db.commit(m["id"]))
    await broadcast_inbox(proj)
    for fid in {m["feature_id"] for m in back}:
        await _recheck(st, proj, fid)
    return stale


async def decide_commit(st, proj: Project, cid: int, decision: str, message: str | None, note: str | None,
                        group: bool = False) -> dict:
    c = proj.db.commit(cid)
    if c is None:
        raise HTTPException(404, "unknown commit")
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve|reject")
    if decision == "approve":
        g = commit_group(proj.db, cid)
        why = _blocked(proj.db, g)  # it, or a member of its group, requires a rejected request
        if why:
            raise HTTPException(409, why)
        others = _open_others(g, cid)
        if group and others:  # a group of one (or the rest committed) takes the single path below
            return await _commit_group(st, proj, [m for m in g if m["state"] != "committed"], note)
        if others:
            o = others[0]
            raise HTTPException(409, f"#{cid} is part of a commit group with #{o['id']} ({o['state']}): "
                                     "commit the group from the Inbox")
    if c["state"] != "master_review":
        raise HTTPException(409, f"commit #{cid} is {c['state']}")
    feat = proj.db.feature(c["feature_id"]) if c["feature_id"] else None
    conv = feat["conversation_id"] if feat else f"dm-{c['role']}"
    who = _decision_mentions(c["role"])
    at = " ".join("@" + r for r in who)
    if decision == "reject":
        c = proj.db.update_commit(cid, state="rejected", note=note or None)
        await _post(st, proj, conv, "master", f"{at} Commit #{cid} rejected: {note or '(no note)'}", "Decision", who)
        await _activity(proj, "commit", f"Commit #{cid} rejected by the Master", c["role"], c["feature_id"], conv, f"commit:{cid}")
    else:
        root, expect = _commit_ctx(proj, c)  # F-39 B2: the feature's worktree and branch
        stale = _changed(root, c)  # F-45: first thing, no await up to the commit; the Lead reviewed other content
        if stale:
            raise HTTPException(409, {"sent_back": await _send_back(st, proj, [c], {str(cid): stale})})
        msg_text = (message or "").strip() or c["message"]
        proj.committing.add(cid)  # F-34: no withdrawing while it is being written
        try:
            ok, out = await asyncio.to_thread(_commit_sync, root, c["files"], msg_text, expect)
        finally:
            proj.committing.discard(cid)
        if ok:
            c = proj.db.update_commit(cid, state="committed", sha=out, message=msg_text, note=note or None)
            subject = msg_text.splitlines()[0]
            on = f" on {expect[1]}" if expect else ""
            await _post(st, proj, conv, "master", f"@lead Committed `{out}`{on}: {subject}", "Decision", list(COMMITTED_MENTIONS))
            await _activity(proj, "commit", f"Committed {out}{on}: {subject}", c["role"], c["feature_id"], conv, f"commit:{cid}")
        else:
            c = proj.db.update_commit(cid, state="failed", note=out, message=msg_text)
            await _post(st, proj, conv, "master", f"{at} Commit #{cid} failed:\n```\n{out}\n```", "Decision", who)
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
    await _recheck(st, proj, c["feature_id"])
    return c


def _group_commit_sync(ctx: dict[int, tuple[str, tuple[str, str, str] | None]],
                       members: list[dict]) -> tuple[list[str], dict | None, str]:
    """Commit each member in order; on a failure roll HEAD and the index of every repo the group touches back to where
    it was before the first member (the working tree is never touched). F-36: members may span several repos, so one
    snapshot per repo is taken up front. F-39 B2: `ctx` maps a member's id to (its root, its branch check): members of
    different features resolve in their own worktrees, and every branch is checked before anything is committed.
    Returns (the sha text of each committed member, the failed member or None, git output)."""
    for m in members:
        bad = _branch_problem(*ctx[m["id"]])
        if bad:
            return [], m, bad
    snaps: dict[str, str] = {}
    touched: dict[str, list[str]] = {}
    for m in members:
        for repo, paths in _repo_groups(ctx[m["id"]][0], m["files"]):
            snaps.setdefault(repo, _head(repo))
            touched.setdefault(repo, [])
            touched[repo] += [p for p in paths if p not in touched[repo]]
    shas = []
    for m in members:
        ok, out = _commit_sync(ctx[m["id"]][0], m["files"], m["message"])
        if not ok:
            for repo, paths in touched.items():
                _rollback(repo, snaps[repo], paths)
            return [], m, out
        shas.append(out)
    return shas, None, ""


async def _commit_group(st, proj: Project, members: list[dict], note: str | None) -> dict:
    """F-11: commit every pending member of a group in dependency order, all or nothing. F-45: if any member's files
    changed after the Lead approved it, nothing is committed and the whole group goes back to Lead review."""
    db = proj.db
    bad = [m for m in members if m["state"] != "master_review"]
    if bad:
        raise HTTPException(409, {"group": [{"id": m["id"], "state": m["state"]} for m in members],
                                  "detail": f"#{bad[0]['id']} is {bad[0]['state']}"})
    ctx = {m["id"]: _commit_ctx(proj, m) for m in members}  # F-39 B2: each member's worktree and branch
    stale = {str(m["id"]): s for m in members if (s := _changed(ctx[m["id"]][0], m))}
    if stale:
        raise HTTPException(409, {"sent_back": await _send_back(st, proj, members, stale)})
    proj.committing.update(m["id"] for m in members)  # F-34: no withdrawing while they are being written
    try:
        shas, failed, out = await asyncio.to_thread(_group_commit_sync, ctx, members)
    finally:
        proj.committing.difference_update(m["id"] for m in members)
    label = " + ".join(f"#{m['id']}" for m in members)
    first = members[0]
    feat = db.feature(first["feature_id"]) if first["feature_id"] else None
    conv = feat["conversation_id"] if feat else f"dm-{first['role']}"
    if failed:
        db.update_commit(failed["id"], state="failed", note=out)
        who = []
        for m in members:
            who += [r for r in _decision_mentions(m["role"]) if r not in who]
        await _post(st, proj, conv, "master", " ".join("@" + r for r in who)
                    + f" Commit group {label} failed at #{failed['id']}; nothing was committed:\n```\n{out}\n```",
                    "Decision", who)
    else:
        for m, sha in zip(members, shas):
            m = db.update_commit(m["id"], state="committed", sha=sha, note=note or None)
            mconv = db.feature(m["feature_id"])["conversation_id"] if m["feature_id"] else f"dm-{m['role']}"
            subject = m["message"].splitlines()[0]
            expect = ctx[m["id"]][1]
            on = f" on {expect[1]}" if expect else ""
            await _post(st, proj, mconv, "master", f"@lead Committed `{sha}`{on}: {subject}", "Decision", list(COMMITTED_MENTIONS))
            await _activity(proj, "commit", f"Committed {sha}{on}: {subject}", m["role"], m["feature_id"], mconv,
                            f"commit:{m['id']}")
        await _post(st, proj, conv, "system", f"Group {label} committed.", "System", [],
                    ref=f"{COMMIT_EVENT}{first['id']}")
    for m in members:
        await proj.hub.broadcast("commit", db.commit(m["id"]))
    await broadcast_inbox(proj)
    for fid in {m["feature_id"] for m in members}:
        await _recheck(st, proj, fid)
    return {**db.commit(first["id"]), "group": [db.commit(m["id"]) for m in members]}


# ---- locks ---------------------------------------------------------------------
def _lock_dict(l: dict) -> dict:
    return {"path": l["path"], "role": l["role"], "turn_id": l.get("turn_id"), "created_at": l["created_at"]}


async def broadcast_locks(proj: Project) -> None:
    await proj.hub.broadcast("locks", {"locks": proj.db.locks()})


async def acquire_lock(proj: Project, role: str, turn_id: int | None, path: str, lane: str = "") -> dict:
    rel = _rel(proj.path, path)  # docs under .aiteams/features/ are lockable too
    holder = proj.db.acquire_lock(rel, role, turn_id, lane)  # F-41: the holder is a role AND a lane
    if holder:
        hl = holder.get("lane") or ""
        return {"ok": False, "path": rel, "holder": lanes.label(holder["role"], hl), "holder_role": holder["role"],
                "holder_lane": hl, "since": holder["created_at"]}
    proj.db.touch_edit(rel, role)
    await broadcast_locks(proj)
    return {"ok": True, "path": rel}


def _rel(project_path: str, p: str) -> str:
    """A lock's key: a project file's path relative to the project; F-39 B2: a file in a feature's worktree (under
    AITeams-worktrees/<project>) is keyed by its absolute realpath, so two features' trees never collide."""
    full = p if os.path.isabs(p) else os.path.join(project_path, p)
    rp = os.path.realpath(full)
    root = os.path.realpath(project_path)
    wt = branches.worktrees_root(project_path)
    if rp.startswith(wt + os.sep):
        return rp
    if not (rp == root or rp.startswith(root + os.sep)):
        raise HTTPException(400, f"{p} is outside the project")
    return os.path.relpath(rp, root)


async def release_lock(st, proj: Project, role: str, path: str, force: bool, lane: str = "") -> dict:
    rel = _rel(proj.path, path)
    holder = proj.db.one("SELECT * FROM locks WHERE path=?", rel)
    if holder is None:
        return {"ok": True, "path": rel, "released": False}
    holder = dict(holder)
    if holder["role"] == role and (holder["lane"] or "") == lane:
        proj.db.release_lock(rel)
    elif force and role in (*LEADS, "manager"):
        proj.db.release_lock(rel)
        when = time.strftime("%H:%M", time.localtime(holder["created_at"]))
        await _post(st, proj, f"dm-{holder['role']}", role,
                    f"I force-unlocked `{rel}` (it was yours since {when}).", "Message", [holder["role"]])
    else:
        raise HTTPException(403, f"{rel} is locked by {lanes.label(holder['role'], holder['lane'] or '')}; "
                                 "only a Lead or the manager may force-unlock it")
    await broadcast_locks(proj)
    return {"ok": True, "path": rel, "released": True}


async def master_release_lock(st, proj: Project, path: str) -> dict:
    rel = _rel(proj.path, path)
    holder = proj.db.release_lock(rel)
    if holder:
        await _post(st, proj, f"dm-{holder['role']}", "system", f"The Master released your lock on `{rel}`.", "System", [])
        await broadcast_locks(proj)
    return {"ok": True, "path": rel, "released": bool(holder)}


# ---- F-16: messages that arrive during a turn ---------------------------------------
# A commit System line ("superseded", "Lead approved", "Group … committed") carries ref commit_event:<id>. It isn't
# `commit:<id>`, because the UI renders a full commit card for every message with a commit: ref.
COMMIT_EVENT = "commit_event:"
NEWS_CAP, NEWS_CHARS = 10, 600


def _commit_ref(m: dict) -> int | None:
    ref = m.get("ref") or ""
    for prefix in (COMMIT_EVENT, "commit:"):
        if ref.startswith(prefix) and ref[len(prefix):].isdigit():
            return int(ref[len(prefix):])
    return None


def _news(db: ProjectDB, role: str, after: int, turn_id: int) -> tuple[list[dict], list[dict], int]:
    """(relevant, others, highest id seen) among messages newer than `after` that the role didn't write and that
    its own turn's actions didn't post (a "superseded" or "Lead approved" System line carries the caller's turn)."""
    rows = db.q("SELECT id, conversation_id FROM messages WHERE id>? ORDER BY id", after)
    if not rows:
        return [], [], after
    top = rows[-1]["id"]
    msgs = [m for m in db.messages_by_ids([r["id"] for r in rows])
            if m["author"] != role and m.get("turn_id") != turn_id and m["id"] > db.reset_after(m["conversation_id"])
            and (m["author"] != "system" or _commit_ref(m) is not None)]
    voiced: dict[str, bool] = {}
    relevant, others = [], []
    for m in msgs:
        cid = m["conversation_id"]
        if cid not in voiced:
            conv = db.conversation(cid) or {}
            feat = db.feature(cid) if conv.get("kind") == "feature" else None
            voiced[cid] = bool(feat and role in voices(db, feat))
        n = _commit_ref(m) if m["author"] == "system" else None
        c = db.commit(n) if n is not None else None
        # F-22: a Lead follows the commit events of its own features and of featureless requests
        cfeat = db.feature(c["feature_id"]) if c is not None and c["feature_id"] else None
        mine = c is not None and (c["role"] == role or (role in LEADS and (cfeat is None or cfeat["lead"] in (None, role))))
        if role in m["mentions"] or cid == f"dm-{role}" or voiced[cid] or mine:
            relevant.append(m)
        else:
            others.append(m)
    return relevant, others, top


def _news_text(relevant: list[dict], others: list[dict], head: str) -> str:
    shown, extra = relevant[-NEWS_CAP:], others + relevant[:-NEWS_CAP]
    lines = ["---", head.format(n=len(relevant) + len(others))]
    for m in shown:
        text = m["text"] if len(m["text"]) <= NEWS_CHARS else m["text"][:NEWS_CHARS] + "…"
        at = " " + " ".join("@" + r for r in m["mentions"]) if m["mentions"] else ""
        lines.append(f"- [{m['conversation_id']}] #{m['id']} {m['author']} ({m['type']}){at}: "
                     + text.replace("\n", "\n  "))
    if extra:
        per: dict[str, int] = {}
        for m in extra:
            per[m["conversation_id"]] = per.get(m["conversation_id"], 0) + 1
        lines.append(f"{'and ' if shown else ''}{len(extra)} other message{'s' if len(extra) != 1 else ''} in "
                     + ", ".join(f"{k} ({v})" for k, v in sorted(per.items(), key=lambda kv: -kv[1])) + ".")
    return "\n".join(lines)


def _watermark(proj: Project, turn: dict) -> int:
    wm = proj.delivered.get(turn["id"])
    if wm is None:  # shouldn't happen (set when the prompt is built); never flood a turn with old messages
        wm = proj.delivered[turn["id"]] = proj.db.max_message_id()
    return wm


async def new_messages(proj: Project, turn: dict, shown_conversation: str | None = None) -> str:
    """F-16: the block of messages this turn hasn't been shown yet ("" if none); each is delivered once per turn.
    Messages in `shown_conversation` (a get_messages call just printed it) count as delivered without being listed."""
    from .orchestrator import receipts
    relevant, others, top = _news(proj.db, turn["role"], _watermark(proj, turn), turn["id"])
    proj.delivered[turn["id"]] = top
    if shown_conversation:
        relevant = [m for m in relevant if m["conversation_id"] != shown_conversation]
        others = [m for m in others if m["conversation_id"] != shown_conversation]
    if not relevant and not others:
        return ""
    await receipts.mark(proj.db, proj.hub, [m["id"] for m in relevant[-NEWS_CAP:]], turn["role"], "seen")
    return _news_text(relevant, others, "**{n} new messages since your turn started.** Read them before you act "
                                        "further.")


async def guard(proj: Project, turn: dict, conversations: set[str], commit_ids: set[int]) -> None:
    """F-16 refuse-once: if relevant messages the turn hasn't been shown touch the decision's conversation or
    requests, refuse with all new messages (409 {"guard": text}) and mark them delivered, so a retry passes."""
    relevant, others, top = _news(proj.db, turn["role"], _watermark(proj, turn), turn["id"])
    if not any(m["conversation_id"] in conversations or _commit_ref(m) in commit_ids for m in relevant):
        return
    proj.delivered[turn["id"]] = top
    from .orchestrator import receipts
    await receipts.mark(proj.db, proj.hub, [m["id"] for m in relevant[-NEWS_CAP:]], turn["role"], "seen")
    raise HTTPException(409, {"guard": _news_text(
        relevant, others, "**Not done: {n} new messages since your turn started.** Read these; if your action is "
                          "still right, call again.")})


def commit_targets(db: ProjectDB, cid: int) -> tuple[set[str], set[int]]:
    """The guard's targets for review_commit(N): N's conversation and N itself."""
    c = db.commit(cid)
    if c is None:
        return set(), set()
    feat = db.feature(c["feature_id"]) if c["feature_id"] else None
    return {feat["conversation_id"] if feat else f"dm-{c['role']}"}, {cid}


# ---- inbox ---------------------------------------------------------------------
def inbox(proj: Project) -> dict:
    db = proj.db
    questions = db.questions("pending")
    approvals = db.approvals("pending")
    groups = inbox_groups(db)  # F-11: a group of linked requests is one item; its members leave the flat list
    grouped = {m["id"] for g in groups for m in g["members"]}
    commits = [c for c in db.commits("master_review") if c["id"] not in grouped]
    msgs = db.inbox_messages()
    for a in approvals:
        a["feature"] = db.feature(a["feature_id"])["key"] if a["feature_id"] else None
    for c in commits:
        c["feature"] = db.feature(c["feature_id"])["key"] if c["feature_id"] else None
    return {"questions": questions, "approvals": approvals, "commits": commits, "groups": groups, "messages": msgs,
            "count": len(questions) + len(approvals) + len(commits) + len(groups) + len(msgs)}


async def broadcast_inbox(proj: Project) -> None:
    await proj.hub.broadcast("inbox", {"count": inbox(proj)["count"]})


# ---- M1c: team status, dashboard --------------------------------------------------
def _conv_short(db: ProjectDB, cid: str | None) -> dict | None:
    if not cid:
        return None
    conv = db.conversation(cid)
    if conv is None:
        return {"conversation_id": cid, "label": cid, "feature_key": None}
    feat = db.feature(cid) if conv["kind"] == "feature" else None
    label = feat["key"] if feat else ("#" + conv["title"] if conv["kind"] == "channel" else "DM " + conv["title"])
    return {"conversation_id": cid, "label": label, "feature_key": feat["key"] if feat else None}


def _lane_state(db: ProjectDB, running: dict, pid: str, role: str, lane: str) -> tuple[str, object]:
    """("running" | "queued" | "idle", the runner) of one (role, lane)."""
    runner = running.get((pid, role, lane))
    return ("running" if runner else ("queued" if any(db.queued_wake(role, k, lane) for k in WAKE_KINDS) else "idle")), runner


def _lane_meter(st, db: ProjectDB, role: str, lane: str) -> tuple[dict, list[dict], dict]:
    """(session info, recent turns, context meter) of one (role, lane). F-42: the meter reads 100% when the session rotates."""
    from .orchestrator.turn import context_window_from
    info = db.session_info(role, lane)
    turns = db.turns(role, limit=20, lane=lane)
    mine = [t for t in turns if t["session_id"] == info["session_id"]]
    ctx = next((t["context_tokens"] for t in mine if t.get("context_tokens")), 0) or 0
    window = next((context_window_from(json.loads(t["result_json"]), None)
                   for t in turns if t.get("result_json")), context_window_from(None))
    limit = rotation_limit(st.settings, window)
    return info, turns, {"context_tokens": ctx, "context_window": window, "context_limit": limit,
                         "context_pct": (ctx / limit) if limit else 0.0}


def _role_lanes(db: ProjectDB, running: dict, pid: str, role: str) -> list[str]:
    """F-41: the feature lanes worth a chip for the role: an active feature session, a running turn or a queued wake."""
    found = {ln for r, ln in db.active_lanes(role)}
    found |= {k[2] for k in running if k[:2] == (pid, role) and k[2]}
    found |= {w["lane"] for w in db.queued_wakes() if w["role"] == role and w.get("lane")}
    return sorted(found, key=lambda ln: (int(ln[2:]) if ln[2:].isdigit() else 0, ln))


def team_status(st, proj: Project) -> list[dict]:
    from . import stalls  # late import: stalls imports this module
    db = proj.db
    running = st.scheduler.running if st.scheduler else {}
    out = []
    for role in ROLES:
        info, turns, meter = _lane_meter(st, db, role, "")  # the role row is its MAIN session; feature lanes are in `lanes`
        last = turns[0] if turns else None
        state, runner = _lane_state(db, running, proj.id, role, "")
        model = model_for(st.settings, role)
        rs = db.role_state(role)
        lane_rows = []
        for ln in _role_lanes(db, running, proj.id, role):
            linfo, lturns, lmeter = _lane_meter(st, db, role, ln)
            lstate, lrunner = _lane_state(db, running, proj.id, role, ln)
            lrs = db.role_state(role, ln)
            lane_rows.append({"lane": ln, "state": lstate, "turn_id": (lrunner.turn["id"] if lrunner and lrunner.turn
                                                                         else (lturns[0]["id"] if lturns else None)),
                              "session_id": linfo["session_id"], "session_seq": linfo["seq"], **lmeter,
                              "rotation": lrs["rotate_reason"], "rotation_handoff_pending": bool(lrs["rotate_handoff"]),
                              "kind": (getattr(lrunner, "kind", None) if lrunner else None),
                              "stuck": stalls.is_stuck(st, proj, role, lane=ln)})
        out.append({"role": role, "state": state, "session_id": info["session_id"], "last_turn": last, **meter,
                    "lanes": lane_rows,
                    "cost_today": db.cost_today(role), "model": model, "model_label": model_label(model),
                    "effort": effort_for(st.settings, role),
                    "session_seq": info["seq"], "fresh_starts": info["fresh_starts"],
                    "rotation": rs["rotate_reason"], "rotation_handoff_pending": bool(rs["rotate_handoff"]),
                    "current": _conv_short(db, (last or {}).get("conversation_id")),
                    "kind": (getattr(runner, "kind", None) if runner else None),
                    "stuck": stalls.is_stuck(st, proj, role)})  # F-38: running or queued past stuck_turn_s
    return out


def _waits_on(st, proj: Project, feat: dict, running_convs: dict[str, str], queued_convs: dict[str, str]) -> dict | None:
    db, cid = proj.db, feat["conversation_id"]
    apps = db.approvals("pending", feat["id"])
    if apps:
        return {"kind": "approval", "who": "master", "since": apps[-1]["created_at"]}
    for q in db.questions("pending"):
        t = db.turn(q["turn_id"])
        if t and t.get("conversation_id") == cid:
            return {"kind": "question", "who": "master", "since": q["created_at"]}
    for c in db.commits("master_review", feat["id"]):
        return {"kind": "commit", "who": "master", "since": c["created_at"]}
    for c in db.commits("lead_review", feat["id"]):
        return {"kind": "review", "who": feat.get("lead") or "lead", "since": c["created_at"], "ref": f"commit:{c['id']}"}
    if cid in running_convs:
        return {"kind": "running", "who": running_convs[cid], "since": None}
    if cid in queued_convs:
        return {"kind": "queued", "who": queued_convs[cid], "since": None}
    # F-38: the newest NON-System message (System lines, our own nudges included, never wait on anyone). A mention waits
    # on the first role that isn't its author; a role's Handoff/Question/Blocker/Concern that mentions nobody waits on
    # the feature's owner, else the Manager ("unaddressed"). Master items were handled above and are never stalls.
    m = next((x for x in reversed(db.messages(cid, limit=30)) if x["author"] != "system"), None)
    if m:
        if "master" in m["mentions"]:
            return {"kind": "message", "who": "master", "since": m["created_at"], "ref": f"msg:{m['id']}"}
        roles = [r for r in m["mentions"] if r in ROLES and r != m["author"]]
        if roles:
            return {"kind": "message", "who": roles[0], "since": m["created_at"], "ref": f"msg:{m['id']}"}
        if m["author"] in ROLES and not m["mentions"] and m["type"] in ("Handoff", "Question", "Blocker", "Concern"):
            owner = feat.get("owner")
            who = owner if owner in ROLES and owner != m["author"] else "manager"
            if who != m["author"]:
                return {"kind": "unaddressed", "who": who, "since": m["created_at"], "ref": f"msg:{m['id']}"}
    return None


async def _recheck(st, proj: Project, feature_id: int | None) -> None:
    """F-38: after a post in a feature or a commit request's state change, drop a stall row whose wait has moved on."""
    from . import stalls  # late import: stalls imports this module
    await stalls.recheck(st, proj, feature_id)


def dashboard_features(st, proj: Project) -> list[dict]:
    db = proj.db
    running = st.scheduler.running if st.scheduler else {}
    running_convs = {r.wake["conversation_id"]: role for (pid, role, _), r in running.items() if pid == proj.id}
    queued_convs = {w["conversation_id"]: w["role"] for w in db.queued_wakes() if w["kind"] == "normal"}
    stale_s = float(st.settings.get("stale_hours", 4)) * 3600
    now = time.time()
    out = []
    for f in db.features():
        f["waits_on"] = _waits_on(st, proj, f, running_convs, queued_convs)
        f["stale"] = f["phase"] != "done" and not f["archived"] and (now - (f["last_activity"] or 0)) > stale_s
        out.append(f)
    return out


def feature_summary(project_path: str, key: str) -> str | None:
    """F-17: the text after `**Summary:**` on the first such line in the first 40 lines of the feature's prd.md."""
    try:
        with open(os.path.join(project_path, ".aiteams", "features", key, "prd.md"), encoding="utf-8",
                  errors="replace") as fh:
            for _, line in zip(range(40), fh):
                if line.startswith("**Summary:**"):
                    return line[len("**Summary:**"):].strip()[:200] or None
    except OSError:
        pass
    return None


def week_start(now: float | None = None) -> float:
    """Monday 00:00 local time of the week containing `now`."""
    t = time.localtime(now if now is not None else time.time())
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday - t.tm_wday, 0, 0, 0, 0, 0, -1))


SHIP_DAYS, SHIP_TIMELINE_CAP = 14, 100


def shipped(proj: Project, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    s = proj.db.shipped(week_start(now))
    summaries: dict[str, str | None] = {}

    def item(f: dict) -> dict:
        if f["key"] not in summaries:
            summaries[f["key"]] = feature_summary(proj.path, f["key"])
        return {"id": f["id"], "key": f["key"], "title": f["title"], "summary": summaries[f["key"]],
                "conversation_id": f["conversation_id"], "done_at": f["done_at"]}
    s["recent"] = [item(f) for f in s["recent"]]
    # F-18: everything done in the last 14 days, for the Dashboard's timeline
    s["timeline"] = [item(f) for f in proj.db.done_since(now - SHIP_DAYS * 86400, SHIP_TIMELINE_CAP)]
    return s


def hour_start(t: float) -> float:
    """The start of the local hour containing `t`."""
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1))


def activity_hourly(proj: Project, now: float | None = None) -> list[dict]:
    """F-18: 24 hourly buckets {start, messages, commits}, oldest first, the last one the current local hour.
    Messages exclude System lines; commits are committed requests, by their commit time (updated_at)."""
    first = hour_start(now if now is not None else time.time()) - 23 * 3600
    out = [{"start": first + i * 3600, "messages": 0, "commits": 0} for i in range(24)]
    for kind, times in proj.db.activity_times(first).items():
        for t in times:
            i = int((t - first) // 3600)
            if 0 <= i < 24:
                out[i][kind] += 1
    return out


def dashboard(st, proj: Project) -> dict:
    db = proj.db
    ib = inbox(proj)
    feats = dashboard_features(st, proj)
    counts = {p: 0 for p in PHASES}
    for f in feats:
        counts[f["phase"]] = counts.get(f["phase"], 0) + 1
    return {"needs_you": {"questions": len(ib["questions"]), "approvals": len(ib["approvals"]),
                          "commits": len(ib["commits"]), "messages": len(ib["messages"]), "total": ib["count"]},
            "phase_counts": counts, "features": feats, "shipped": shipped(proj), "activity_hourly": activity_hourly(proj),
            "team": team_status(st, proj),
            "activity": db.activity(30), "standup": db.current_standup(), "last_standup": db.last_standup()}


# ---- M1c: handoffs ------------------------------------------------------------------
def handoffs_dir(project_path: str, role: str) -> str:
    return os.path.join(project_path, ".aiteams", "handoffs", role)


async def write_handoff(st, proj: Project, role: str, turn: dict, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    db = proj.db
    lane = turn.get("lane") or ""  # F-41: the handoff belongs to the session the turn ran in
    rs = db.role_state(role, lane)  # outside a rotation the handoff is saved on the role's own initiative
    info = db.session_info(role, lane)
    d = handoffs_dir(proj.path, role)
    os.makedirs(d, exist_ok=True)
    n = len(db.handoffs(role, 10000, lane)) + 1
    path = os.path.join(d, f"{lane}-{n}.md" if lane else f"{n}.md")
    reason = rs["rotate_reason"] or "own initiative"
    last = db.turns(role, limit=1, lane=lane)
    pct = None
    if last and last[0].get("context_tokens"):
        from .orchestrator.turn import context_window_from
        res = json.loads(last[0]["result_json"]) if last[0].get("result_json") else None
        pct = last[0]["context_tokens"] / (context_window_from(res, None) or 1)
    header = (f"# Handoff — {role_name(role)}, session #{info['seq']}" + (f" ({lane})" if lane else "") + "\n"
              f"written {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime())}\nreason: {reason}\n"
              + (f"context: {pct:.0%}\n" if pct is not None else "") + "\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + text + "\n")
    rel = os.path.relpath(path, proj.path)
    msg = await _post(st, proj, f"dm-{role}", role, text, "Handoff", [], turn["id"])
    h = db.add_handoff(role, info["session_id"] or turn["session_id"], n, path, msg["id"], reason, pct, lane)
    await _activity(proj, "handoff", f"{lanes.label(role, lane)} wrote handoff #{n} ({reason})", role, None, f"dm-{role}",
                    f"handoff:{h['id']}")
    return {"id": h["id"], "path": rel}


# ---- M1c: standup -------------------------------------------------------------------
async def start_standup(st, proj: Project, requested_by: str) -> dict:
    db = proj.db
    if db.current_standup():
        raise HTTPException(409, "standup in progress")
    last = db.last_standup()
    since = last["created_at"] if last else 0.0
    roles = [r for r in ROLES if r != "manager" and db.turns_since(r, since)]
    who = "the Master" if requested_by == "master" else requested_by
    if not roles:
        when = time.strftime("%H:%M", time.localtime(since)) if since else "the start"
        await _post(st, proj, "standup", "system", f"Standup requested by {who}: nothing changed since {when}.", "System", [])
        return {"status": "nothing_changed", "since": since}
    su = db.create_standup(roles, requested_by)
    msg = await _post(st, proj, "standup", "system",
                      f"Standup #{su['id']} called by {who} — waiting for {', '.join(roles)}", "System", [])
    su = db.update_standup(su["id"], start_message_id=msg["id"])
    origin = "master" if requested_by == "master" else "role"
    for r in roles:
        db.add_wake(r, "standup", [], origin, kind="standup", ref=su["id"])
    await proj.hub.broadcast("standup", su)
    if st.scheduler:
        st.scheduler.notify()
    return {"status": "collecting", **su}


async def save_standup(st, proj: Project, role: str, turn: dict, text: str) -> dict:
    if role != "manager":
        raise HTTPException(403, "only the Manager saves the standup")
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    db = proj.db
    su = db.current_standup()
    if not su or su["state"] != "compiling":
        raise HTTPException(409, "no standup is being compiled")
    d = os.path.join(proj.path, ".aiteams", "standups")
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M")
    path = os.path.join(d, f"{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Standup #{su['id']} — {stamp}\n\n{text}\n")
    msg = await _post(st, proj, "standup", "manager", text, "Standup", ["master"], turn["id"])
    su = db.update_standup(su["id"], state="done", path=path, message_id=msg["id"], ended_at=time.time())
    await _activity(proj, "standup", f"Standup #{su['id']} report saved", "manager", None, "standup", f"standup:{su['id']}")
    await proj.hub.broadcast("standup", su)
    return {"id": su["id"], "path": os.path.relpath(path, proj.path)}


def standup_report(proj: Project, sid: int) -> dict:
    su = proj.db.standup(sid)
    if su is None:
        raise HTTPException(404, "unknown standup")
    text = ""
    if su.get("path") and os.path.isfile(su["path"]):
        with open(su["path"], encoding="utf-8", errors="replace") as f:
            text = f.read()
    return {**su, "text": text}


# ---- F-10: brainstorm round ------------------------------------------------------------
BRAINSTORM_STARTERS = ("product", "manager")


async def start_brainstorm(st, proj: Project, feat: dict, topic: str, requested_by: str) -> dict:
    """Wake every voice on the feature once (kind `brainstorm`) for one idea each; Product compiles them into a card."""
    db = proj.db
    if requested_by != "master" and requested_by not in BRAINSTORM_STARTERS:
        raise HTTPException(403, "only Product, the Manager or the Master start a brainstorm")
    topic = " ".join((topic or "").split())[:300]
    if not topic:
        raise HTTPException(400, "topic required")
    cur = db.active_brainstorm(feat["id"])
    if cur:
        raise HTTPException(409, f"brainstorm #{cur['id']} on {feat['key']} is still {cur['state']}")
    roles = voices(db, feat)
    bs = db.create_brainstorm(feat["id"], topic, roles, requested_by)
    who = "the Master" if requested_by == "master" else requested_by
    msg = await _post(st, proj, feat["conversation_id"], "system",
                      f"Brainstorm #{bs['id']} on {topic} (started by {who}) — waiting for {', '.join(roles)}",
                      "System", [], ref=f"brainstorm:{bs['id']}")
    bs = db.update_brainstorm(bs["id"], start_message_id=msg["id"])
    origin = "master" if requested_by == "master" else "role"
    for r in roles:
        db.add_wake(r, feat["conversation_id"], [], origin, kind="brainstorm", ref=bs["id"])
    await _activity(proj, "brainstorm", f"{feat['key']} brainstorm #{bs['id']}: {topic}",
                    None if requested_by == "master" else requested_by, feat["id"], feat["conversation_id"],
                    f"brainstorm:{bs['id']}")
    await proj.hub.broadcast("brainstorm", bs)
    if st.scheduler:
        st.scheduler.notify()
    return bs


def brainstorm_ideas(db: ProjectDB, bs: dict) -> list[dict]:
    """Messages posted by the round's brainstorm turns (one idea per role, in order)."""
    tids = {t["id"] for t in db.turns_for_wake_ref("brainstorm", bs["id"])}
    feat = db.feature(bs["feature_id"])
    return [m for m in db.messages(feat["conversation_id"], limit=200)
            if m["id"] > (bs.get("start_message_id") or 0) and m.get("turn_id") in tids]


# ---- M1c: feature fresh start --------------------------------------------------------
async def fresh_start_feature(st, proj: Project, feat: dict, mode: str) -> dict:
    if mode not in ("keep_docs", "start_over"):
        raise HTTPException(400, "mode must be keep_docs|start_over")
    db, cid = proj.db, feat["conversation_id"]
    first_master = next((m for m in db.messages(cid, limit=1000) if m["author"] == "master"), None)
    r = db.one("SELECT MAX(id) AS m FROM messages WHERE conversation_id=?", cid)
    db.set_reset_after(cid, int(r["m"] or 0))
    label = "keep docs" if mode == "keep_docs" else "start over"
    await _post(st, proj, cid, "system", f"Fresh start ({label}) by the Master", "System", [])
    await _activity(proj, "fresh_start", f"{feat['key']} fresh start ({label})", None, feat["id"], cid)
    if mode == "start_over":
        d = docs_dir(proj.path, feat)
        names = [n for n in (os.listdir(d) if os.path.isdir(d) else []) if n != "archive"]
        if names:
            arch = os.path.join(d, "archive", time.strftime("%Y-%m-%d-%H%M%S"))
            os.makedirs(arch, exist_ok=True)
            for n in names:
                shutil.move(os.path.join(d, n), os.path.join(arch, n))
        for a in db.approvals("pending", feat["id"]):
            await proj.hub.broadcast("approval", db.resolve_approval(a["id"], "changes_requested", "fresh start"))
        for c in db.commits("all", feat["id"]):
            if c["state"] in ("lead_review", "master_review"):
                await proj.hub.broadcast("commit", db.update_commit(c["id"], state="rejected", note="fresh start"))
        feat = db.update_feature(feat["id"], phase="intake", owner=None)
        if first_master:
            await _post(st, proj, cid, "master", first_master["text"], "Message", first_master["mentions"])
    for w in db.queued_wakes():
        if w["kind"] == "normal" and w["message_ids"] and all(db.reset_after(m["conversation_id"]) >= m["id"]
                                                            for m in db.messages_by_ids(w["message_ids"])):
            db.set_wake_state(w["id"], "done")
    await proj.hub.broadcast("conversation", db.conversation(cid))
    await proj.hub.broadcast("feature", db.feature(feat["id"]))
    await broadcast_inbox(proj)
    return db.feature(feat["id"])
