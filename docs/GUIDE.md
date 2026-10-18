# AITeams user guide

How to run a project with the team, screen by screen. Read the [README](../README.md) first for what
AITeams is and how to install it. The [walkthrough GIF](walkthrough.gif) shows the tour below.

## 1. Start

```bash
cd ~/code/my-project
aiteams
```

The first run starts the daemon in the background (`127.0.0.1:7700`), registers the folder as a
project and opens the browser on that project's **Dashboard** through a single-use sign-in link
(`/auth?t=...`, valid 60 s) that sets the session cookie. Running `aiteams` in another repo adds
a second project; the project picker is at the top of the sidebar. `aiteams status` and `aiteams stop`
manage the daemon. A tab without the cookie (a private window, another browser) shows a plain sign-in
page: run `aiteams` again, or paste the URL from `aiteams token --link`.

The daemon runs from a **snapshot** of the AITeams repo, not from the checkout (`README` → Install).
The sidebar footer shows `v<version> · <sha>`; when the source repo has newer commits a line under
the top bar says so, and `aiteams upgrade` installs them (`aiteams upgrade <older ref>` goes back).
Upgrading builds the UI inside the snapshot first (`npm ci` + `npm run build`, Node ≥ 22.12), then stops
the daemon: running turns end `interrupted` and are re-queued on the new daemon. A missing Node or a
failed build aborts before the stop, so the old daemon keeps running. A rollback to a ref from before
M2c uses the build committed in that ref (`ui        prebuilt in snapshot`); rolling forward from there
needs the checkout's CLI once: `uv run --no-sync python -B -m aiteams.cli upgrade`.

What the first run creates inside the repo:

```
.aiteams/state.db          all state (messages, turns, features, locks...)
.aiteams/features/F-<n>/   the living docs of each feature
.aiteams/handoffs/<role>/  handoff notes written when a session is retired
.aiteams/standups/         compiled standup reports
.aiteams/prompts/          the system prompt each role was last given (for debugging)
```

The folder is added to `.git/info/exclude`, so nothing of this is ever committed.

## 2. The screen

- **Sidebar:** Dashboard, Inbox (with a badge), the primary **New task** button, channels
  (`# general`, `# standup`), active features with their phase chip, **History**, one direct message
  per role (dot = idle/queued/running, bar = context meter), and a **Pause project** checkbox.
- **Team strip** (top): each role's state, context % and today's cost. Click a pill to open its DM.
- **Main pane:** whatever is selected. Every conversation has a chat box; `@` autocompletes roles.
  **Enter** sends, **Shift+Enter** adds a line.

### Read receipts
Under every message: stacked avatars grouped by state with a small icon — check = acknowledged (the
role replied in that conversation), eye = seen (its turn started), clock = queued (woken, not started). Max three per group, then
`+N`. Always visible, never hidden behind a click.

## 3. Give the team work: New task

Click **New task**, describe the work in plain words, pick who starts:

- **Product** (default) for anything that needs a PRD or scope decision.
- **Lead** for small, clear engineering fixes.
- **Manager** when you want it routed for you.

This creates feature `F-<n>` in phase `intake`, opens its conversation and wakes the starter. The
feature header shows the phase and owner (both editable by you and by any role), **Archive**,
**Fresh start**, and the **Chat / Docs** tabs. Docs lists the feature's files with who last edited
them and when; click one to read it.

## 4. Who gets woken, and when

One rule: **@mention or DM = wake**. A message that mentions nobody wakes nobody, except your own
untagged messages in a channel or feature, which go to the Manager. Roles only talk through the chat,
so everything they do is visible. A running role is never interrupted by a new message; it gets the
new messages at its next wake as a digest.

At most three roles run at once across all projects (`max_concurrent`). Your messages jump the queue.

## 5. The Inbox

Only what needs you lands here:

| Card | Comes from | You do |
|---|---|---|
| **Question** (with options) | a role's `AskUserQuestion` | click an option or type an answer; the role continues in the same turn |
| **Approval** | `request_approval` (scope lock, scope change, Done, risky change) | Approve / Request changes, optional note, optionally set the phase |
| **Commit** | a commit request the Lead already approved | read the diff, edit the subject if you like, Approve → the daemon commits in your git identity |
| **Question / Blocker message** @you | a typed message | reply in the conversation, then **Resolve** |

Cards also appear inline in the conversation where they were raised.

## 6. Commits, locks and safety

- Roles cannot commit or push; a hook denies it. They call `request_commit` with the exact files and a
  message `F-<n>: subject` plus body. The Lead reviews first (the Lead's own requests skip review).
  Only Lead-approved commits reach your Inbox. The daemon commits only the listed files.
- Two roles never write the same file at the same moment: an edit locks the file to that role for the
  rest of its turn. The feature header lists current locks; ✕ releases one. Lead and Manager can also
  break locks with a tool.
- Roles work on the repo's current branch. No AI branches, no worktrees.

## 7. Watching a role: direct messages and the Live session

Each role's DM has two tabs. **Chat** is a private line to that role (everything you write there
wakes it). **Live session** shows its turns (state, cost, tokens, kind) and, for the selected turn,
the raw Claude Code stream: tool calls, file diffs, command output. **Prompt** shows the exact text
the daemon sent for that turn. The header shows a `claude --resume <id>` command you can run in your
own terminal, safe only while the role is idle.

## 8. Keeping sessions small: handoffs and fresh starts

A role's session is disposable; its role is permanent. The daemon measures each turn's context use:

- Past **60%** (`rotate_pct`), the role gets a **handoff turn**: it writes what it was doing, the exact
  state, decisions and next steps to `.aiteams/handoffs/<role>/<n>.md` and posts it in its DM as a
  `Handoff` message. Its next wake starts a fresh session whose prompt begins with that handoff.
  The DM shows `Fresh session #n (reason)`.
- **Fresh start** (DM title bar or Dashboard → Team): force the same thing now. Tick **skip handoff**
  when the role is confused or looping and its memory is the problem.
- **Stop** (visible while a role runs): interrupts the turn immediately. The role is told it was
  stopped and continues from the files at its next wake. Nothing is re-queued.
- Three failed turns in a row rotate the role automatically.
- The Manager has `interrupt(role)` and `checkpoint(role)` tools for the same two actions.

## 9. Standup

Click **Standup** on the Dashboard or in `# standup`. The daemon wakes only the roles that did
something since the last standup, each posts **Done / Next / Blocked**, and the Manager compiles a
report (`.aiteams/standups/<ts>.md`, posted as a `Standup` message). If nobody was active you get
"nothing changed" and no one is woken. The Manager may also call a standup on its own after a
milestone or when you ask what is going on. There is no schedule.

## 10. Feature fresh start and History

**Fresh start** in a feature header offers two modes:

- **Keep docs:** the conversation gets a divider; roles only see messages after it, the docs stay.
  Use it when the chat has become noise but the spec is good.
- **Start over:** docs are archived to `F-<n>/archive/<ts>/`, pending approvals and commits are
  cancelled, the phase goes back to intake and the original request is re-posted to the starter.

Features whose phase is `done` or that you archived move to **History** (sidebar). They stay
readable and writable; a role can set the phase back to reopen one.

## 11. Dashboard

The landing page per project:

- **Needs you:** counts of questions, approvals, commits and messages waiting on you (links to the Inbox).
- **Features:** counts by phase and a table with phase, owner, **waits on** (derived: you, the Lead, a
  running/queued role, or the last @mentioned role) and a **stale** chip after `stale_hours` (4) of
  silence.
- **Team:** state, current conversation, context meter, session number, fresh starts, Stop and Fresh
  start per role.
- **Activity:** commits, decisions, phase changes, handoffs, fresh sessions, stops, standups.

## 12. Settings and costs

`~/.aiteams/settings.json`, read on start and changeable live:

```bash
curl -X PUT 127.0.0.1:7700/api/settings -H "Authorization: Bearer $(cat ~/.aiteams/token)" \
  -H 'content-type: application/json' \
  -d '{"default_model": "opus", "models": {"qa": "sonnet"}, "max_concurrent": 3, "rotate_pct": 0.6, "stale_hours": 4}'
```

Every `/api` call needs that bearer header (the browser uses the cookie instead); `GET /api/health`
is the only open route. `aiteams token` prints the token, `aiteams token --rotate` replaces it and
signs every open tab out.

Roles use your Claude plan. A typical turn on Opus costs about $0.15–0.5 of plan usage; the team strip
shows each role's spend today. When the plan's usage window is exhausted the daemon pauses all
projects, shows a banner and resumes when the window resets. **Pause project** (sidebar) stops new
turns for one project without losing queued work.

## 13. Customising the team

Each role's prompt is a Markdown file in `aiteams/roles/` **of the installed snapshot**, so an edit in
the checkout reaches the team only after `aiteams upgrade`. Drop a file with the same name into
`<repo>/.aiteams/roles/` to override it for one project (`team-rules.md` too); project overrides win
over the snapshot and apply at once. The repo's own `CLAUDE.md`, if any, is appended to every role's
system prompt.

### UI development and QA

Working on the UI: `cd ui && npm ci`, then `npm run dev` serves `localhost:5173` and proxies `/api`, `/ws`
and `/auth` to the daemon. Sign the dev origin in by taking the `aiteams token --link` URL and swapping
the host for `localhost:5173`. `npm run build` writes `aiteams/static/` in the working tree for local
checks; it is gitignored and never committed (`aiteams upgrade` builds its own), and a commit request
that lists it is refused.

The UI is built from the shadcn/ui kit in `ui/src/components/ui/` (Radix primitives; add one with
`npx shadcn add <name>`, then move its text sizes onto the type tokens), lucide icons and five text
sizes: `text-title`, `text-body`, `text-meta`, `text-caption`, `text-code`.

QA never uses the live daemon. `scripts/qa_sandbox.py` starts a throwaway one:

```bash
uv run --no-sync python -B scripts/qa_sandbox.py --port 8790   # temp home, project id `demo`, no claude
```

It seeds the project `demo` (features F-1 and F-2, docs, a chat with mentions, a pending approval and
question, a Master-stage commit request with a modified, an added and a binary file, a finished Live
session for Frontend) and adds two test-only routes: `POST /qa/fire {kind}` emits one live event
(`question`, `question_message`, `blocker`, `approval`, `commit_request`, `committed`, `commit_failed`,
`standup`, `stopped`, `interrupted`, `fresh_session`, `rate_limit`, `rate_clear`, `message`) and
`POST /qa/reset` re-seeds. Both need the sandbox token (`qa-sandbox-token` by default). Ports
8790–8799 are reserved for sandboxes.

On top of it, in `ui/`: `npm run e2e:install` once (Chromium headless shell, ~100 MB), `npm run e2e`
(build + the Playwright suite in `ui/e2e/`, which starts its own sandbox) and
`npm run shot -- /p/demo "/p/demo?c=inbox"` (one PNG per route in `ui/test-results/shots/`). Each role gets its
own sandbox port from `ui/e2e/ports.mjs` (manager 8791, lead 8792, lead-2 8798, product 8793, frontend 8794,
backend 8795, qa 8796) and its own `ui/test-results/<role>/` and `ui/playwright-report/<role>/`, so roles can run
e2e at the same time; by hand (no `AITEAMS_ROLE`) e2e and shot both use 8790, and `$AITEAMS_QA_PORT` overrides
both. 8797 and 8799 stay free for QA's live sandboxes. The e2e build swaps files into `aiteams/static/` without
emptying it, so one run's rebuild never breaks another run's sandbox.

## 14. When something looks wrong

- A role did nothing after being mentioned: check its Live session for the turn and its `result`.
  `num_turns: 0` means the CLI flushed a stale notification; the daemon retries once.
- "could not resume session": the daemon starts a fresh session automatically and tells you in the DM.
- Daemon crashed or was killed: on restart running turns are marked failed and re-queued once. If the
  install itself is broken (a bad upgrade), `aiteams upgrade <last good ref> --src ~/AITeams` from any
  folder reinstalls that ref; `~/.aiteams/runtime.json` records what is installed and from where.
- "This project's database is from a newer AITeams": you rolled back past a schema migration. The
  project is shown read-only and no role is woken until you `aiteams upgrade` forward again.
- Roles are told never to touch `~/.claude`, `.aiteams/state.db` or the daemon; the hook denies edits
  outside the project folder, any HTTP call to the daemon's port, reading `~/.aiteams/token` or the
  runtime, and `aiteams upgrade|stop|serve|token` / `uv tool` (best-effort: same macOS user).
- Reset a project completely: stop the daemon and delete `<repo>/.aiteams/`.
