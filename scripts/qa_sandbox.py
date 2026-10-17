"""QA sandbox daemon for UI checks and Playwright: a temp AITEAMS_HOME, one seeded project with the fixed id `demo`,
the scheduler off (max_concurrent 0, so `claude` never runs) and test-only /qa routes.

    uv run --no-sync python -B scripts/qa_sandbox.py --port 8790 [--dir TMP] [--token T] [--seed demo|empty]

Never reads or writes ~/.aiteams and never talks to the live daemon. Ports 8790-8799 are reserved for sandboxes.
POST /qa/fire {kind, ...} emits one live event (kinds in FIRE_KINDS) through the same db + hub calls the real code
uses; POST /qa/reset re-seeds the project. Serves the working-tree UI build (`cd ui && npm run build`).
"""
import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_ID = "demo"
DEFAULT_TOKEN = "qa-sandbox-token"
FIRE_KINDS = ("message", "question", "question_message", "blocker", "approval", "commit_request", "committed",
              "commit_failed", "standup", "stopped", "interrupted", "fresh_session", "rate_limit", "rate_clear",
              "reaction", "card", "card_withdraw")


def isolate_env(home: Path, token: str) -> None:
    """Drop every AITEAMS_* variable (a role's turn carries the live daemon's URL and turn token), then point at home."""
    for k in [k for k in os.environ if k.startswith("AITEAMS_")]:
        del os.environ[k]
    os.environ["AITEAMS_HOME"] = str(home)
    os.environ["AITEAMS_TOKEN"] = token
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, rel: str, data: str | bytes) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data) if isinstance(data, bytes) else p.write_text(data)


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(64))
ORDERS_PY = '''"""Orders table access."""
from dataclasses import dataclass


@dataclass
class Order:
    id: int
    customer: str
    total_cents: int


def list_orders(db, limit=100):
    return [Order(*row) for row in db.execute("SELECT id, customer, total_cents FROM orders LIMIT ?", (limit,))]
'''
EXPORT_PY = '''"""CSV export of the orders table, streamed row by row."""
import csv
import io

from .orders import list_orders


def export_csv(db):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "customer", "total"])
    for o in list_orders(db, limit=None):
        w.writerow([o.id, o.customer, f"{o.total_cents / 100:.2f}"])
    return buf.getvalue()
'''
PRD_MD = """# F-1 PRD: Export orders to CSV

## Problem
Support exports orders by hand from the admin SQL console every Monday.

## Scope
- An **Export CSV** button in the orders header.
- `GET /api/orders/export.csv` streams `id, customer, total`.
- Exports respect the current filter.

## Out of scope
- Excel files, scheduled exports.
"""
DESIGN_MD = """# F-1 design

| piece | owner | notes |
|---|---|---|
| `src/export_csv.py` | backend | `csv.writer` over `list_orders(limit=None)` |
| Export button | frontend | right of the search box, `outline` button |

The endpoint returns `text/csv; charset=utf-8` with `Content-Disposition: attachment`.
"""


def make_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "QA Sandbox")
    _git(repo, "config", "user.email", "qa@sandbox.local")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", ".git/no-hooks")  # the Master's global hooks stay out of sandbox commits
    _write(repo, "README.md", "# demo shop\n\nA tiny orders app used by the AITeams QA sandbox.\n")
    _write(repo, "src/__init__.py", "")
    _write(repo, "src/orders.py", ORDERS_PY)
    _write(repo, "assets/logo.png", PNG)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")


async def seed(st, proj) -> None:
    """Deterministic demo data: features F-1 (docs, chat with mentions, approval, question, commit request) and F-2 (done)."""
    from aiteams import workflow
    from aiteams.api.public import post_message

    db, repo = proj.db, Path(proj.path)
    f1 = await workflow.new_feature(st, proj, "Export orders to CSV", "build", "lead",
                                    text="Support wants a CSV export of the orders table. Scope it, please.", starter="product")
    conv = f1["conversation_id"]
    docs = Path(workflow.docs_dir(proj.path, f1))
    (docs / "prd.md").write_text(PRD_MD)
    (docs / "design.md").write_text(DESIGN_MD)
    say = lambda conv_id, author, text, mentions, type_="Message": post_message(st, proj, conv_id, author, text, type_, mentions, None)
    await say(conv, "product", "PRD is in `prd.md`. @lead can you check the scope? @frontend the button goes in the orders header.",
              ["lead", "frontend"])
    await say(conv, "lead", "Scope looks right. @backend take the endpoint, @frontend the button. Design notes are in `design.md`.",
              ["backend", "frontend"], "Decision")
    await say(conv, "backend", "Endpoint is done and streams rows; commit request below. @lead please review.", ["lead"])
    await say("general", "master", "@manager what is everyone working on?", ["manager"])
    await say("general", "manager", "F-1 (CSV export) is in build: Backend has a commit in review, Frontend is on the button. "
              "Product's PRD is waiting on your approval.", [])
    await workflow.request_approval(st, proj, "product", None, "F-1", "PRD ready: CSV export scope",
                                    "Button in the orders header, streamed CSV endpoint, current filter respected. "
                                    "Excel and scheduled exports are out of scope.")
    turn = db.create_turn("frontend", "sess-demo-frontend", "master", "dm-frontend", token="qa-question-turn")
    db.update_turn(turn["id"], state="done", started_at=time.time() - 50, ended_at=time.time() - 40)
    q = db.create_question(turn["id"], "frontend", "toolu_demo", {"questions": [{
        "question": "Where should the Export CSV button go?", "header": "Placement", "multiSelect": False,
        "options": [{"label": "Orders header", "description": "Right of the search box, as in design.md"},
                    {"label": "Row menu", "description": "Per-selection export from the ⋯ menu"}]}]})
    await proj.hub.broadcast("question", q)
    # Master-stage commit request: one modified text file, one added file, one modified binary file.
    _write(repo, "src/orders.py", ORDERS_PY.replace("limit=100", "limit=100, customer=None").replace(
        '    return [Order(*row)', '    # customer filter added for the export\n    return [Order(*row)'))
    _write(repo, "src/export_csv.py", EXPORT_PY)
    _write(repo, "assets/logo.png", PNG[::-1])
    c = await workflow.request_commit(st, proj, "backend", None, "F-1", ["src/orders.py", "src/export_csv.py", "assets/logo.png"],
                                      "add CSV export endpoint\n\nStreams `id, customer, total` with `csv.writer`.")
    await workflow.review_commit(st, proj, "lead", c["id"], "approve", "Reviewed: streaming is fine, unit tests pass.")
    live = db.create_turn("frontend", "sess-demo-frontend", "master", conv, token="qa-live-turn")
    for ev in LIVE_EVENTS:
        db.add_turn_event(live["id"], ev)
    db.update_turn(live["id"], state="done", started_at=time.time() - 30, ended_at=time.time() - 12, cost_usd=0.21,
                   context_tokens=41000)
    await workflow.new_feature(st, proj, "Order search by customer", "done", "frontend", text="Search orders by customer name.")


LIVE_EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "sess-demo-frontend", "model": "claude-opus-5-5"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the design notes before I add the button."},
                                                  {"type": "tool_use", "id": "toolu_1", "name": "Read",
                                                   "input": {"file_path": ".aiteams/features/F-1/design.md"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                              "content": "# F-1 design\n| piece | owner | notes |"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash",
                                                   "input": {"command": "cd ui && npm run build", "description": "Build the UI"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "built in 2.1s"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "The button is in place; asking where it should live."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 18000, "total_cost_usd": 0.21,
     "result": "The button is in place; asking where it should live."},
]


async def fire(st, proj, body: dict) -> dict:
    """Emit one live event through the real code paths; returns the conversation the toast should open."""
    from aiteams import workflow
    from aiteams.api.public import post_message

    db, hub, repo = proj.db, proj.hub, Path(proj.path)
    kind, text = body.get("kind"), body.get("text")
    if kind not in FIRE_KINDS:
        raise ValueError(f"kind must be one of {', '.join(FIRE_KINDS)}")
    n = db.q("SELECT COUNT(*) c FROM messages")[0]["c"]
    f1 = workflow.resolve_feature(db, "F-1") or await workflow.new_feature(st, proj, "QA feature", "build", "lead")
    conv = f1["conversation_id"]
    role = body.get("role", "lead")
    out: dict = {"ok": True, "kind": kind, "conversation": conv}
    if kind == "message":
        conv = body.get("conversation", conv)
        m = await post_message(st, proj, conv, role, text or f"Plain message {n}", body.get("type", "Message"),
                               body.get("mentions", []), None)
        out.update(conversation=conv, message=m["id"])
    elif kind in ("question_message", "blocker"):
        type_ = "Question" if kind == "question_message" else "Blocker"
        m = await post_message(st, proj, conv, role, text or f"@master {type_} number {n}: can I drop the old export?",
                               type_, ["master"], None)
        out["message"] = m["id"]
    elif kind == "question":
        t = db.create_turn(role, f"sess-{role}", "master", f"dm-{role}", token=f"qa-fire-{n}-{time.time()}")
        q = db.create_question(t["id"], role, None, {"questions": [{
            "question": text or f"Which browser should I test first ({n})?", "header": "Browser", "multiSelect": False,
            "options": [{"label": "Chromium", "description": "Playwright's default"},
                        {"label": "Safari", "description": "WebKit"}]}]})
        await hub.broadcast("question", q)
        await workflow.broadcast_inbox(proj)
        out.update(conversation=f"dm-{role}", question=q["id"])
    elif kind == "approval":
        a = await workflow.request_approval(st, proj, role, None, "F-1", text or f"Design sign-off {n}", "Summary for QA.")
        out["approval"] = a["id"]
    elif kind in ("commit_request", "committed", "commit_failed"):
        rel = f"qa/fire_{n}.txt"
        _write(repo, rel, f"fired {n}\n")
        c = await workflow.request_commit(st, proj, "lead", None, "F-1", [rel], text or f"QA commit {n}")
        if kind == "commit_failed":
            (repo / rel).unlink()  # git add of a vanished, untracked path fails: the real failure path
        if kind != "commit_request":
            c = await workflow.decide_commit(st, proj, c["id"], "approve", None, None, force=True)
        out.update(commit=c["id"], state=c["state"])
    elif kind == "standup":
        su = db.create_standup(["lead", "frontend"], "master")
        await hub.broadcast("standup", su)
        su = db.update_standup(su["id"], state="done", ended_at=time.time())
        await hub.broadcast("standup", su)
        out.update(conversation="standup", standup=su["id"])
    elif kind in ("stopped", "interrupted", "fresh_session"):
        r = body.get("role", "frontend")
        line = {"stopped": f"{r}'s turn was stopped by the Master", "interrupted": f"Manager interrupted {r}: off task",
                "fresh_session": f"{r} started session #2 (context 61%)"}[kind]
        await hub.broadcast("activity", db.add_activity(kind, text or line, r, conversation_id=conv))
        out["conversation"] = f"dm-{r}"
    elif kind == "card":
        # F-10: a 2-question card (one multi-select) through the real ask_master path
        q = await workflow.ask_master(st, proj, role, None, body.get("feature", "F-1"), body.get("questions") or [
            {"question": text or f"Which export format first ({n})?", "header": "Format", "options": [
                {"label": "CSV (Recommended)", "description": "Opens everywhere"},
                {"label": "XLSX", "description": "Keeps types"}]},
            {"question": "Which columns matter?", "header": "Columns", "multiSelect": True, "options": [
                {"label": "Date", "description": ""}, {"label": "Amount", "description": ""},
                {"label": "Customer", "description": ""}]}],
            body.get("intro", "My take: CSV first, XLSX later."), body.get("purpose", "interview"))
        out.update(conversation=q["conversation_id"], question=q["id"], message=q["message_id"])
    elif kind == "card_withdraw":
        # withdraws card {question} as the role that asked it
        q = db.question(int(body["question"]))
        if q is None:
            raise ValueError(f"unknown card {body['question']}")
        q = await workflow.withdraw_question(st, proj, q["role"], q["id"])
        out.update(conversation=q["conversation_id"], question=q["id"], state=q["state"])
    elif kind == "reaction":
        # toggles `role`'s emoji (default 👍) on message_id, else on the newest message in `conversation`
        from aiteams.db import EMOJI
        conv = body.get("conversation", conv)
        mid = body.get("message_id") or (db.messages(conv, limit=1) or [{}])[-1].get("id")
        if not mid:
            raise ValueError(f"no message to react to in {conv}")
        emoji = body.get("emoji", EMOJI[0])
        added = db.toggle_reaction(int(mid), role, emoji)
        reactions = db.reactions_for([int(mid)]).get(int(mid), [])
        await hub.broadcast("reaction", {"message_id": int(mid), "reactions": reactions})
        out.update(conversation=conv, message=int(mid), added=added, reactions=reactions)
    elif kind == "rate_limit":
        await st.set_rate_limit({"status": "rejected", "resetsAt": time.time() + 3600})
        out["conversation"] = None
    elif kind == "rate_clear":
        await st.set_rate_limit({"status": "allowed"})
        out["conversation"] = None
    return out


def build_app(port: int, workdir: Path, seed_kind: str):
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import JSONResponse

    from aiteams import config
    from aiteams.api.public import master_auth
    from aiteams.daemon import create_app
    from aiteams.runtime import AppState, Project

    repo = workdir / "repo" / "demo"

    async def reseed(st) -> None:
        """Fresh repo + DB under the fixed id; keeps the old hub so open WebSockets stay connected."""
        old = st.projects.pop(DEMO_ID, None) if st is not None else None
        if old is not None:
            old.db.close()
        if repo.exists():
            shutil.rmtree(repo)
        make_repo(repo)
        config.save_projects([{"id": DEMO_ID, "path": os.path.realpath(repo), "name": "demo", "registered_at": time.time()}])
        target = st or AppState(port)
        proj = Project(config.load_projects()[0])
        target.projects[DEMO_ID] = proj
        if seed_kind == "demo":
            await seed(target, proj)  # on the new, client-less hub: open pages get no toasts for seed rows
        if old is not None:
            proj.hub = old.hub
        if st is None:
            proj.db.close()  # create_app's lifespan loads the project again from projects.json

    if seed_kind == "empty":
        config.save_projects([])
    else:
        asyncio.run(reseed(None))

    app = create_app(port, write_daemon_json=False)

    @app.post("/qa/fire", dependencies=[Depends(master_auth)])
    async def qa_fire(request: Request):
        st = request.app.state.app
        proj = st.projects.get(DEMO_ID)
        if proj is None:
            raise HTTPException(404, "no demo project (started with --seed empty)")
        try:
            return await fire(st, proj, await request.json())
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    @app.post("/qa/reset", dependencies=[Depends(master_auth)])
    async def qa_reset(request: Request):
        st = request.app.state.app
        await st.set_rate_limit({"status": "allowed"})  # a fired rate_limit must not leak into the next test
        if seed_kind == "empty":
            for p in st.projects.values():
                p.db.close()
            st.projects.clear()
            config.save_projects([])
        else:
            await reseed(st)
        return {"ok": True}
    return app


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="qa_sandbox", description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--dir", help="work dir (kept on exit); default a fresh temp dir, removed on exit")
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--seed", choices=("demo", "empty"), default="demo")
    args = ap.parse_args(argv)
    workdir = Path(args.dir).resolve() if args.dir else Path(tempfile.mkdtemp(prefix="aiteams-qa-"))
    home = workdir / "home"
    if args.dir and (workdir / "repo").exists():
        shutil.rmtree(workdir / "repo")
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"max_concurrent": 0}))
    isolate_env(home, args.token)
    sys.path.insert(0, str(ROOT))  # the working tree's aiteams package, whatever is installed

    import uvicorn

    app = build_app(args.port, workdir, args.seed)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    # uvicorn re-raises the captured signal after a graceful stop; exit through `finally` instead of dying on it.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"sandbox http://127.0.0.1:{args.port} token={args.token} home={home}", flush=True)
    try:
        server.run()
    finally:
        if not args.dir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
