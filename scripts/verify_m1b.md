# M1b end-to-end verification

Real `claude` on the configured model (default Opus). ~10 role turns ≈ $3–5 of Team-plan quota.
Prelude as in `verify_m1a.md`: `cd ui && npm run build`, `uv sync`, `aiteams` already installed
editable; `aiteams stop` first so the daemon restarts on the new code.
Since M2a, install via `aiteams upgrade` (see README) and give every curl `-H "Authorization: Bearer $(cat ~/.aiteams/token)"`.

```
export TMPREPO=$(mktemp -d)/demo && mkdir -p $TMPREPO && cd $TMPREPO && git init -q && echo "# demo" > README.md && git add -A && git commit -qm init
aiteams            # opens /p/<id>; PID below = that project id
```

## 1. New task → PRD → approval
Sidebar "New task": `Add a CSV export to the reports page. Write the PRD and ask me to lock the scope.`,
starter Product. Expect: `F-1` under Features (phase intake), Product's turn writes
`.aiteams/features/F-1/prd.md` (Docs tab lists it with "product, just now"), calls `request_approval`
→ Inbox badge 1, Approval card inline + in Inbox. Approve with set phase `spec`: phase chip `spec`,
a Decision from master mentioning @product, Product woken.
```
curl -s localhost:7700/api/p/PID/features | jq '.[] | {key, phase, open_approvals}'
curl -s localhost:7700/api/p/PID/inbox | jq '{count, approvals: [.approvals[] | {id, title, state}]}'
curl -s localhost:7700/api/p/PID/features/1/docs | jq .
```

## 2. Locks
DM backend: `Append one line "backend was here" to README.md, then run python3 -c "import time; time.sleep(90)" and end.`
~10 s later DM frontend: `Append one line "frontend was here" to README.md and end.`
Expect: frontend's Edit denied (Live session shows `AITEAMS_DENY: README.md is locked by backend`),
`GET /locks` shows backend's lock; after backend's turn ends the lock list is empty.
```
curl -s localhost:7700/api/p/PID/locks | jq .
```

## 3. Commit flow (backend → Lead → Master)
DM backend: `In F-1, create api_export.py with a hello() function and request a commit for it.`
Expect: commit row `lead_review`, Commit message in feature-1 mentioning @lead, Lead woken, Lead
approves (`review_commit`) → Inbox commit card with diff. Edit the subject in the card, Approve:
`git log -1` shows the edited subject with the user's git identity, backend woken by the Decision.
```
curl -s "localhost:7700/api/p/PID/commits" | jq '.[] | {id, role, state, files, sha}'
git -C $TMPREPO log -1 --format='%h %an: %s'
```

## 4. Lead direct + reject
DM lead: `Fix the typo "demo" -> "Demo" in README.md and request a commit.` Expect: card appears
immediately in Inbox (no Lead review). Reject with note "keep lowercase": Lead's DM shows the
Decision; `git status` still shows README.md modified.

## 5. Blocker
DM qa: `Post a Blocker in #general mentioning @master saying the test database is missing, then end.`
Expect: Inbox → Messages shows it; Resolve removes it (count back to 0).

## 6. Hygiene
`pytest -q` passes; no `__pycache__` outside .venv/node_modules; `~/.claude/settings.json`,
`CLAUDE.md`, `remote-settings.json` sha256 unchanged.
