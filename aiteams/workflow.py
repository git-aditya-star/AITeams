"""Features, approvals, commit requests, locks and the Inbox: logic shared by the public and internal routers."""
from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import tempfile
import time

from fastapi import HTTPException

from .config import ROLES
from .db import PHASES, ProjectDB
from .runtime import Project

COMMIT_STATES = ("lead_review", "master_review", "rejected", "committed", "failed")


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


def docs_list(db: ProjectDB, project_path: str, feat: dict) -> list[dict]:
    d = docs_dir(project_path, feat)
    rel_prefix = os.path.join(".aiteams", "features", feat["key"]) + os.sep
    edits = db.file_edits_for(rel_prefix)
    out = []
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".md"))
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        try:
            stt = os.stat(p)
        except OSError:
            continue
        e = edits.get(rel_prefix + n)
        out.append({"name": n, "size": stt.st_size, "updated_at": e["updated_at"] if e else stt.st_mtime,
                    "editor": e["role"] if e else None})
    return out


def feature_header(db: ProjectDB, project_path: str, feat: dict, with_sizes: bool = False) -> str:
    lines = [f"# Feature {feat['key']}: {feat['title']}",
             f"- phase: {feat['phase']} · owner: {feat['owner'] or 'none'} · conversation id: {feat['conversation_id']}"
             + (" · ARCHIVED" if feat["archived"] else "")]
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
    locks = db.locks()
    if locks:
        lines.append("- locks now: " + " · ".join(f"{l['path']} ({l['role']})" for l in locks))
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
    if fields.get("title") and fields["title"].strip() != feat["title"]:
        changes["title"] = fields["title"].strip()[:200]
    if fields.get("archived") is not None and bool(fields["archived"]) != feat["archived"]:
        changes["archived"] = bool(fields["archived"])
    if not changes:
        return feat
    feat = proj.db.update_feature(feat["id"], **changes)
    who = "Master" if by == "master" else by
    lines = []
    for k, v in changes.items():
        if k == "archived":
            lines.append(f"{who} {'archived' if v else 'unarchived'} {feat['key']}")
        else:
            lines.append(f"{who} set {k} {v if v is not None else 'none'}")
    await _post(st, proj, feat["conversation_id"], "system", "\n".join(lines), "System", [])
    await proj.hub.broadcast("feature", feat)
    return feat


def resolve_feature(db: ProjectDB, ident) -> dict | None:
    if ident is None or str(ident).strip() == "":
        return None
    feat = db.feature(ident)
    if feat is None:
        raise HTTPException(404, f"unknown feature: {ident}")
    return feat


# ---- approvals ---------------------------------------------------------------
async def request_approval(st, proj: Project, role: str, turn_id: int | None, feature, title: str, summary: str) -> dict:
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    feat = resolve_feature(proj.db, feature)
    a = proj.db.create_approval(feat["id"] if feat else None, role, turn_id, title[:200], summary or "")
    conv = feat["conversation_id"] if feat else f"dm-{role}"
    text = f"**Approval requested: {title}**\n\n{summary or ''}".rstrip()
    msg = await _post(st, proj, conv, role, text, "Approval", ["master"], turn_id, f"approval:{a['id']}")
    proj.db.x("UPDATE approvals SET message_id=? WHERE id=?", msg["id"], a["id"])
    a = proj.db.approval(a["id"])
    await proj.hub.broadcast("approval", a)
    await broadcast_inbox(proj)
    return a


async def decide_approval(st, proj: Project, aid: int, decision: str, note: str | None, set_phase: str | None) -> dict:
    a = proj.db.approval(aid)
    if a is None:
        raise HTTPException(404, "unknown approval")
    if a["state"] != "pending":
        raise HTTPException(409, f"approval is {a['state']}")
    if decision not in ("approve", "changes"):
        raise HTTPException(400, "decision must be approve|changes")
    if set_phase and set_phase not in PHASES:
        raise HTTPException(400, "bad set_phase")
    state = "approved" if decision == "approve" else "changes_requested"
    a = proj.db.resolve_approval(aid, state, note)
    feat = proj.db.feature(a["feature_id"]) if a["feature_id"] else None
    extra = ""
    if decision == "approve" and set_phase and feat and set_phase != feat["phase"]:
        feat = proj.db.update_feature(feat["id"], phase=set_phase)
        await proj.hub.broadcast("feature", feat)
        extra = f" (phase → {set_phase})"
    if decision == "approve":
        text = f"Approved: {a['title']}{extra}" + (f"\n\n{note}" if note else "")
    else:
        text = f"Changes requested on {a['title']}: {note or '(no note)'}"
    conv = feat["conversation_id"] if feat else f"dm-{a['role']}"
    await _post(st, proj, conv, "master", f"@{a['role']} {text}", "Decision", [a["role"]])
    await proj.hub.broadcast("approval", a)
    await broadcast_inbox(proj)
    return a


# ---- commits -----------------------------------------------------------------
def normalize_path(project_path: str, p: str) -> str:
    p = (p or "").strip()
    if not p:
        raise HTTPException(400, "empty path")
    full = p if os.path.isabs(p) else os.path.join(project_path, p)
    rp = os.path.realpath(full)
    root = os.path.realpath(project_path)
    if not (rp == root or rp.startswith(root + os.sep)):
        raise HTTPException(400, f"{p} is outside the project")
    rel = os.path.relpath(rp, root)
    if rel == ".aiteams" or rel.startswith(".aiteams" + os.sep):
        raise HTTPException(400, f"{p} is under .aiteams/ and cannot be committed")
    return rel


def _git(project_path: str, *args, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project_path, capture_output=True, text=True, timeout=60, check=check)


def _sha(project_path: str, rel: str) -> str | None:
    p = os.path.join(project_path, rel)
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tracked(project_path: str, rel: str) -> bool:
    return _git(project_path, "ls-files", "--error-unmatch", "--", rel).returncode == 0


def _commit_text(c: dict, feat: dict | None) -> str:
    lines = [f"**Commit request #{c['id']}** ({len(c['files'])} file{'s' if len(c['files']) != 1 else ''})"]
    lines += [f"- {f}" for f in c["files"]]
    lines += ["", c["message"]]
    if c["state"] == "lead_review":
        lines += ["", f"@lead please review: `git diff HEAD -- {' '.join(c['files'])}`, then "
                      f"`review_commit({c['id']}, 'approve'|'reject', note)`."]
    else:
        lines += ["", "Waiting on the Master."]
    return "\n".join(lines)


async def request_commit(st, proj: Project, role: str, turn_id: int | None, feature, files: list[str], message: str) -> dict:
    if not files:
        raise HTTPException(400, "list at least one file")
    message = (message or "").strip()
    if not message:
        raise HTTPException(400, "commit message required")
    feat = resolve_feature(proj.db, feature)
    rels, hashes = [], {}
    for f in files:
        rel = normalize_path(proj.path, f)
        if rel in rels:
            continue
        full = os.path.join(proj.path, rel)
        if rel == "." or os.path.isdir(full):
            raise HTTPException(400, f"{rel} is a directory; list files one by one")
        if not os.path.isfile(full) and not await asyncio.to_thread(_tracked, proj.path, rel):
            raise HTTPException(400, f"{rel} does not exist and is not tracked")
        holder = proj.db.one("SELECT role FROM locks WHERE path=?", rel)
        if holder and holder["role"] != role:
            raise HTTPException(409, f"{rel} is locked by {holder['role']}; ask them or @lead to unlock it first")
        rels.append(rel)
        hashes[rel] = _sha(proj.path, rel)
    if feat and not (message.startswith(feat["key"] + ":") or message.startswith(feat["key"] + " ")):
        message = f"{feat['key']}: {message}"
    state = "master_review" if role == "lead" else "lead_review"
    c = proj.db.create_commit(feat["id"] if feat else None, role, turn_id, rels, message, hashes, state)
    conv = feat["conversation_id"] if feat else f"dm-{role}"
    mentions = ["lead"] if state == "lead_review" else []
    msg = await _post(st, proj, conv, role, _commit_text(c, feat), "Commit", mentions, turn_id, f"commit:{c['id']}")
    c = proj.db.update_commit(c["id"], message_id=msg["id"])
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
    return c


async def review_commit(st, proj: Project, role: str, cid: int, decision: str, note: str | None) -> dict:
    if role != "lead":
        raise HTTPException(403, "only the Lead reviews commits")
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
        c = proj.db.update_commit(cid, state="master_review", note=note or None)
        await _post(st, proj, conv, "system", f"Lead approved commit #{cid}; waiting on the Master.", "System", [])
    else:
        c = proj.db.update_commit(cid, state="rejected", note=note or None)
        await _post(st, proj, conv, "lead", f"@{c['role']} Commit #{cid} rejected: {note or '(no note)'}", "Decision", [c["role"]])
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
    return c


def _diff_sync(project_path: str, c: dict) -> dict:
    files = c["files"]
    tracked = [f for f in files if _tracked(project_path, f)]
    untracked = [f for f in files if f not in tracked]
    parts = []
    if tracked:
        parts.append(_git(project_path, "diff", "HEAD", "--", *tracked).stdout)
    for f in untracked:
        if os.path.isfile(os.path.join(project_path, f)):
            parts.append(_git(project_path, "diff", "--no-index", "--", os.devnull, f).stdout)
    stale = [f for f in files if _sha(project_path, f) != c["hashes"].get(f)]
    return {"diff": "".join(parts), "stale": stale}


async def commit_diff(proj: Project, cid: int) -> dict:
    c = proj.db.commit(cid)
    if c is None:
        raise HTTPException(404, "unknown commit")
    return await asyncio.to_thread(_diff_sync, proj.path, c)


def _commit_sync(project_path: str, files: list[str], message: str) -> tuple[bool, str]:
    add = _git(project_path, "add", "--", *files)
    if add.returncode != 0:
        return False, add.stderr.strip() or "git add failed"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(message.rstrip() + "\n")
        path = f.name
    try:
        com = _git(project_path, "commit", "-F", path, "--only", "--", *files)
    finally:
        os.unlink(path)
    if com.returncode != 0:
        return False, (com.stderr or com.stdout).strip() or "git commit failed"
    sha = _git(project_path, "rev-parse", "--short", "HEAD").stdout.strip()
    return True, sha


async def decide_commit(st, proj: Project, cid: int, decision: str, message: str | None, note: str | None,
                        force: bool) -> dict:
    c = proj.db.commit(cid)
    if c is None:
        raise HTTPException(404, "unknown commit")
    if c["state"] != "master_review":
        raise HTTPException(409, f"commit #{cid} is {c['state']}")
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve|reject")
    feat = proj.db.feature(c["feature_id"]) if c["feature_id"] else None
    conv = feat["conversation_id"] if feat else f"dm-{c['role']}"
    if decision == "reject":
        c = proj.db.update_commit(cid, state="rejected", note=note or None)
        await _post(st, proj, conv, "master", f"@{c['role']} Commit #{cid} rejected: {note or '(no note)'}", "Decision", [c["role"]])
    else:
        stale = [f for f in c["files"] if _sha(proj.path, f) != c["hashes"].get(f)]
        if stale and not force:
            raise HTTPException(409, {"stale": stale})
        msg_text = (message or "").strip() or c["message"]
        ok, out = await asyncio.to_thread(_commit_sync, proj.path, c["files"], msg_text)
        if ok:
            c = proj.db.update_commit(cid, state="committed", sha=out, message=msg_text, note=note or None)
            subject = msg_text.splitlines()[0]
            await _post(st, proj, conv, "master", f"@{c['role']} Committed `{out}`: {subject}", "Decision", [c["role"]])
        else:
            c = proj.db.update_commit(cid, state="failed", note=out, message=msg_text)
            await _post(st, proj, conv, "master", f"@{c['role']} Commit #{cid} failed:\n```\n{out}\n```", "Decision", [c["role"]])
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
    return c


# ---- locks ---------------------------------------------------------------------
def _lock_dict(l: dict) -> dict:
    return {"path": l["path"], "role": l["role"], "turn_id": l.get("turn_id"), "created_at": l["created_at"]}


async def broadcast_locks(proj: Project) -> None:
    await proj.hub.broadcast("locks", {"locks": proj.db.locks()})


async def acquire_lock(proj: Project, role: str, turn_id: int | None, path: str) -> dict:
    rel = _rel(proj.path, path)  # docs under .aiteams/features/ are lockable too
    holder = proj.db.acquire_lock(rel, role, turn_id)
    if holder:
        return {"ok": False, "path": rel, "holder": holder["role"], "since": holder["created_at"]}
    proj.db.touch_edit(rel, role)
    await broadcast_locks(proj)
    return {"ok": True, "path": rel}


def _rel(project_path: str, p: str) -> str:
    full = p if os.path.isabs(p) else os.path.join(project_path, p)
    rp = os.path.realpath(full)
    root = os.path.realpath(project_path)
    if not (rp == root or rp.startswith(root + os.sep)):
        raise HTTPException(400, f"{p} is outside the project")
    return os.path.relpath(rp, root)


async def release_lock(st, proj: Project, role: str, path: str, force: bool) -> dict:
    rel = _rel(proj.path, path)
    holder = proj.db.one("SELECT * FROM locks WHERE path=?", rel)
    if holder is None:
        return {"ok": True, "path": rel, "released": False}
    holder = dict(holder)
    if holder["role"] == role:
        proj.db.release_lock(rel)
    elif force and role in ("lead", "manager"):
        proj.db.release_lock(rel)
        when = time.strftime("%H:%M", time.localtime(holder["created_at"]))
        await _post(st, proj, f"dm-{holder['role']}", role,
                    f"I force-unlocked `{rel}` (it was yours since {when}).", "Message", [holder["role"]])
    else:
        raise HTTPException(403, f"{rel} is locked by {holder['role']}; only lead/manager may force-unlock it")
    await broadcast_locks(proj)
    return {"ok": True, "path": rel, "released": True}


async def master_release_lock(st, proj: Project, path: str) -> dict:
    rel = _rel(proj.path, path)
    holder = proj.db.release_lock(rel)
    if holder:
        await _post(st, proj, f"dm-{holder['role']}", "system", f"The Master released your lock on `{rel}`.", "System", [])
        await broadcast_locks(proj)
    return {"ok": True, "path": rel, "released": bool(holder)}


# ---- inbox ---------------------------------------------------------------------
def inbox(proj: Project) -> dict:
    db = proj.db
    questions = db.questions("pending")
    approvals = db.approvals("pending")
    commits = db.commits("master_review")
    msgs = db.inbox_messages()
    for a in approvals:
        a["feature"] = db.feature(a["feature_id"])["key"] if a["feature_id"] else None
    for c in commits:
        c["feature"] = db.feature(c["feature_id"])["key"] if c["feature_id"] else None
    return {"questions": questions, "approvals": approvals, "commits": commits, "messages": msgs,
            "count": len(questions) + len(approvals) + len(commits) + len(msgs)}


async def broadcast_inbox(proj: Project) -> None:
    await proj.hub.broadcast("inbox", {"count": inbox(proj)["count"]})
