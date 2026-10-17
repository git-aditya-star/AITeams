# M1a end-to-end verification

Runs against the real `claude` CLI on the configured model (default Opus). Costs real Team-plan quota.

## 0. Build and install (ask the Master before installing)
```
cd ui && npm install && npm run build      # -> aiteams/static/
cd .. && uv sync && uv tool install -e .   # `aiteams` on PATH
```

## 1. Start
```
export TMPREPO=$(mktemp -d)/demo && mkdir -p $TMPREPO && cd $TMPREPO && git init -q && echo "# demo" > README.md && git add -A && git -c user.name=t -c user.email=t@t commit -qm init
aiteams
```
Expect: daemon starts (`~/.aiteams/daemon.json` has pid+port), the browser opens `/p/<id>`, the sidebar
shows #general + 6 DMs, `$TMPREPO/.aiteams/state.db` exists and `.git/info/exclude` contains `.aiteams/`.

## 2. Manager -> Backend delegation (#general)
Send: `@manager ask @backend to list the files in this repo and reply here`
Expect: Manager turn runs (green dot, live events), Manager posts a message mentioning backend,
Backend turn runs, Backend posts the file list in #general, Manager acks. Receipts under the Master's
message show manager + backend circles moving queued -> seen -> acked. Both turns show cost and context
tokens in the Live session tab.

## 3. AskUserQuestion (Product DM)
Send: `Use AskUserQuestion to ask me which database we should use (Postgres or SQLite), then write the
answer into DB_CHOICE.md in the repo root.`
Expect: a question card with two buttons appears during the turn; click Postgres; the turn continues;
`$TMPREPO/DB_CHOICE.md` contains Postgres; `GET /api/p/<id>/questions?state=answered` lists it.

## 4. Hook deny (Backend DM)
Send: `Run git commit --allow-empty -m test and tell me what happened.`
Expect: Live session shows the hook deny (`AITEAMS_DENY`), `git log` has no new commit, Backend reports
the denial in its DM.

## 5. Pause
Pause the project; DM QA `Reply OK`: the wake stays queued (amber dot). Unpause: it runs.

## 6. Crash recovery and graceful stop
While a turn runs: `kill -9 $(jq .pid ~/.aiteams/daemon.json)`; `pgrep -f 'claude -p'` still shows the
orphan; run `aiteams` again: the turn is `failed`, the orphan is gone, the wake is re-queued and runs once.
Then, while a turn runs: `aiteams stop`: the turn becomes `interrupted` (SIGINT result in the Live
session), `pgrep -f 'claude -p'` is empty, and after `aiteams` the wake runs once more.

## 7. Hygiene
`pytest -q` passes; `find . -name __pycache__` (outside .venv/node_modules) is empty;
`~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `~/.claude/remote-settings.json` have the same sha256
before and after (claude writes transcripts under `~/.claude/projects/`, which is expected).

## Handy API calls (replace PID)
```
curl -s localhost:7700/api/p/PID/team | jq '.roles[] | {role, state, context_tokens, cost_today}'
curl -s "localhost:7700/api/p/PID/messages?conversation=general" | jq '.[] | {author, type, text, receipts}'
curl -s "localhost:7700/api/p/PID/turns?limit=10" | jq '.[] | {id, role, state, cost_usd, context_tokens, error}'
curl -s localhost:7700/api/p/PID/questions | jq .
curl -s -X POST localhost:7700/api/p/PID/questions/QID/answer -H 'content-type: application/json' -d '{"answers": {"<question text>": "Postgres"}}'
```
