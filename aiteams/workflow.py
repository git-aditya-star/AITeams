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
from . import commit_graph
from .db import PHASES, WAKE_KINDS, ProjectDB
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
    lines.append("- voices: " + ", ".join(voices(db, feat)))
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


# ---- F-10: voices and question cards -----------------------------------------
CARD_PURPOSES = ("ask", "interview", "concern", "a_vs_b", "brainstorm")


def voices(db: ProjectDB, feat: dict) -> list[str]:
    """Roles with a voice on a feature: authors of / mentioned in its conversation (non-system), plus Product and
    Lead; in ROLES order."""
    who = {"product", "lead"}
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
        owners = ["lead", "product"] + ([feat["owner"]] if feat and feat["owner"] not in (None, "lead", "product") else [])
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


async def withdraw_question(st, proj: Project, role: str, qid: int) -> dict:
    db = proj.db
    q = db.question(qid)
    if q is None or q.get("kind") != "card":
        raise HTTPException(404, f"unknown card #{qid}")
    if q["role"] != role:
        raise HTTPException(403, f"card #{qid} belongs to {q['role']}")
    if q["state"] != "pending":
        raise HTTPException(409, f"card #{qid} is {q['state']}")
    q = db.withdraw_question(qid)
    await _post(st, proj, q["conversation_id"], "system", f"Card #{qid} withdrawn by {role}.", "System", [])
    await proj.hub.broadcast("question", q)
    await broadcast_inbox(proj)
    return q


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
    if c.get("requires"):
        lines.append("Requires " + ", ".join(f"#{n}" for n in c["requires"]))
    lines += ["", c["message"]]
    if c["state"] == "lead_review":
        lines += ["", f"@lead please review: `git diff HEAD -- {' '.join(c['files'])}`, then "
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
    reqs: list[int] = []
    for n in requires or []:
        r = proj.db.commit(int(n)) if str(n).lstrip("#").isdigit() else None
        if r is None or _superseded(r):
            raise HTTPException(400, f"requires #{n}: no such open commit request"
                                     + (f" (it was {r['note']})" if r is not None else ""))
        if r["id"] not in reqs:
            reqs.append(r["id"])
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
            if n not in reqs and n not in old_ids and r and r["state"] != "rejected":
                reqs.append(n)
    reqs = [n for n in reqs if n not in old_ids]
    c = db.create_commit(feat["id"] if feat else None, role, turn_id, rels, message, hashes, state, reqs)
    conv = feat["conversation_id"] if feat else f"dm-{role}"
    mentions = ["lead"] if state == "lead_review" else []
    msg = await _post(st, proj, conv, role, _commit_text(c, feat), "Commit", mentions, turn_id, f"commit:{c['id']}")
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
    return {"ids": [m["id"] for m in g], "files": files}


async def review_commit(st, proj: Project, role: str, cid: int, decision: str, note: str | None,
                        turn_id: int | None = None) -> dict:
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
        await _post(st, proj, conv, "system", f"Lead approved commit #{cid}; waiting on the Master.", "System", [],
                    turn_id, ref=f"{COMMIT_EVENT}{cid}")
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


def _decision_mentions(requester: str) -> list[str]:
    """F-14: the Master's commit decisions wake the requester and the Lead, who sequences the next step."""
    return [requester] if requester == "lead" else [requester, "lead"]


async def decide_commit(st, proj: Project, cid: int, decision: str, message: str | None, note: str | None,
                        force: bool, group: bool = False) -> dict:
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
            return await _commit_group(st, proj, [m for m in g if m["state"] != "committed"], note, force)
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
        stale = [f for f in c["files"] if _sha(proj.path, f) != c["hashes"].get(f)]
        if stale and not force:
            raise HTTPException(409, {"stale": stale})
        msg_text = (message or "").strip() or c["message"]
        ok, out = await asyncio.to_thread(_commit_sync, proj.path, c["files"], msg_text)
        if ok:
            c = proj.db.update_commit(cid, state="committed", sha=out, message=msg_text, note=note or None)
            subject = msg_text.splitlines()[0]
            await _post(st, proj, conv, "master", f"{at} Committed `{out}`: {subject}", "Decision", who)
            await _activity(proj, "commit", f"Committed {out}: {subject}", c["role"], c["feature_id"], conv, f"commit:{cid}")
        else:
            c = proj.db.update_commit(cid, state="failed", note=out, message=msg_text)
            await _post(st, proj, conv, "master", f"{at} Commit #{cid} failed:\n```\n{out}\n```", "Decision", who)
    await proj.hub.broadcast("commit", c)
    await broadcast_inbox(proj)
    return c


def _group_commit_sync(project_path: str, members: list[dict]) -> tuple[list[str], dict | None, str]:
    """Commit each member in order; on a failure roll HEAD and the index of the group's files back (the working
    tree is never touched). Returns (shas of the committed members, the failed member or None, git output)."""
    orig = _git(project_path, "rev-parse", "HEAD").stdout.strip()
    shas = []
    for m in members:
        ok, out = _commit_sync(project_path, m["files"], m["message"])
        if not ok:
            if orig:
                _git(project_path, "reset", "-q", "--soft", orig)
                files = sorted({f for x in members for f in x["files"]})
                _git(project_path, "reset", "-q", orig, "--", *files)
            return [], m, out
        shas.append(out)
    return shas, None, ""


async def _commit_group(st, proj: Project, members: list[dict], note: str | None, force: bool) -> dict:
    """F-11: commit every pending member of a group in dependency order, all or nothing."""
    db = proj.db
    bad = [m for m in members if m["state"] != "master_review"]
    if bad:
        raise HTTPException(409, {"group": [{"id": m["id"], "state": m["state"]} for m in members],
                                  "detail": f"#{bad[0]['id']} is {bad[0]['state']}"})
    stale = {str(m["id"]): s for m in members if (s := [f for f in m["files"] if _sha(proj.path, f) != m["hashes"].get(f)])}
    if stale and not force:
        raise HTTPException(409, {"stale": stale})
    shas, failed, out = await asyncio.to_thread(_group_commit_sync, proj.path, members)
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
            who = _decision_mentions(m["role"])
            subject = m["message"].splitlines()[0]
            await _post(st, proj, mconv, "master", " ".join("@" + r for r in who) + f" Committed `{sha}`: {subject}",
                        "Decision", who)
            await _activity(proj, "commit", f"Committed {sha}: {subject}", m["role"], m["feature_id"], mconv,
                            f"commit:{m['id']}")
        await _post(st, proj, conv, "system", f"Group {label} committed.", "System", [],
                    ref=f"{COMMIT_EVENT}{first['id']}")
    for m in members:
        await proj.hub.broadcast("commit", db.commit(m["id"]))
    await broadcast_inbox(proj)
    return {**db.commit(first["id"]), "group": [db.commit(m["id"]) for m in members]}


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
        mine = c is not None and (c["role"] == role or role == "lead")
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
        state = "running" if runner else ("queued" if any(db.queued_wake(role, k) for k in WAKE_KINDS) else "idle")
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
