# M1c end-to-end verification (real `claude -p`, Opus, ~$4–6)

Prelude: `cd ui && npm run build && cd ..`, `uv sync`, `aiteams stop`, make a throwaway git repo
(`mkdir /tmp/m1c-demo && cd /tmp/m1c-demo && git init && echo hi > README.md && git add -A && git commit -qm init`),
run `aiteams` there (registers + opens the UI). `P=<project id>`, `B=http://127.0.0.1:7700`.
Quote URLs with `?` (zsh globs them). Pipe curl into `jq`.
Since M2a, install via `aiteams upgrade` (see README) and give every curl `-H "Authorization: Bearer $(cat ~/.aiteams/token)"`.

## 1. Rotation at the threshold
- `curl -s -X PUT $B/api/settings -H 'content-type: application/json' -d '{"rotate_pct": 0.02}'`
- New task (UI or `POST $B/api/p/$P/features {"text":"Write a short PRD for a CSV export of README.md","starter":"product"}`).
- Expect, in order: Product's normal turn ends above 2% → `GET /team` shows `rotation: "context N%"`,
  `rotation_handoff_pending: true` → a `handoff` turn in Product's Live session (chip `handoff`, Prompt toggle
  shows "Handoff turn") → a `Handoff` message in Product's DM → `.aiteams/handoffs/product/1.md` exists.
- @mention Product in its DM ("thanks, continue"). Expect `Fresh session #2 (context N%)` System line, the new
  turn's stored prompt (`GET /turns?role=product` → `prompt`) starting with `# Fresh session #2 — handoff`,
  `GET /team` → new `session_id`, `context_pct` ≈ 0 before the turn, `session_seq: 2`, `fresh_starts: 1`.
- Restore: `PUT /settings {"rotate_pct": 0.6}`.

## 2. Master Stop
- DM Backend: "run `python3 -c \"import time; time.sleep(90)\"` and then say done".
- While it sleeps click **Stop** in Backend's DM title bar (or `POST /team/backend/stop`).
- Expect within ~2 s: turn `interrupted`, DM System line "Turn stopped by the Master.", no queued wake for
  backend (`GET /team` → `idle`), dashboard Activity shows "backend's turn was stopped by the Master".

## 3. Manager interrupt + checkpoint
- DM QA the same 90 s sleep. In #general: "@manager interrupt qa (reason: test) and then checkpoint qa without
  a handoff".
- Expect QA's turn `interrupted` with "Turn interrupted by the Manager: test", then QA's DM gets the checkpoint
  System line and `GET /team` shows `rotation: "checkpoint: ..."` with `rotation_handoff_pending: false`.
- DM QA "hello" → `Fresh session #2 (checkpoint: ...)`, prompt has no handoff section.

## 4. Fresh start button (with handoff)
- Dashboard → Team → Lead → **Fresh start** (leave `skip handoff` unchecked). Lead must have run at least once
  (DM it "hi" first). Expect the `handoff pending` chip, a handoff turn, a `Handoff` DM, and a fresh session on
  the next DM.

## 5. Standup fan-out
- With Product and Backend active since start, click **Standup** on the dashboard.
- Expect: System line in #standup "Standup #1 called by the Master — waiting for ...", standup turns for those
  roles (chip `standup`), each posts one Done/Next/Blocked message, then a Manager `standup_compile` turn,
  `.aiteams/standups/<ts>.md`, a `Standup` message in #standup by manager mentioning @master, Inbox count unchanged,
  dashboard "last standup" link opens #standup at that message.
- Click Standup again → "nothing changed" inline, no wakes.

## 6. Feature fresh start
- Feature header → Fresh start → **Keep docs**: divider "Fresh start · time" in the chat, docs untouched, DM a role
  "@backend summarise the feature so far" → its prompt (Live session → Prompt) contains only the post-reset messages.
- Fresh start → **Start over** (second click): docs under `.aiteams/features/F-1/archive/<ts>/`, phase intake,
  the original request re-posted and Product woken.

## 7. Dashboard, History, receipts
- `/p/$P` lands on the dashboard. A feature with a pending approval shows `waits on master (approval)`.
- `PUT /settings {"stale_hours": 0}` → every active feature shows the stale chip; restore to 4.
- Set a feature `done` → it leaves the sidebar, appears under History; reopen from History (phase → build).
- Receipts show SVG icons (no emoji) and move queued → seen → acked live when a role answers.

## 8. Hygiene
`uv run pytest -q` green; `cd ui && npx tsc --noEmit && npm run build` clean; no `__pycache__`;
`git status` shows only intended files; `aiteams stop` leaves no `claude -p` processes (`pgrep -fl "claude -p"`).
