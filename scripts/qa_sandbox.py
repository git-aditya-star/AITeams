"""QA sandbox daemon for UI checks and Playwright: a temp AITEAMS_HOME, one seeded project with the fixed id `demo`,
the scheduler off (max_concurrent 0, so `claude` never runs) and test-only /qa routes.

    uv run --no-sync python -B scripts/qa_sandbox.py --port 8790 [--dir TMP] [--token T] [--seed demo|empty]

Never reads or writes ~/.aiteams and never talks to the live daemon. Ports 8790-8799 are reserved for sandboxes:
8790 is the Master's by hand, 8791-8796 are the roles' e2e/shot ports (ui/e2e/ports.mjs), 8798 is shot.mjs without a
role, and 8797 and 8799 stay free for QA's live sandboxes.
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
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_ID = "demo"
DEFAULT_TOKEN = "qa-sandbox-token"
FIRE_KINDS = ("message", "question", "question_message", "blocker", "approval", "commit_request", "committed",
              "commit_failed", "standup", "stopped", "interrupted", "fresh_session", "rate_limit", "rate_clear",
              "reaction", "card", "card_withdraw", "withdraw", "brainstorm", "commit_group", "plugin_missing",
              "turn_event")


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
    """Deterministic demo data: features F-1 (docs, chat with mentions, approval, question, commit request), F-2 (done) F-3 (done, with a prd.md Summary and a request committed 3h ago) and F-4 (build, Lead 2); the early chat is backdated."""
    from aiteams import workflow
    from aiteams.api.public import post_message

    db, repo = proj.db, Path(proj.path)
    f1 = await workflow.new_feature(st, proj, "Export orders to CSV", "build", "lead",
                                    text="Support wants a CSV export of the orders table. Scope it, please.", starter="product")
    conv = f1["conversation_id"]
    # F-22: F-1 has its Lead before the chat below, so "@lead" in it routes to Lead 1 with no "picked automatically"
    # line in between (that line would break the folded "3 replies" run). Direct SQL: no message, updated_at stays.
    db.x("UPDATE features SET lead='lead' WHERE id=?", f1["id"])
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
    last_chat = await say("general", "manager", "F-1 (CSV export) is in build: Backend has a commit in review, Frontend is "
                          "on the button. Product's PRD is waiting on your approval.", [])
    await workflow.request_approval(st, proj, "product", None, "F-1", "PRD ready: CSV export scope",
                                    "Button in the orders header, streamed CSV endpoint, current filter respected. "
                                    "Excel and scheduled exports are out of scope.")
    # F-21: the daemon refuses commit requests on a feature until its design is approved. F-1's design was approved
    # before the build; the row is made directly so no extra message shifts the seeded ids (the PRD approval is #1).
    da = db.create_approval(f1["id"], "lead", None, "Design: Export orders to CSV", ".aiteams/features/F-1/design.md")
    db.resolve_approval(da["id"], "approved", None)
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
    # F-17: a second done feature, with a prd.md Summary line (F-2 has none), for the Dashboard's Shipped panel
    f3 = await workflow.new_feature(st, proj, "Order notes", "done", "backend", text="Let support add a note to an order.")
    (Path(workflow.docs_dir(proj.path, f3)) / "prd.md").write_text(
        "# F-3 Order notes\n\n**Summary:** Support can add a short note to any order.\n")
    # F-18: give the Dashboard's 24h sparkline some shape. The chat up to the manager's reply moves back in three
    # chunks (9h, 5h and 2h ago), keeping id order = time order, and F-3 gets a request committed 3h ago.
    now = time.time()
    ids = [r["id"] for r in db.q("SELECT id FROM messages WHERE id<=? ORDER BY id", last_chat["id"])]
    for i, mid in enumerate(ids):
        db.x("UPDATE messages SET created_at=? WHERE id=?", now - (9, 5, 2)[i * 3 // len(ids)] * 3600 + i, mid)
    c3 = db.create_commit(f3["id"], "backend", None, ["src/notes.py"], "add order notes", {}, "committed")
    db.x("UPDATE commits SET sha=?, created_at=?, updated_at=? WHERE id=?", "0a1b2c3", now - 3 * 3600 - 60,
         now - 3 * 3600, c3["id"])
    # F-22: F-1..F-3 belong to Lead 1 (what migration 9 gives existing features); F-4, an active feature, to Lead 2.
    # Direct SQL, so updated_at (History's order) and every seeded message id stay as they are.
    db.x("UPDATE features SET lead='lead'")
    f4 = await workflow.new_feature(st, proj, "Order export schedule", "build", "backend")
    db.x("UPDATE features SET lead='lead-2' WHERE id=?", f4["id"])
    # F-25: two resolved approvals on F-3 for History's Approvals tab and the outcome badges. Last, and straight to the
    # db (no message), so no seeded id or message count moves. Their ids come after every seeded approval (#3, #4).
    ch = db.create_approval(f3["id"], "backend", None, "Use skill: team-skills:local-preview", "Preview the notes page locally.")
    db.resolve_approval(ch["id"], "changes_requested", "Say which page, then ask again.")
    rj = db.create_approval(f3["id"], "frontend", None, "Scope lock: Order notes export", "Also export notes to PDF.")
    db.resolve_approval(rj["id"], "rejected", "PDF is out of scope for this release.")
    # F-29: mocks and a diagram in F-1's docs, plus two chat messages that show them. Last, so no seeded message id
    # moves (these two come after every other seeded message), and the design.md addition is appended.
    _write(docs, "mock-export.html", MOCK_EXPORT_HTML)
    _write(docs, "shot.png", _png())
    _write(docs, "flow.svg", FLOW_SVG)
    (docs / "design.md").write_text(DESIGN_MD + DESIGN_MERMAID)
    await say(conv, "frontend", "Mock of the Export button and a screenshot of it:\n"
              "`.aiteams/features/F-1/mock-export.html` and `.aiteams/features/F-1/shot.png`", [])
    await say(conv, "backend", "The export flow:\n\n```mermaid\nsequenceDiagram\n  Master->>UI: Export CSV\n"
              "  UI->>API: GET export.csv\n  API-->>UI: stream rows\n```", [])
    # F-29 B: one published artifact on F-1 (a fake url, version 2) and its link card, after every other seeded message.
    # Its source is the mock above. The row and the message go straight to the db: nothing is published, nobody is woken.
    art = db.create_artifact(f1["id"], ".aiteams/features/F-1/mock-export.html", "frontend", None, None,
                             "https://claude.ai/artifact/demo", "Export orders mock", 2)
    db.add_message(conv, "frontend", "Message", "Updated Export orders mock (v2)\n\nhttps://claude.ai/artifact/demo", [],
                   None, f"artifact:{art['id']}")


MOCK_EXPORT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Export orders mock</title>
<style>
  body { margin: 0; padding: 24px; font: 14px/1.5 -apple-system, "Segoe UI", Inter, sans-serif; color: #171717; background: #fafafa; }
  .card { max-width: 420px; border: 1px solid #e5e5e5; border-radius: 12px; padding: 16px; background: #fff; }
  h1 { font-size: 16px; margin: 0 0 4px; } p { margin: 0 0 12px; color: #737373; }
  button { border: 1px solid #d4d4d4; border-radius: 8px; padding: 6px 12px; background: #fff; font: inherit; cursor: pointer; }
  ul { margin: 16px 0 0; padding: 0; list-style: none; font: 12px ui-monospace, Menlo, monospace; color: #525252; }
</style></head><body>
<div class="card" data-mock="export">
  <h1>Orders</h1><p>Export the orders table as CSV.</p>
  <button type="button" id="export">Export CSV</button>
  <ul id="probe"><li id="probe-parent">parent: checking</li><li id="probe-fetch">fetch: checking</li></ul>
</div>
<script>
  // The sandbox probe: a mock must not reach the page that frames it, nor the daemon's API with the Master's cookie.
  const say = (id, text) => { document.getElementById(id).textContent = text; };
  try {
    if (parent === window) say('probe-parent', 'parent: not framed');
    else { void parent.document.title; say('probe-parent', 'parent: reachable'); }
  } catch (e) { say('probe-parent', 'parent: blocked'); }
  fetch('/api/projects').then((r) => say('probe-fetch', r.ok ? 'fetch: reachable' : 'fetch: blocked'))
    .catch(() => say('probe-fetch', 'fetch: blocked'));
</script>
</body></html>
"""
FLOW_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="360" height="120" viewBox="0 0 360 120">
  <rect x="8" y="40" width="96" height="40" rx="8" fill="#fff" stroke="#a3a3a3"/><text x="56" y="65" font-size="13" text-anchor="middle" fill="#171717">Export</text>
  <rect x="132" y="40" width="96" height="40" rx="8" fill="#fff" stroke="#a3a3a3"/><text x="180" y="65" font-size="13" text-anchor="middle" fill="#171717">Stream</text>
  <rect x="256" y="40" width="96" height="40" rx="8" fill="#fff" stroke="#a3a3a3"/><text x="304" y="65" font-size="13" text-anchor="middle" fill="#171717">Download</text>
  <path d="M104 60h28M228 60h28" stroke="#737373" stroke-width="2"/>
</svg>
"""
DESIGN_MERMAID = """
```mermaid
flowchart LR
  A[Export CSV] --> B[GET export.csv]
  B --> C[Stream rows]
  C --> D[Download]
```
"""


def _png(w: int = 160, h: int = 100) -> bytes:
    """A small valid RGB PNG (a gradient), generated so the seed needs no binary file."""
    import struct
    import zlib

    rows = b"".join(b"\x00" + b"".join(bytes((40 + x * 160 // w, 90 + y * 120 // h, 200)) for x in range(w)) for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _tool(n: int, name: str, tool_input: dict) -> dict:
    """An assistant event with one tool_use, in the shape the stream carries (F-33 design: "Real event shapes")."""
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": tool_input}]}}


def _answer(n: int, content: str, data: dict | None = None, is_error: bool = False) -> dict:
    """The user event that answers it: the tool_result in the message, and `tool_use_result` next to it."""
    block: dict = {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": content}
    if is_error:
        block["is_error"] = True
    ev: dict = {"type": "user", "message": {"role": "user", "content": [block]}}
    if data is not None:
        ev["tool_use_result"] = data
    return ev


def _think(n: int) -> dict:
    """The thinking block a headless run really ends each model step with: only the signature, the text is EMPTY."""
    return {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "", "signature": f"sig-step-{n}"}]}}


def _hook(name: str, event: str, outcome: str | None = None, output: str = "") -> list[dict]:
    """system hook_started / hook_response events (not a `hook_event` type: that is what an older parser looked for)."""
    out = [{"type": "system", "subtype": "hook_started", "hook_id": f"hook-{name}", "hook_name": name, "hook_event": event}]
    if outcome:
        out.append({"type": "system", "subtype": "hook_response", "hook_id": f"hook-{name}", "hook_name": name,
                    "hook_event": event, "outcome": outcome, "output": output})
    return out


_LIVE_TSX = "/work/demo/ui/src/components/LiveSession.tsx"
_TERMINAL_TS = "\n".join([
    "// Pure helpers that turn a stream-json tool call into one terminal line.",
    "export function relPath(p: string, root: string) {",
    "  return p.startsWith(root + '/') ? p.slice(root.length + 1) : p",
    "}",
    "",
    "export function plural(n: number, word: string) {",
    "  return n + ' ' + word + (n === 1 ? '' : 's')",
    "}",
    "",
    "export function firstLine(text: string) {",
    "  return text.split('\\n').find((l) => l.trim()) ?? ''",
    "}",
    "",
    "export const DENY = 'AITEAMS_DENY'",
]) + "\n"

# One full turn of the frontend role, in the real stream-json shapes (a probe of `claude -p --output-format stream-json
# --verbose --include-hook-events`): thinking (one block with text, then an EMPTY one before every later step, which is what a
# headless run returns: only the signature), Read, Grep, an Edit with `structuredPatch`, a Write, a denied Edit, a failing
# and a passing Bash, a post_message, system hook events, then the final result. No `$` anywhere: F-8 keeps cost and
# currency out of this view. Smoke and primitives specs read the first and last sentences and the result line.
LIVE_EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "sess-demo-frontend", "model": "claude-opus-5-5", "cwd": "/work/demo"},
    *_hook("SessionStart:startup", "SessionStart", "success"),
    {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "The Master wants the export button in the "
                                                   "orders header. Read the design first, then find where the header renders.",
                                                   "signature": "sig-demo"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the design notes before I add the button."}]}},
    _tool(1, "Read", {"file_path": "/work/demo/.aiteams/features/F-1/design.md"}),
    _answer(1, "     1\t# F-1 design\n     2\t\n     3\t| piece | owner | notes |\n     4\t| --- | --- | --- |\n"
               "     5\t| endpoint | backend | streams rows |\n     6\t| button | frontend | orders header |",
            {"type": "text", "file": {"filePath": "/work/demo/.aiteams/features/F-1/design.md", "numLines": 6, "startLine": 1,
                                       "totalLines": 6}}),
    *_hook("PreToolUse:Grep", "PreToolUse", "success"),
    _think(2),
    _tool(2, "Grep", {"pattern": "function BlockView", "path": "/work/demo/ui/src", "output_mode": "content"}),
    _answer(2, "ui/src/components/LiveSession.tsx:140:function BlockView({ b }: { b: Block }) {",
            {"mode": "content", "numFiles": 0, "filenames": [], "content": "ui/src/components/LiveSession.tsx:140:function "
             "BlockView({ b }: { b: Block }) {", "numLines": 1}),
    _think(3),
    _tool(3, "Edit", {"file_path": _LIVE_TSX, "old_string": "  switch (b.kind) {",
                      "new_string": "  // terminal look: one block per tool call\n  switch (b.kind) {", "replace_all": False}),
    _answer(3, f"The file {_LIVE_TSX} has been updated successfully.",
            {"filePath": _LIVE_TSX, "oldString": "  switch (b.kind) {",
             "newString": "  // terminal look: one block per tool call\n  switch (b.kind) {", "replaceAll": False,
             "structuredPatch": [{"oldStart": 140, "oldLines": 3, "newStart": 140, "newLines": 4,
                                  "lines": [" function BlockView({ b }: { b: Block }) {", "-  switch (b.kind) {",
                                            "+  // terminal look: one block per tool call", "+  switch (b.kind) {",
                                            "     case 'text':"]}]}),
    _think(4),
    _tool(4, "Write", {"file_path": "/work/demo/ui/src/lib/terminal.ts", "content": _TERMINAL_TS}),
    _answer(4, "File created successfully at: /work/demo/ui/src/lib/terminal.ts",
            {"type": "create", "filePath": "/work/demo/ui/src/lib/terminal.ts", "content": _TERMINAL_TS, "structuredPatch": []}),
    *_hook("PreToolUse:Edit", "PreToolUse", "error", "AITEAMS_DENY: aiteams/workflow.py is locked by backend. Ask them in chat."),
    _think(5),
    _tool(5, "Edit", {"file_path": "/work/demo/aiteams/workflow.py", "old_string": "def docs_list(", "new_string": "def docs_list_all(",
                      "replace_all": False}),
    _answer(5, "AITEAMS_DENY: aiteams/workflow.py is locked by backend. Ask them in chat.", is_error=True),
    _think(6),
    _tool(6, "Bash", {"command": "cd ui && npx tsc --noEmit", "description": "Typecheck the UI"}),
    _answer(6, "Exit code 2\nsrc/components/LiveSession.tsx(212,7): error TS2322: Type 'string' is not assignable to type "
               "'number'.\n  212         toolId: c.id,\n              ~~~~~~\nFound 1 error in src/components/LiveSession.tsx:212",
            "Error: Exit code 2", is_error=True),
    _think(7),
    _tool(7, "Bash", {"command": "cd ui && npm run build", "description": "Build the UI"}),
    _answer(7, "built in 2.1s"),
    *_hook("Stop", "Stop", "success"),
    _think(8),
    _tool(8, "mcp__aiteams__post_message",
          {"conversation": "feature-1", "type": "Handoff", "mentions": ["lead"],
           "text": "@lead The terminal view is filed as **#104**. It is 5 files, and the full e2e gave **137 passed**.\n\n"
                   "- `terminal.ts`: pure helpers for each tool line\n- `LiveSession.tsx`: the dark pane and the paired calls"}),
    _answer(8, "posted #1412"),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "The button is in place; asking where it should live."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 18000, "total_cost_usd": 0.21,
     "result": "The button is in place; asking where it should live."},
]


async def fire(st, proj, body: dict) -> dict:
    """Emit one live event through the real code paths; returns the conversation the toast should open."""
    from aiteams import config, plugins, workflow
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
        q = await workflow.withdraw_request(st, proj, q["role"], "card", q["id"], body.get("reason") or "not needed any more")
        out.update(conversation=q["conversation_id"], question=q["id"], state=q["state"])
    elif kind == "withdraw":
        # F-34: withdraws {item: approval|commit|card, id} as {role} (default: the role that sent it) with {reason}.
        # The item's kind is `item`, since `kind` is already the name of this fire kind.
        what = body.get("item", "approval")
        row = {"approval": db.approval, "commit": db.commit, "card": db.question}.get(what, lambda _: None)(int(body["id"]))
        if row is None:
            raise ValueError(f"unknown {what} {body['id']}")
        item = await workflow.withdraw_request(st, proj, body.get("role") or row["role"], what, row["id"],
                                               body.get("reason", "superseded"))
        feat = db.feature(item["feature_id"]) if item.get("feature_id") else None
        out.update(conversation=item.get("conversation_id") or (feat["conversation_id"] if feat else f"dm-{item['role']}"),
                   item=what, id=item["id"], state=item["state"], note=item["note"])
    elif kind == "commit_group":
        # F-11: a backend request + a frontend request that requires it, both Lead-approved (one Inbox item) unless
        # {lead_review: true} leaves the frontend half in Lead review (the group isn't ready)
        _write(repo, f"qa/api_{n}.py", f"def api_{n}(): return {n}\n")
        _write(repo, f"qa/ui_{n}.ts", f"export const ui{n} = {n}\n")
        base = await workflow.request_commit(st, proj, "backend", None, "F-1", [f"qa/api_{n}.py"],
                                             text or f"group backend half {n}")
        await workflow.review_commit(st, proj, "lead", base["id"], "approve", None)
        ui = await workflow.request_commit(st, proj, "frontend", None, "F-1", [f"qa/ui_{n}.ts"],
                                           f"group UI half {n}", [base["id"]])
        if not body.get("lead_review"):
            await workflow.review_commit(st, proj, "lead", ui["id"], "approve", None)
        out.update(commits=[base["id"], ui["id"]], group=base["id"])
    elif kind == "brainstorm":
        # F-10: starts a round on `feature` (default F-1) through the real path; the sandbox runs no turns, so it
        # stays `collecting` with one queued wake per voice
        feat = workflow.resolve_feature(db, body.get("feature", "F-1"))
        bs = await workflow.start_brainstorm(st, proj, feat, text or f"How should export handle big files ({n})?",
                                             body.get("requested_by", "master"))
        out.update(conversation=feat["conversation_id"], brainstorm=bs["id"], roles=bs["roles"],
                   message=bs["start_message_id"])
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
    elif kind == "turn_event":
        # F-33: append one stream-json event (body `event`) to `role`'s newest turn, as a running turn would: stored, then
        # broadcast, so an open Live session shows it. The seeded turn is already done; the view does not care.
        turns = db.turns(role if "role" in body else "frontend", 1)
        if not turns or not isinstance(body.get("event"), dict):
            raise ValueError("turn_event needs a role with a turn and an `event` object")
        stored = db.add_turn_event(turns[0]["id"], body["event"])
        await hub.broadcast("turn_event", stored)
        out.update(turn=turns[0]["id"], seq=stored["seq"])
    elif kind == "plugin_missing":
        # F-23: a plugin switched On that a turn did not load. {id, role, loaded: true} makes it load instead, which clears it.
        pid = body.get("id") or "project-notes@project-notes"
        if not any(r["id"] == pid and r["on"] for r in config.plugin_rows(st.settings)):
            rows = [r for r in config.plugin_rows(st.settings) if r["id"] != pid]
            st.settings = config.save_settings({"plugins": [*rows, {"id": pid, "on": True, "ask": False}]})
        init = {"type": "system", "subtype": "init", "plugins": [{"name": config.plugin_name(pid), "source": pid}]
                if body.get("loaded") else [{"name": "team-skills", "source": "team-skills@example"}]}
        out.update(plugin=pid, flagged=await plugins.check_plugins(st, proj, role, init), conversation=None)
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
        if seed_kind == "demo" and st is not None:
            await seed(target, proj)  # on the new, client-less hub: open pages get no toasts for seed rows
        if old is not None:
            proj.hub = old.hub
        if st is None:
            proj.db.close()  # create_app's lifespan loads the project again from projects.json

    if seed_kind == "empty":
        config.save_projects([])
    else:
        asyncio.run(reseed(None))  # repo + projects.json only

    app = create_app(port, write_daemon_json=False)
    if seed_kind == "demo":
        # Seed after the daemon opened the project: opening expires pending blocking questions (F-12), and the
        # demo's blocking question must stay pending.
        inner = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(a):
            async with inner(a):
                await reseed(a.state.app)
                yield
        app.router.lifespan_context = lifespan

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
        st.plugin_status.clear()  # nor a fired plugin_missing or its row
        st.settings = config.save_settings({"plugins": []})
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
