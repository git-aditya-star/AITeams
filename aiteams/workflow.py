"""Features, approvals, commit requests, locks and the Inbox: logic shared by the public and internal routers."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

from fastapi import HTTPException

from .config import ROLES, effort_for, model_for, model_label
from .db import PHASES, ProjectDB
from .runtime import Project


def _activity(proj: Project, kind: str, text: str, role=None, feature_id=None, conversation_id=None, ref=None):
    row = proj.db.add_activity(kind, text, role, feature_id, conversation_id, ref)
    return proj.hub.broadcast("activity", row)

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
    if "phase" in changes:
        await _activity(proj, "phase", f"{feat['key']} phase → {changes['phase']} ({who})",
                        None if by == "master" else by, feat["id"], feat["conversation_id"])
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
    await _activity(proj, "approval", f"{'Approved' if decision == 'approve' else 'Changes requested'}: {a['title']}",
                    a["role"], a["feature_id"], conv, f"approval:{aid}")
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
    # errors="replace": a diff of a non-UTF-8 file must not crash the endpoint
    return subprocess.run(["git", *args], cwd=project_path, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, check=check)


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


def _ignored(project_path: str, rel: str) -> bool:
    return _git(project_path, "check-ignore", "-q", "--", rel).returncode == 0


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
        if rel != "." and await asyncio.to_thread(_ignored, proj.path, rel):
            raise HTTPException(400, f"{rel} is gitignored (build output is not committed)")
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
    # a re-file supersedes the same role's pending requests in this feature whose files it covers
    for state in PENDING_COMMIT_STATES:
        for o in proj.db.commits(state, limit=1000):
            if o["id"] != c["id"] and o["role"] == role and o["feature_id"] == c["feature_id"] and set(o["files"]) <= set(rels):
                o = proj.db.update_commit(o["id"], state="rejected", note=f"superseded by #{c['id']}")
                await _post(st, proj, conv, "system", f"Commit #{o['id']} superseded by #{c['id']}.", "System", [])
                await proj.hub.broadcast("commit", o)
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
        await _activity(proj, "commit", f"Commit #{cid} rejected by the Lead", c["role"], c["feature_id"], conv, f"commit:{cid}")
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
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
        tracked = _tracked(project_path, f)
        if tracked:
            text = _git(project_path, "diff", "HEAD", "--", f).stdout
        else:
            text = _git(project_path, "diff", "--no-index", "--", os.devnull, f).stdout if exists else ""
        parts.append(text)
        status, binary, adds, dels = _file_stats(text)
        if not tracked and not exists:
            status = "missing"
        files.append({"path": f, "status": status, "binary": binary, "additions": adds, "deletions": dels,
                      "size": os.path.getsize(full) if exists else None, "built": f.startswith(BUILT_PREFIX),
                      "text": text})
    stale = [f for f in c["files"] if _sha(project_path, f) != c["hashes"].get(f)]
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
    out = await asyncio.to_thread(_diff_sync, proj.path, c)
    feature = proj.db.feature(c["feature_id"])["key"] if c["feature_id"] else None
    return {**out, "commit": {**c, "feature": feature}, "overlap": _overlap(proj.db, c)}


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
        await _activity(proj, "commit", f"Commit #{cid} rejected by the Master", c["role"], c["feature_id"], conv, f"commit:{cid}")
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
            await _activity(proj, "commit", f"Committed {out}: {subject}", c["role"], c["feature_id"], conv, f"commit:{cid}")
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


def team_status(st, proj: Project) -> list[dict]:
    from .orchestrator.turn import context_window_from
    db = proj.db
    running = st.scheduler.running if st.scheduler else {}
    out = []
    for role in ROLES:
        info = db.session_info(role)
        turns = db.turns(role, limit=20)
        last = turns[0] if turns else None
        runner = running.get((proj.id, role))
        state = "running" if runner else ("queued" if (db.queued_wake(role) or db.queued_wake(role, "handoff")
                                                       or db.queued_wake(role, "standup") or db.queued_wake(role, "standup_compile")) else "idle")
        mine = [t for t in turns if t["session_id"] == info["session_id"]]
        ctx = next((t["context_tokens"] for t in mine if t.get("context_tokens")), 0) or 0
        model = model_for(st.settings, role)
        window = next((context_window_from(json.loads(t["result_json"]), None)
                       for t in turns if t.get("result_json")), context_window_from(None))
        rs = db.role_state(role)
        out.append({"role": role, "state": state, "session_id": info["session_id"], "last_turn": last,
                    "context_tokens": ctx, "context_window": window,
                    "context_pct": (ctx / window) if window else 0.0,
                    "cost_today": db.cost_today(role), "model": model, "model_label": model_label(model),
                    "effort": effort_for(st.settings, role),
                    "session_seq": info["seq"], "fresh_starts": info["fresh_starts"],
                    "rotation": rs["rotate_reason"], "rotation_handoff_pending": bool(rs["rotate_handoff"]),
                    "current": _conv_short(db, (last or {}).get("conversation_id")),
                    "kind": (getattr(runner, "kind", None) if runner else None)})
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
        return {"kind": "review", "who": "lead", "since": c["created_at"]}
    if cid in running_convs:
        return {"kind": "running", "who": running_convs[cid], "since": None}
    if cid in queued_convs:
        return {"kind": "queued", "who": queued_convs[cid], "since": None}
    last = db.messages(cid, limit=1)
    if last:
        m = last[0]
        if "master" in m["mentions"]:
            return {"kind": "message", "who": "master", "since": m["created_at"]}
        roles = [r for r in m["mentions"] if r in ROLES]
        if roles:
            return {"kind": "message", "who": roles[0], "since": m["created_at"]}
    return None


def dashboard_features(st, proj: Project) -> list[dict]:
    db = proj.db
    running = st.scheduler.running if st.scheduler else {}
    running_convs = {r.wake["conversation_id"]: role for (pid, role), r in running.items() if pid == proj.id}
    queued_convs = {w["conversation_id"]: w["role"] for w in db.queued_wakes() if w["kind"] == "normal"}
    stale_s = float(st.settings.get("stale_hours", 4)) * 3600
    now = time.time()
    out = []
    for f in db.features():
        f["waits_on"] = _waits_on(st, proj, f, running_convs, queued_convs)
        f["stale"] = f["phase"] != "done" and not f["archived"] and (now - (f["last_activity"] or 0)) > stale_s
        out.append(f)
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
            "phase_counts": counts, "features": feats, "team": team_status(st, proj),
            "activity": db.activity(30), "standup": db.current_standup(), "last_standup": db.last_standup()}


# ---- M1c: handoffs ------------------------------------------------------------------
def handoffs_dir(project_path: str, role: str) -> str:
    return os.path.join(project_path, ".aiteams", "handoffs", role)


async def write_handoff(st, proj: Project, role: str, turn: dict, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    db = proj.db
    rs = db.role_state(role)  # outside a rotation the handoff is saved on the role's own initiative
    info = db.session_info(role)
    d = handoffs_dir(proj.path, role)
    os.makedirs(d, exist_ok=True)
    n = len(db.handoffs(role, 10000)) + 1
    path = os.path.join(d, f"{n}.md")
    reason = rs["rotate_reason"] or "own initiative"
    last = db.turns(role, limit=1)
    pct = None
    if last and last[0].get("context_tokens"):
        from .orchestrator.turn import context_window_from
        res = json.loads(last[0]["result_json"]) if last[0].get("result_json") else None
        pct = last[0]["context_tokens"] / (context_window_from(res, None) or 1)
    header = (f"# Handoff — {role.capitalize()}, session #{info['seq']}\n"
              f"written {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime())}\nreason: {reason}\n"
              + (f"context: {pct:.0%}\n" if pct is not None else "") + "\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + text + "\n")
    rel = os.path.relpath(path, proj.path)
    msg = await _post(st, proj, f"dm-{role}", role, text, "Handoff", [], turn["id"])
    h = db.add_handoff(role, info["session_id"] or turn["session_id"], n, path, msg["id"], reason, pct)
    await _activity(proj, "handoff", f"{role} wrote handoff #{n} ({reason})", role, None, f"dm-{role}", f"handoff:{h['id']}")
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
