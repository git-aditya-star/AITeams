# AITeams — pending improvements

This is the open backlog. Every item is wanted and not built yet (checked against the code after F-26, 2026-10-18).
Each item lists only what is already decided and where the code is today; Product, the Lead and the builders work out
the rest. The team picks items from here, and the Master writes each task's text in the UI. "Parked" items at the end have no decision yet.

Tags: `[new req]` is new behaviour, `[improvement]` makes something that exists better, `[bug]` is a bug. Suggested order
(what is left of the M2c plan): diff viewer, design-change broadcast, Live session redesign, mockups/screenshots, Inbox
guidance, message types, upgrade System line, hook false positives. The other items have no order.

## Review & commits

**Commit review diff → `@pierre/diffs`** `[improvement]`
- Decided (M2c stack choice): replace the hand-rolled viewer with `@pierre/diffs` (Apache-2.0; npm 1.5.2 on
  2026-10-18). It takes the raw multi-file patch and adds Shiki highlighting, word-level changes, a split/unified toggle
  and hunk expansion. F-3 left exactly these out of scope.
- Keep the F-3 summary header (feature, what/why/how tested, Lead verdict + note, stale and overlap warnings). Built,
  binary, unchanged, missing and large (>300 changed lines) files stay folded by default.
- Today: `GET /api/p/{pid}/commits/{cid}/diff` (`aiteams/api/features.py:112` → `workflow.commit_diff`/`_diff_sync`)
  already returns the raw patch as `diff`, next to `files`, `totals`, `stale` and `overlap`. UI: `CommitReview.tsx` is the
  full-width overlay (`?review=<id>`). `DiffFile.tsx` (with the `foldedByDefault` rule) and `ui/src/diff.ts` are the
  parts this replaces.

**Mockups and screenshots in the review flow** `[new req]`
- Why: for UI work the Master approves the scope, design and commit without seeing the result. The first look comes
  after `aiteams upgrade`. F-1's toasts went through three looks, and each look cost a commit and an upgrade.
- Mockups are optional and never required for a UI scope lock or design approval. Product or the Lead puts a static
  HTML page or a PNG in `.aiteams/features/F-<n>/mockups/`. The approval card and the Docs tab render it: HTML in a
  sandboxed iframe, PNG inline. When the choice is a matter of taste there are two or three variants (look A/B/C), and the
  Master's pick is recorded as the Decision.
- Screenshots of the real build: `request_commit` gets optional image paths (from `npm run shot`), and the commit review
  shows them next to the diff.
- Why this is allowed: headless roles have no Artifact tool, and local HTML artifacts were rejected. A file in the
  feature folder, rendered by our own UI, is neither of those, and it stays inside the project.
- Today: `workflow.docs_list` lists only `*.md`. Rendering is in `FeatureDocs.tsx` and `ApprovalCard.tsx`.
  `request_commit(feature, files, message, requires)` lives in `aiteams/mcp_server.py` → `workflow.request_commit`. PNG
  paths are only text in "how tested" (team-rules). `npm run shot` writes `ui/test-results/shots/<slug>.png`
  (`ui/e2e/shot.mjs`).

**Inbox guidance when several commits wait** `[improvement]`
- Why: with three commit cards waiting, the Master had to ask the Lead in chat which ones to approve.
- Already built: the Lead verdict badge with its note on hover (F-7). A re-filed request supersedes the old one and
  posts a System line (F-7). Linked requests form one commit group, committed in dependency order (F-11). The overlap
  list exists, but only in the full review view (F-3).
- Remaining:
  1. The Inbox card itself says when its files overlap another pending request.
  2. An "Approve all reviewed" action when the Lead-approved requests share no files.
  3. A superseded request reads as superseded. Today it shows a red "Rejected" chip with the note "superseded by #n".
- Not wanted: a suggested order across independent requests. The Master chose no global commit queue (F-11); order is
  enforced only inside commit groups.
- Today: `InboxView.tsx`, `CommitCard.tsx`, `CommitGroupCard.tsx` and `leadReview.ts`. `workflow._overlap` builds the
  overlap list. `workflow.request_commit` does the supersede by setting `state="rejected"` with `note="superseded by #n"`.

**Commit squash / amend** `[new req]`
- Deferred since M1b. Only the want is recorded. The standing rules still apply: roles never run `git commit` (the hook
  denies it), and every commit is a daemon action in the Master's git identity that the Master approves.
- Today: one approved request is one commit. `workflow._commit_sync` runs `git add -- <files>` and
  `git commit -F <msg> --only -- <files>`.

## Conversations & messages

**Design-change broadcast** `[new req]`
- Why: during F-3 the Lead changed `design.md` after the builders had started. Both builders filed requests on the old
  shape, both requests were rejected and both were re-filed. Nothing told them the doc had moved.
- Decided: when a feature doc changes, a System line goes into the feature conversation ("lead updated design.md") and
  the feature header shows "design updated HH:MM". No auto-wake: the Lead @mentions the builders when they must act now.
- Already built: wake prompts list each doc with its last editor and age (`workflow.feature_header`). F-21 refuses
  commit requests until the latest `Design:` approval is approved, so a redesign that asks for a new approval gates
  commits again. The header chip shows the approval state live (F-21/F-25).
- Gotchas:
  - Edits are recorded by the PreToolUse lock check on every Edit/Write, before the write runs (`workflow.acquire_lock`
    → `db.touch_edit`, table `file_edits`). A naive System line would post once per edit, and Bash writes are never seen.
  - F-16's mid-turn news drops System lines that don't reference a commit (`workflow._news`, `_commit_ref`). This line
    must pass that filter to reach a role that is mid-turn.

**"Upgraded to <sha7>" System line** `[new req]`
- Why: QA's live-UI checks wait for `aiteams upgrade`, which only the Master runs, and roles can't tell when it happened.
  The sidebar banner ("New commits since the running version") is for the Master only.
- Decided: after an upgrade, each feature conversation whose commits became live gets a System line: "Upgraded to
  <sha7>, F-n commits are live".
- Gotchas:
  - The F-16 System-line filter above applies here too.
  - Rollback exists (`aiteams upgrade <older ref>`), so "live" can go backwards.
- Today: `aiteams/cli.py:241` prints "upgraded to <sha7>" only in the terminal. `/api/health` reports `sha`, `ref` and
  `source_head` (`aiteams/runtime.py`). Each landed request stores its `sha` (`commits.sha`). Roles see no version
  anywhere (not in prompts, not in `/internal`).

**Message types** `[improvement]`
- Problem (still current): roles post `Handoff` when they hand work to the next role. By F-26 there were 70 such
  messages in feature conversations, but the type means a session handoff (`write_handoff`, posted in the role's DM). The
  first dogfood run also used `Decision` for a QA report. The Inbox and toasts key off types (a `Question` or `Blocker`
  that @mentions the Master), so misuse can hide things.
- Decided: either add types (e.g. `Report`, `Handover`/`Done`) or tighten the team-rules wording. The team picks.
- Today: the types are `MESSAGE_TYPES` (`aiteams/api/internal.py:20`) and the `post_message` enum
  (`aiteams/mcp_server.py:30`). The UI union is in `ui/src/api.ts`, the rules are at `aiteams/roles/team-rules.md:14`,
  and the toast mapping is in `ui/src/toasts.ts`.

**Master images and screenshots in chat** `[new req]`
- Deferred since M1a. The Master attaches an image to a message (paste or pick), and the role woken by it sees the
  image.
- Unverified since M0: whether image blocks in a stream-json user message work in `claude -p`. Probe that first. The
  wake prompt is already sent as a list of content blocks (`claude_proc.py:147`), so an image block would sit next to
  the text block.
- Today: `ChatBox.tsx` sends text only (`api.sendMessage`), messages are text rows in SQLite, and prompts are built in
  `aiteams/orchestrator/prompt.py`.

**Auto TL;DR of conversations** `[new req]`
- Planned for M2+ in the first plan, next to standup and digest (both exist now). Only the want is recorded: a short
  summary of a long conversation for the Master.
- Today: wake-prompt digests and on-demand standups exist. The only summary is the PRD's `**Summary:**` line on the
  Dashboard's Shipped panel (`workflow.feature_summary`, F-17).

## Live session

**Live session redesign** `[improvement]`
- Why: the view is a debug log, not something the Master can follow.
- Decided:
  - **Tool calls by kind, not JSON.**
    - Bash: the command in a code line, with the output folded under it (exit status, duration, stderr in red).
    - Edit/Write: the file path and a small +/- diff.
    - Read/Grep/Glob: the path or pattern and a count ("12 lines", "3 matches").
    - AITeams MCP tools: one human sentence ("posted Finding to F-1 @lead", "requested commit #3, 8 files"), payload
      folded.
    - AskUserQuestion: the question card.
    - An icon or colour per kind, so the eye can scan.
  - **Folded by default:** thinking (one line) and tool results (a one-line summary). Assistant prose is always visible.
    Expand all / Collapse all at the top, plus a filter: prose only / tools / denied.
  - **A readable turn header** replaces the resume command: role, turn #, kind, state, start and duration, context %,
    the wake reason ("woken by @mention in F-1 from lead", linked), and the prompt behind a disclosure. The raw
    `claude --resume` command moves to a "…" menu. No cost figures (F-8).
  - **Turn list:**
    - grouped by session, with fresh-session boundaries visible;
    - one line per turn saying what it did (its first tool or first prose sentence);
    - failures and denies highlighted;
    - a follow-live toggle, and a "jump to latest" pill when scrolled up.
  - **Allowed:** shadcn's agent Trace / Code Block patterns, and Shiki. Use the kit components and the five type tokens
    only; mono only for commands, paths and code.
- Today: `ui/src/components/LiveSession.tsx`.
  - It has a 240px turn list, prose in `<pre>`, grey thinking, `ToolName {json}` previews (200 chars), tool results
    (300 chars), red-border denies, a `result` line, and the `--resume` command plus a Prompt toggle in the header.
  - Events come from `turn_events`, with live frames through the WS fan-out in `App.tsx`.
  - The wake reason can come from `turns.origin`/`conversation_id`/`wake_id` → `wake_queue.message_ids`.

## Dashboard

**Past stats on the Dashboard** `[new req]`
- Why: the Dashboard shows what is happening now, not how the team has been doing.
- Decided: per project, with a range of today / 7 days / all, shown as small flat tiles plus one or two trend lines:
  - features done, and their cycle time (created → scope lock → first commit → done);
  - commits landed vs rejected, and by whom;
  - Lead review rounds per request;
  - approvals, and the Master's wait time (how long items sat in the Inbox);
  - turns per role, with interrupted and failed counts;
  - standups held;
  - rotations and fresh sessions.
- The History view gets the per-feature breakdown. This is query and UI work only: the data is already in SQLite
  (`features`, `commits`, `approvals`, `turns`, `activity`, `standups`, `sessions`). No cost or usage panel unless the
  Master asks for one.
- Already built: the Shipped panel (done this week, total, recent; F-17). F-18 added a 24-hour activity spark (messages
  and commits), a 14-day shipped timeline, the phase bar, team rings and needs-you tiles. F-18 drew its charts by hand in
  SVG with no chart library, to be revisited "if a later feature needs axes, zoom or real time series".
- Today: `workflow.dashboard`/`shipped`/`activity_hourly` (`aiteams/workflow.py` ~1234–1282) and
  `db.shipped`/`done_since`/`activity_times`. UI: `DashboardView.tsx`, `ui/src/components/dashboard/*` and
  `HistoryView.tsx`.

## Projects & settings

**Multi-repo projects** `[new req]`
- From the first plan: a project can span several linked repos (e.g. one product split across microservice repos), and
  roles work across all of them. Repos get linked in three ways:
  - set in the app;
  - said in chat;
  - found by an agent that reads the project docs or notes and asks the Master to confirm.
- New task gets an optional "repos" field.
- Everything assumes one root today. These need working through:
  - The hook denies Edit/Write and `rm -rf` outside `AITEAMS_PROJECT_PATH` (`inside_project` in
    `aiteams/hooks/pretooluse.py`).
  - `claude -p` runs with the project as cwd.
  - Commits run `git` in the project path (`workflow._commit_sync`).
  - Locks and `file_edits` store project-relative paths.
  - The `.aiteams/` + `.git/info/exclude` setup is per repo.
- Today: `~/.aiteams/projects.json` holds `{id, path, name, registered_at}`, one path per project
  (`config.register_project`).

**Cross-project views** `[new req]`
- Out of scope since M1c (marked "M2+"). Only the want is recorded. Standing decisions:
  - each project is its own workspace with its own team and chat ("nothing is mixed");
  - the per-project Dashboard stays the landing page;
  - one daemon and one global concurrency cap already serve all projects.
- Today: the sidebar project picker switches projects, `/` redirects to the first project (`ui/src/Home.tsx`), and
  `GET /api/projects` lists them.

**Settings UI for the remaining global settings** `[improvement]`
- Already built: model and effort per role (F-9) and the Plugins tab (F-23), both in `RoleSettingsDialog.tsx`.
- Remaining, which F-9 left out on purpose: `max_concurrent`, `ask_timeout_s`, `max_turns`, `turn_timeout_s`,
  `rotate_pct`, `stale_hours`, `fail_streak_rotate`, `port`. Settings stay global, not per project (F-9).
- Today: the defaults are `DEFAULT_SETTINGS` in `aiteams/config.py`. `PUT /api/settings` (`aiteams/api/public.py:149`)
  saves any key but validates only the role fields (`config.validate_role_settings`), then notifies the scheduler.

## Roles & runtime

**Roles generate Claude artifacts** `[new req]`
- The Master (2026-10-16): role artifacts "should get generated" too. Deferred since M1c.
- Known:
  - The Artifact tool does not exist in headless `claude -p`. The M0 probe on claude 2.1.301 found it missing on
    haiku/sonnet/opus/default, even with `enableArtifact: true`, `--tools` or connectors on.
  - The claude.ai Docs connector connects headless, but creating a doc that way was never tested.
  - Role turns run with `ENABLE_CLAUDEAI_MCP_SERVERS=false` and `--strict-mcp-config` (`child_env`/`build_cmd` in
    `aiteams/orchestrator/claude_proc.py`), so connectors are off. That isolation was chosen partly so that role turns
    don't load every claude.ai connector (mail, chat). Re-probe on the current CLI first.
- Current fallback: the Live session header shows `claude --resume <role session id> --setting-sources local`. The
  Master runs it in their own terminal and asks for the artifact there, only while the role is idle (two writers on one
  session clash).
- Rejected earlier: a local HTML "artifact" feature inside AITeams. Mockups in the feature folder are a separate item
  (above).

## Hook & safety

**Hook false positives** `[bug]`
- (a) `KILL_RE` denies any `kill`/`pkill` whose text contains the substring `aiteams`. QA's
  `pkill -f "qa/sandbox.py 8791"`, for a script under `.aiteams/features/F-1/qa/`, was denied 8× as "killing the AITeams
  daemon". Decided: match the daemon's pid or the `aiteams` process name, not the substring.
- (b) `rm -rf` outside the project is denied, so QA's Chromium profiles and sandbox state were left in `/tmp/qa-f1`.
  Decided: a sanctioned scratch area. Either allow `rm -rf` under `$TMPDIR/aiteams-<role>-*`, or give each turn an
  `AITEAMS_SCRATCH` dir that the hook whitelists and the daemon cleans at turn end. Edit/Write outside the project are
  denied as well (`check_write`).
- Stays as is: the rules about the daemon pid, port, token and runtime. The threat model is best-effort against
  accidental misuse, since roles run as the Master's macOS user.
- Today: `aiteams/hooks/pretooluse.py:17-18` (`RM_RE`, `KILL_RE`) and the check at line 82 in `check_bash`. The turn env
  is set in `claude_proc.child_env`. `scripts/qa_sandbox.py` creates its temp dir with `mkdtemp(prefix="aiteams-qa-")`.

## Docs & cleanup

**User guide and docs media catch up** `[improvement]`
- M2c regenerated `docs/*.jpg` but not `docs/walkthrough.gif`, which still shows the pre-M2c UI. The JPGs predate the
  visual Dashboard, the New task modal, the cards and Role settings.
- `docs/GUIDE.md` has had only its e2e section updated since M2c (F-11, F-22). Most features since F-4 are missing
  from it: role settings and plugins, question/brainstorm cards, reactions, commit groups, design approval, Reject, two
  Leads, the Shipped panel and the visual Dashboard.
- Today: screenshots come from the QA sandbox through `npm run shot`, and `README.md` links the GIF and the guide.

**Drop the unused `workflow.design_state()`** `[improvement]`
- F-21's design says to "drop it in a later small change". The only definition is at `aiteams/workflow.py:72`, and
  nothing calls it (`design_info`/`design_line` are what's used).

## Parked (no decision yet; ask the Master before picking these up)
- **UI Upgrade button.** M2a turned it down for M2a with "the daemon replacing itself is M2b material", and M2c left it
  out again. Today `aiteams upgrade` is CLI-only, the hook denies it to roles, and the sidebar banner tells the Master to
  run it.
- **Dev tooling:** ruff, Biome/ESLint/Prettier, a `check.sh`, pre-commit hooks. The Master said "not now" (M2c).
- **Stale-wait re-wake, one feature per role turn, queue time per role.** All six roles proposed these cheap fixes in
  the F-22 brainstorm:
  - the daemon re-wakes whoever is responsible when a commit in Lead review, a card, an approval or a live QA check has
    waited longer than N minutes (#65 sat 8 h in Lead review because of a missed wake; the Lead approved it within a
    minute once chased);
  - each role turn works on one feature;
  - a Dashboard number, "queued time waiting for this role" over 24 h, from the turn and wake rows.
  
  The Master chose two Leads instead (F-22) and left these as "separate features if wanted".
