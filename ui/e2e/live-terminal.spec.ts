import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-33: a role's Live session renders like the Claude Code terminal. The sandbox seeds frontend's newest turn with a full demo
// turn in the real stream-json shapes (thinking, Read, Grep, an Edit with structuredPatch, a Write, a denied Edit, a failing and a
// passing Bash, a post_message, system hook events, the result), and `POST /qa/fire {kind:'turn_event'}` streams one more event in.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const LIVE = '/p/demo?c=dm-frontend&tab=live'
const TOOLS = ['Read', 'Grep', 'Edit', 'Write', 'Edit', 'Bash', 'Bash', 'mcp__aiteams__post_message']

// What a class computes to in this build, measured on a probe element.
const probe = (page: Page, cls: string, prop: string) =>
  page.evaluate(
    ([c, p]) => {
      const e = document.createElement('div')
      e.className = c
      document.body.appendChild(e)
      const v = getComputedStyle(e).getPropertyValue(p)
      e.remove()
      return v
    },
    [cls, prop],
  )

const tool = (page: Page, name: string) => page.locator(`[data-term-tool="${name}"]`)
const detailsToggle = (page: Page) => page.getByRole('button', { name: 'Show details' })
const assistant = (text: string) => ({ type: 'assistant', message: { content: [{ type: 'text', text }] } })
const toolUse = (id: string, name: string, input: unknown) => ({ type: 'assistant', message: { content: [{ type: 'tool_use', id, name, input }] } })
const answer = (id: string, content: string, extra: Record<string, unknown> = {}) => ({
  type: 'user',
  message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content }] },
  ...extra,
})

// The seeded turn predates F-9, so it has no spawn command: put one first, as the daemon records it (see role-settings.spec).
async function withCmd(page: Page) {
  await page.route(/\/api\/p\/demo\/turns\/\d+\/events/, async (route) => {
    const res = await route.fetch()
    const events = await res.json()
    if (Array.isArray(events) && events.length && route.request().url().includes('after_seq=0')) {
      for (const ev of events) ev.seq += 1
      events.unshift({ turn_id: events[0].turn_id, seq: 1, ts: events[0].ts, type: 'aiteams_cmd',
        data: { type: 'aiteams_cmd', argv: ['claude', '-p', '--model', 'opus', '--effort', 'high', '--mcp-config', '<json>'] } })
    }
    await route.fulfill({ response: res, json: events })
  })
}

// Make the newest turn look like it is still running (the seeded one is done).
async function running(page: Page) {
  await page.route(/\/api\/p\/demo\/turns\?role=frontend/, async (route) => {
    const res = await route.fetch()
    const turns = await res.json()
    if (Array.isArray(turns) && turns.length) turns[0] = { ...turns[0], state: 'running', ended_at: null }
    await route.fulfill({ response: res, json: turns })
  })
}

test('the transcript is a dark monospace terminal; the turn list and header stay light', async ({ page, qa }) => {
  await qa.open(LIVE)
  const pane = page.locator('[data-term]')
  await expect(pane).toBeVisible()
  await expect(pane).toHaveCSS('background-color', await probe(page, 'bg-neutral-950', 'background-color'))
  await expect(pane).toHaveCSS('font-family', /mono/i)
  await expect(pane).toHaveCSS('font-size', '12.5px') // the text-code token, no arbitrary size
  const list = page.locator('[data-live-session] > div').first()
  await expect(list).not.toHaveCSS('background-color', await probe(page, 'bg-neutral-950', 'background-color'))
  await expect(page.getByText('claude --resume sess-demo-frontend --setting-sources local')).toBeVisible() // the header is kept
  await expect(page.getByText('success · 3 turns · 18s')).toBeVisible() // and so is the result line
  await expect(page.locator('[data-live-session]')).not.toContainText('$') // F-8
})

test('each tool call is one ⏺ line with a ⎿ summary, paths relative to the project', async ({ page, qa }) => {
  await qa.open(LIVE)
  await expect(tool(page, 'Read')).toBeVisible()
  expect(await page.locator('[data-term-tool]').evaluateAll((els) => els.map((e) => e.getAttribute('data-term-tool')))).toEqual(TOOLS) // one block per call
  const summary = (t: Locator) => t.locator('[data-term-summary]')

  await expect(tool(page, 'Read')).toContainText('Read(.aiteams/features/F-1/design.md)') // /work/demo/ is the turn's cwd
  await expect(summary(tool(page, 'Read'))).toContainText('Read 6 lines')
  await expect(tool(page, 'Grep')).toContainText('Grep("function BlockView", ui/src)')
  await expect(summary(tool(page, 'Grep'))).toContainText('Found 1 line')
  await expect(tool(page, 'Edit').first()).toContainText('Update(ui/src/components/LiveSession.tsx)')
  await expect(summary(tool(page, 'Edit').first())).toContainText('Updated ui/src/components/LiveSession.tsx with 2 additions and 1 removal')
  await expect(tool(page, 'Write')).toContainText('Write(ui/src/lib/terminal.ts)')
  await expect(summary(tool(page, 'Write'))).toContainText('Wrote 14 lines to ui/src/lib/terminal.ts')
  await expect(tool(page, 'Bash').nth(1)).toContainText('Bash(cd ui && npm run build)')
  await expect(summary(tool(page, 'Bash').nth(1))).toContainText('built in 2.1s')
  // the glyph is green once the result is in
  await expect(tool(page, 'Read').locator('[data-dot]')).toHaveCSS('color', await probe(page, 'text-green-400', 'color'))
  // the assistant's text is a white ⏺ and Markdown
  await expect(page.locator('[data-term-text]').first()).toContainText('Reading the design notes before I add the button.')
  await expect(page.locator('[data-term-text] [data-dot]').first()).toHaveCSS('color', await probe(page, 'text-white', 'color'))
})

test('a click expands the full output, another collapses it', async ({ page, qa }) => {
  await qa.open(LIVE)
  const read = tool(page, 'Read')
  await expect(page.locator('[data-term-output]')).toHaveCount(0) // collapsed by default
  await expect(read.locator('[data-term-summary]')).toContainText('(click to expand)')
  await read.getByRole('button').click()
  await expect(read.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  await expect(read.locator('[data-term-output]')).toContainText('| endpoint | backend | streams rows |')
  await read.getByRole('button').click()
  await expect(read.locator('[data-term-output]')).toHaveCount(0)
  // a one-line result has nothing more to show, so it is not a button
  await expect(tool(page, 'Bash').nth(1).getByRole('button')).toHaveCount(0)
})

test('an Edit is a red and green diff with real line numbers; a Write shows its first lines', async ({ page, qa }) => {
  await qa.open(LIVE)
  const diff = tool(page, 'Edit').first().locator('[data-term-diff="edit"]')
  await expect(diff.locator('[data-diff]')).toHaveText([/^\s+140\s+│\s+function BlockView/, /^-\s+141\s+│\s+switch \(b\.kind\) \{$/, /^\+\s+141\s+│\s+\/\/ terminal look/, /^\+\s+142\s+│\s+switch \(b\.kind\) \{$/, /^\s+143\s+│\s+case 'text':$/])
  expect(await diff.locator('[data-diff]').evaluateAll((els) => els.map((e) => e.getAttribute('data-diff')))).toEqual(['ctx', 'del', 'add', 'add', 'ctx'])
  await expect(diff.locator('[data-diff="del"]')).toHaveCSS('background-color', await probe(page, 'bg-red-950/70', 'background-color'))
  await expect(diff.locator('[data-diff="del"]')).toHaveCSS('color', await probe(page, 'text-red-300', 'color'))
  await expect(diff.locator('[data-diff="add"]').first()).toHaveCSS('background-color', await probe(page, 'bg-green-950/70', 'background-color'))
  await expect(diff.locator('[data-diff="add"]').first()).toHaveCSS('color', await probe(page, 'text-green-300', 'color'))

  const write = tool(page, 'Write').locator('[data-term-diff="write"]')
  await expect(write.locator('[data-diff]')).toHaveCount(10)
  await expect(write.locator('[data-diff="add"]')).toHaveCount(10)
  await expect(write.locator('[data-term-more]')).toHaveText('… +4 lines (click to expand)')
  await write.locator('[data-term-more]').click()
  await expect(write.locator('[data-diff]')).toHaveCount(14)
  await expect(write.locator('[data-term-more]')).toHaveCount(0)
})

test('without a structuredPatch an Edit falls back to old and new, and a result with no call is a plain ⎿ line', async ({ page, qa }) => {
  await qa.open(LIVE)
  const edit = { file_path: '/work/demo/ui/src/a.ts', old_string: 'const a = 1', new_string: 'const a = 2\nconst b = 3', replace_all: false }
  await qa.fire('turn_event', { event: toolUse('toolu_x1', 'Edit', edit) })
  await qa.fire('turn_event', { event: answer('toolu_x1', 'The file /work/demo/ui/src/a.ts has been updated successfully.') })
  await qa.fire('turn_event', { event: answer('toolu_nope', 'a result nobody asked for\nsecond line') })
  const fallback = tool(page, 'Edit').last()
  await expect(fallback.locator('[data-term-summary]')).toContainText('Updated ui/src/a.ts with 2 additions and 1 removal')
  expect(await fallback.locator('[data-diff]').evaluateAll((els) => els.map((e) => e.getAttribute('data-diff')))).toEqual(['del', 'add', 'add'])
  await expect(fallback.locator('[data-diff="del"]')).toHaveText(/^-\s+│\s+const a = 1$/) // no line numbers to show
  await expect(page.locator('[data-term-orphan]')).toHaveText('⎿ a result nobody asked for')
})

test('post_message is readable: the line, the Markdown text, the posted id, no JSON', async ({ page, qa }) => {
  await qa.open(LIVE)
  const post = tool(page, 'mcp__aiteams__post_message')
  await expect(post).toContainText('Posted in feature-1')
  await expect(post).toContainText('· Handoff → @lead')
  const body = post.locator('[data-term-body]')
  await expect(body.locator('.mention')).toHaveText('@lead')
  await expect(body.locator('strong')).toHaveText(['#104', '137 passed'])
  await expect(body.locator('li')).toHaveCount(2)
  await expect(body.locator('code')).toHaveText(['terminal.ts', 'LiveSession.tsx'])
  await expect(post.locator('[data-term-summary]')).toContainText('#1412')
  const text = await post.innerText()
  expect(text).not.toMatch(/[{}]|"conversation"|"mentions"|mcp__/) // no raw tool name or arguments
})

test('thinking is a dim ✻ Thinking… line that opens on a click', async ({ page, qa }) => {
  await qa.open(LIVE)
  const t = page.locator('[data-term-thinking]')
  await expect(t).toHaveText('✻ Thinking…')
  await expect(page.getByText('Read the design first')).toHaveCount(0)
  await t.getByRole('button').click()
  await expect(t).toContainText('Read the design first, then find where the header renders.')
  await t.getByRole('button').click()
  await expect(page.getByText('Read the design first')).toHaveCount(0)
})

test('a denied call is red and always shown, with the reason and without the AITEAMS_DENY prefix', async ({ page, qa }) => {
  await qa.open(LIVE)
  await expect(detailsToggle(page)).toHaveAttribute('aria-pressed', 'false') // details are off
  const denied = page.locator('[data-term-denied]')
  await expect(denied).toBeVisible()
  await expect(denied).toHaveAttribute('data-state', 'error')
  await expect(denied).toContainText('Update(aiteams/workflow.py)')
  await expect(denied.locator('[data-term-summary]')).toHaveText('⎿ Denied by hook: aiteams/workflow.py is locked by backend. Ask them in chat.')
  await expect(denied.locator('[data-dot]')).toHaveCSS('color', await probe(page, 'text-red-400', 'color'))
  await expect(denied.locator('[data-term-summary]')).toHaveCSS('color', await probe(page, 'text-red-400', 'color'))
  await expect(page.locator('[data-term]')).not.toContainText('AITEAMS_DENY')
  // a failing Bash is red too: the line after "Exit code 2" is the useful one
  const bad = tool(page, 'Bash').first()
  await expect(bad).toHaveAttribute('data-state', 'error')
  await expect(bad.locator('[data-term-summary]')).toContainText("Exit code 2: src/components/LiveSession.tsx(212,7): error TS2322: Type 'string' is not assignable to type 'number'.")
  await expect(bad.locator('[data-term-summary]')).toContainText('+3 lines')
  await bad.getByRole('button').click()
  await expect(bad.locator('[data-term-output]')).toContainText('Found 1 error in src/components/LiveSession.tsx:212')
  await expect(bad.locator('[data-term-output]')).toHaveCSS('color', await probe(page, 'text-red-300', 'color'))
})

test('no hook, init or command line until Show details is on', async ({ page, qa }) => {
  await withCmd(page)
  await qa.open(LIVE)
  await expect(tool(page, 'Read')).toBeVisible()
  const pane = page.locator('[data-term]')
  for (const t of ['init ·', 'hook ·', 'session sess-demo-frontend']) await expect(pane).not.toContainText(t)
  await expect(page.locator('[data-aiteams-cmd]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/live-terminal-details-off.png`, animations: 'disabled' })

  await detailsToggle(page).click()
  await expect(detailsToggle(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('[data-aiteams-cmd]')).toHaveText('claude -p --model opus --effort high --mcp-config <json>')
  await expect(pane).toContainText('init · session sess-demo-frontend · model claude-opus-5-5')
  await expect(pane).toContainText('hook · SessionStart:startup · success')
  await expect(pane.getByText('hook · PreToolUse:Edit · error')).toHaveCSS('color', await probe(page, 'text-red-400', 'color')) // the denying hook, in red
  await expect(pane.getByText('hook · SessionStart:startup · success')).toHaveCSS('color', await probe(page, 'text-neutral-500', 'color'))
  await page.screenshot({ path: `${SHOTS}/live-terminal-details-on.png`, animations: 'disabled' })

  await detailsToggle(page).click()
  await expect(pane).not.toContainText('hook ·')
  await expect(page.locator('[data-aiteams-cmd]')).toHaveCount(0)
})

test('a running turn streams into the view: the call shows as running, its result fills the same block', async ({ page, qa }) => {
  await running(page)
  await qa.open(LIVE)
  await expect(tool(page, 'Read')).toBeVisible()
  await qa.fire('turn_event', { event: assistant('Streaming in now.') })
  await expect(page.locator('[data-term-text]', { hasText: 'Streaming in now.' })).toBeVisible()

  await qa.fire('turn_event', { event: toolUse('toolu_live', 'Bash', { command: 'pytest -q' }) })
  const live = tool(page, 'Bash').last()
  await expect(live).toContainText('Bash(pytest -q)')
  await expect(live).toHaveAttribute('data-state', 'running')
  await expect(live.locator('[data-dot]')).toHaveAttribute('data-dot', 'running')
  await expect(live.locator('[data-term-summary]')).toHaveText('⎿ Running…')

  await qa.fire('turn_event', { event: answer('toolu_live', '516 passed, 1 skipped in 40.2s') })
  await expect(tool(page, 'Bash')).toHaveCount(3) // paired into the same block, not a fourth
  await expect(live).toHaveAttribute('data-state', 'ok')
  await expect(live.locator('[data-term-summary]')).toHaveText('⎿ 516 passed, 1 skipped in 40.2s')
  await expect(live.locator('[data-dot]')).toHaveCSS('color', await probe(page, 'text-green-400', 'color'))
})

test('a path outside the project stays absolute, and a long Bash command is cut at 80 characters', async ({ page, qa }) => {
  await qa.open(LIVE)
  await qa.fire('turn_event', { event: toolUse('toolu_p', 'Read', { file_path: '/etc/hosts' }) })
  await qa.fire('turn_event', { event: toolUse('toolu_q', 'Bash', { command: `echo ${'x'.repeat(120)}\nsecond line` }) })
  await expect(tool(page, 'Read').last()).toContainText('Read(/etc/hosts)')
  const long = tool(page, 'Bash').last()
  await expect(long).toContainText(`Bash(echo ${'x'.repeat(74)}…)`) // 79 characters and an ellipsis
  await expect(long).not.toContainText('x'.repeat(80))
  await expect(long).not.toContainText('second line') // only the first line of the command
})

test('the other team tools, TodoWrite and a foreign MCP tool each read as one line', async ({ page, qa }) => {
  await qa.open(LIVE)
  let n = 0
  const call = async (name: string, input: unknown, result: string) => {
    const id = `toolu_t${++n}`
    await qa.fire('turn_event', { event: toolUse(id, name, input) })
    await qa.fire('turn_event', { event: answer(id, result) })
    return tool(page, name).last()
  }
  const sum = (t: Locator) => t.locator('[data-term-summary]')
  const q = (header: string) => ({ question: `${header}?`, header, options: [{ label: 'A' }, { label: 'B' }] })

  const ask = await call('mcp__aiteams__ask_master', { questions: [q('Browser'), q('Scope')], feature: 'F-33' }, "card #37 posted in feature-33; end your turn, the Master's answer will wake you")
  await expect(ask).toContainText('Asked the Master: 2 questions (Browser, Scope)')
  await expect(sum(ask)).toHaveText('⎿ card #37')

  const appr = await call('mcp__aiteams__request_approval', { title: 'Design: Live terminal', summary: 'The plan.' }, 'approval #38 pending; end your turn, the Master will answer in the Inbox')
  await expect(appr).toContainText('Asked for approval: Design: Live terminal')
  await expect(sum(appr)).toHaveText('⎿ approval #38')

  const commit = await call('mcp__aiteams__request_commit', { files: ['ui/a.ts', 'ui/b.ts', 'ui/c.ts'], message: 'feat(live): terminal\n\nThe body.' }, 'commit #104 lead_review (3 files)')
  await expect(commit).toContainText('Commit request: feat(live): terminal (3 files)')
  await expect(commit.locator('[data-term-body]')).toHaveText('ui/a.tsui/b.tsui/c.ts')
  await expect(sum(commit)).toHaveText('⎿ #104')

  const review = await call('mcp__aiteams__review_commit', { commit: 104, decision: 'reject', note: 'Fix the gap.' }, 'commit #104 is now rejected')
  await expect(review).toContainText('Rejected commit #104')
  await expect(review.locator('[data-term-body]')).toHaveText('Fix the gap.')
  await expect(sum(review)).toHaveCount(0) // nothing under it

  const react = await call('mcp__aiteams__react', { message_id: 1409, emoji: '👍' }, 'added 👍 to #1409')
  await expect(react).toContainText('Reacted 👍 to #1409')
  await expect(sum(react)).toHaveCount(0)

  await expect(await call('mcp__aiteams__set_feature', { feature: 'F-33', phase: 'build', owner: 'frontend' }, 'header')).toContainText('Set F-33: phase build, owner frontend')
  await expect(await call('mcp__aiteams__write_handoff', { text: 'notes' }, 'saved handoff.md')).toContainText('Wrote handoff')

  const get = await call('mcp__aiteams__get_feature', { feature: 'F-33' }, '# Feature F-33: Live session\n- phase: build\n- owner: lead')
  await expect(get).toContainText('Get feature(F-33)')
  await expect(sum(get)).toContainText('# Feature F-33: Live session')
  await expect(sum(get)).toContainText('+2 lines')
  await get.getByRole('button').click()
  await expect(get.locator('[data-term-output]')).toContainText('- owner: lead')

  const todo = await call('TodoWrite', { todos: [{ content: 'Build it', status: 'completed', activeForm: 'Building' }, { content: 'Test it', status: 'pending', activeForm: 'Testing' }] }, 'Todos have been modified successfully')
  await expect(todo).toContainText('Update Todos')
  await expect(todo).toContainText('☒ Build it')
  await expect(todo).toContainText('☐ Test it')

  const other = await call('mcp__other__lookup', { query: 'invoice 42' }, 'found it')
  await expect(other).toContainText('other - lookup (MCP)(invoice 42)')
  await expect(sum(other)).toHaveText('⎿ found it')
})
