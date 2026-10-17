# M2c end-to-end verification (real `claude -p`, Opus, ~$2–4)

Prelude — **commit M2c first** (the `aiteams/static` removal included): the snapshot is HEAD, and an
uncommitted M2c would install the pre-M2c tree. Node ≥ 22.12 with npm on PATH (`node --version`).
`B=http://127.0.0.1:7700`, `T="Authorization: Bearer $(cat ~/.aiteams/token)"`.

## 1. The upgrade that crosses into M2c builds the UI
- `git ls-files aiteams/static` prints nothing.
- The installed `aiteams` is pre-M2c (no build step), so run the checkout's CLI once:
  `uv run --no-sync python -B -m aiteams.cli upgrade` → output has `snapshot ...`, then npm's own output, then
  `ui        built with node v<x> in <n>s`, then `stop`, `install`, `record`, `upgraded to <sha7>`.
- `aiteams status` → `runtime: ... <sha7> (HEAD) ...`, no `upgrade available`.
- `ls "$(uv tool dir)/aiteams/lib/python3"*/site-packages/aiteams/static` → `assets index.html`.
- `ls ~/.aiteams/runtime/*/ui` has no `node_modules`.
- `aiteams` in this repo → the app opens in the new kit (Radix pickers, lucide icons).

## 2. Aborts cost no downtime
- `env PATH=/usr/bin:/bin "$(uv tool dir --bin)/aiteams" upgrade` → `upgrade aborted: Node >= 22.12 and npm are
  needed to build the UI (found: no node, no npm); install Node 24 LTS, e.g. `brew install node@24``, exit 1.
  `aiteams status` → daemon up, same sha, same pid as before.
- Throwaway branch with a TS error: `git switch -c m2c-broken && echo 'const x: number = "a"' >> ui/src/main.tsx &&
  git commit -qam broken && git switch -`, then `aiteams upgrade m2c-broken` → tsc error streamed,
  `upgrade aborted: npm run build failed (2)`; `aiteams status` unchanged. `git branch -D m2c-broken`.

## 3. Rollback to a pre-M2c ref and forward again
- `aiteams upgrade 969f048` → `ui        prebuilt in snapshot`, the old UI is served (native `<select>` pickers).
- The installed CLI is now the pre-M2c one: forward with `uv run --no-sync python -B -m aiteams.cli upgrade` →
  `ui        built with node ...`, new UI again.

## 4. QA tooling
- `cd ui && npm ci && npm run e2e:install && npm run e2e` → `24 passed` (Chromium headless shell).
- `grep -rE 'text-\[[0-9.]+px\]|text-(xs|sm|base|lg|xl)\b' ui/src` and `grep -rn '<select' ui/src` → nothing.
- `npm run shot -- /p/demo "/p/demo?c=feature-1" "/p/demo?c=inbox" "/p/demo?c=inbox&review=1" "/p/demo?c=dm-frontend&tab=live"`
  → five PNGs in `ui/test-results/shots/`; they look like the `docs/` screenshots (same flat light look).
- While e2e runs, `aiteams status` shows the live daemon untouched (the sandbox is on 8790).

## 5. Real team turn on Opus (this repo)
- From the UI, New task to Frontend (starter Lead is fine): "Tooltip on the sidebar version line showing the full
  sha and ref".
- Expect in Frontend's Live session: an edit using `ui/src/components/ui/tooltip`, a new or extended spec in
  `ui/e2e/`, `npm run e2e` and `npm run shot -- /p/demo` run in the turn; a commit request whose body ("how tested")
  carries `N passed` and the PNG path, and no `aiteams/static` path. A request naming `aiteams/static/...` is
  refused with "`aiteams/static/...` is gitignored (build output is not committed)".
- The Lead re-runs `npm run e2e` before approving; the Master approves from the Inbox; `aiteams upgrade` builds it and
  the tooltip shows in the app.

## 6. A real approval toast
- Ask any role in its DM to `request_approval` for anything. Expect the Sonner toast bottom-right in the F-1 rev3 look
  (accent bar, bell icon with the ring pulse, "APPROVAL NEEDED", full text on hover of the clamped body, × to
  dismiss, countdown bar), gone after 15 s (pauses while the pointer is over the stack); a click opens the
  conversation.
