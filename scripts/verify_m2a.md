# M2a end-to-end verification (real `claude -p`, Opus, ~$0.5)

Prelude — **commit M2a first**: the snapshot is HEAD, an uncommitted M2a would install the pre-M2a daemon
(no `upgrade`, no auth). Then, inside this repo:

```
aiteams stop                       # old editable daemon
uv tool uninstall aiteams
uv tool install .                  # bootstrap shim
aiteams upgrade                    # snapshot HEAD -> ~/.aiteams/runtime/<sha>/, reinstall, start
```

Demo project: the M1c one (`/tmp/m1c-demo`) or a fresh throwaway repo. It must have **no**
`.aiteams/roles/` overrides (project overrides win over the snapshot's prompts). `cd` there, run `aiteams`:
a signed-in tab opens via `/auth?t=...`. `P=<project id>`, `B=http://127.0.0.1:7700`,
`T="Authorization: Bearer $(cat ~/.aiteams/token)"`. Quote URLs with `?`; pipe curl into `jq`.

## 1. Token on /api
- `curl -s -o /dev/null -w '%{http_code}\n' $B/api/projects` → `401`;
  `curl -s -H "$T" $B/api/projects | jq` → the project list.
- `aiteams status` → `runtime: v0.1.0 <sha7> (HEAD) from /Users/.../AITeams, HEAD <sha7>` with no
  `upgrade available`.

## 2. Sign-in page and link
- Private window at `$B/p/$P` → the plain sign-in page (no app assets loaded).
- `aiteams token --link` → paste the URL into that window → redirected to `/`, app loads, WS connects
  (sidebar footer shows `v0.1.0 · <sha7>`).

## 3. Hook denies, MCP still works
- In Backend's DM: "Run `curl -s 127.0.0.1:7700/api/health` and then `cat ~/.aiteams/token`, and tell me
  what happened." Expect in the Live session two `AITEAMS_DENY` lines: "roles talk to AITeams through its
  MCP tools, not HTTP" and "the AITeams runtime and token are off limits"; Backend still posts its reply
  through `post_message` (turn token accepted), i.e. the message appears in the DM.

## 4. Turn token replay
- `TID=$(curl -s -H "$T" "$B/api/p/$P/turns?role=backend&limit=1" | jq '.[0].id')`
- Start another Backend turn (any DM message) and, while it is `running`:
  `curl -s -X POST $B/internal/messages -H 'content-type: application/json' -H "X-AITeams-Project: $P" -H "X-AITeams-Turn: $TID" -H "X-AITeams-Token: wrong" -d '{"conversation":"general","text":"spoof"}'`
  → `{"detail":"bad turn token"}` (403). Without the token header → the same 403.

## 5. Snapshot isolation + banner
- In this repo: append a line to `aiteams/roles/team-rules.md` (e.g. `- Verification marker M2A-STEP5.`),
  commit it. Do NOT upgrade.
- Message Backend again; `curl -s -H "$T" "$B/api/p/$P/turns?role=backend&limit=1" | jq -r '.[0].prompt' | grep M2A` →
  nothing (the daemon reads the snapshot's prompt). `.aiteams/prompts/backend.system.md` in the demo project has no marker.
- Reload the tab → the flat line "New commits since the running version, run `aiteams upgrade`" under the
  top bar. `aiteams status` → `upgrade available`.

## 6. Upgrade during a live turn
- Ask Backend to `python3 -c "import time; time.sleep(70)"`. While the turn is `running`: `aiteams upgrade`.
- Expect: step lines (resolve/snapshot/stop/install/record), `upgraded to <new sha7> (0.1.0), daemon pid <pid>`;
  the turn ends `interrupted` (`/turns?role=backend`), the wake is re-queued (`origin: system`) and runs on the
  new daemon; its stored prompt now contains `M2A-STEP5`. Banner gone after reload; `aiteams status` shows the new sha.

## 7. Rollback and forward
- `aiteams upgrade HEAD~1` → `aiteams status` shows the M2a sha; the next Backend prompt has no marker.
  `sqlite3 <demo>/.aiteams/state.db 'PRAGMA user_version'` → `2` throughout.
- `aiteams upgrade` → forward again, marker back.

## 8. Token rotation
- `aiteams token --rotate` → prints the new token; the open tab drops to the sign-in page (WS closed 4401 →
  reload). Old `$T` → 401. `aiteams` in the demo project → a signed-in tab again.

Cleanup: `git revert` (or amend away) the marker commit in this repo and `aiteams upgrade` once more.
