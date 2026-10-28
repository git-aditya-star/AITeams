import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-41: a role has its main session and one session ("lane") per feature that has its own worktree. The UI names them "Frontend · F-36":
// a message from a lane carries that tag, the team strip gets a small chip per active lane (own dot, own context meter), the Live
// view has a session picker, a stall on a lane names it, and the role settings dialog has the two load numbers.
//
// The daemon's wire shapes (design §4, posted in feature-41): `message.lane` / `turn.lane` ('' = main, 'F-36'), and a role row in
// `/team` gains `lanes: [{lane, state, context_pct, turn_id}]` for its ACTIVE feature sessions. The sandbox runs no turns, so the
// shape cases rewrite the responses with page.route; the last case goes through the real daemon.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const NOW = Date.now() / 1000

const strip = (page: Page) => page.locator('main > div').filter({ has: page.getByRole('button', { name: /^Manager\b/ }) }).first()
const chip = (page: Page, key: string) => strip(page).locator(`[data-lane-chip="${key}"]`)
const picker = (page: Page) => page.getByRole('combobox', { name: 'Session' })
const away = async (page: Page) => {
  await page.mouse.move(700, 5)
  await page.mouse.move(1390, 890)
  await expect(page.getByRole('tooltip')).toHaveCount(0)
}

// Frontend runs two feature sessions (F-36 working, F-40 idle and over its limit) and Backend one (F-36, queued).
async function withLanes(page: Page) {
  await page.route(/\/api\/p\/demo\/team$/, async (route) => {
    const r = await route.fetch()
    const d = await r.json()
    const by = (role: string) => d.roles.find((t: any) => t.role === role)
    Object.assign(by('frontend'), {
      state: 'idle',
      kind: null,
      context_pct: 0.08,
      lanes: [
        { lane: 'F-36', state: 'running', kind: 'turn', context_pct: 0.31, turn_id: 9001 },
        { lane: 'F-40', state: 'idle', context_pct: 2.4, turn_id: null },
      ],
    })
    Object.assign(by('backend'), { state: 'idle', kind: null, context_pct: 0.22, lanes: [{ lane: 'F-36', state: 'queued', context_pct: 0.44, turn_id: null }] })
    await route.fulfill({ response: r, json: d })
  })
}

// Frontend's turns: the seeded main-session ones (an older daemon sends no `lane` on them) plus one on F-36, and a closed F-12's.
async function withLaneTurns(page: Page) {
  await page.route(/\/api\/p\/demo\/turns\?role=frontend/, async (route) => {
    const r = await route.fetch()
    const turns = await r.json()
    const base = turns[0]
    const extra = [
      { ...base, id: 9001, lane: 'F-36', state: 'running', ended_at: null, session_id: 'sess-f36', context_tokens: 46000, origin: 'feature-36', prompt: 'F-36 wake' },
      { ...base, id: 9000, lane: 'F-12', state: 'done', session_id: 'sess-f12', context_tokens: 12000, origin: 'feature-12' },
    ]
    await route.fulfill({ response: r, json: [...extra, ...turns.map((t: any) => ({ ...t, lane: '' }))].slice(0, 50) })
  })
  await page.route(/\/api\/p\/demo\/turns\/9001\/events/, (route) =>
    route.fulfill({
      json: [{ turn_id: 9001, seq: 1, ts: NOW, type: 'assistant', data: { type: 'assistant', message: { content: [{ type: 'text', text: 'Editing the F-36 PR card.' }] } } }],
    }),
  )
}

test('a message from a feature session reads "Frontend · F-36"; main-session and Master posts carry no tag', async ({ page, qa }) => {
  const lane = await qa.fire('message', { conversation: 'feature-4', role: 'frontend', text: 'U2 is built against the contract. Filing with requires.' })
  const main = await qa.fire('message', { conversation: 'feature-4', role: 'frontend', text: 'A main-session note about the sidebar.' })
  const master = await qa.fire('message', { conversation: 'feature-4', role: 'master', text: 'Thanks, go ahead.' })
  await withLanes(page)
  await page.route(/\/api\/p\/demo\/messages\?/, async (route) => {
    const r = await route.fetch()
    const ms = await r.json()
    for (const m of ms) m.lane = m.id === lane.message ? 'F-36' : '' // a lane is '' for main, the feature key otherwise
    await route.fulfill({ response: r, json: ms })
  })
  await qa.open('/p/demo?c=feature-4')

  const row = (id: unknown) => page.locator(`#m-${id}`)
  await expect(row(lane.message)).toContainText('Frontend')
  await expect(row(lane.message).locator('[data-msg-lane]')).toHaveText('·F-36')
  await expect(row(lane.message).locator('[data-msg-lane]')).toHaveAttribute('data-msg-lane', 'F-36')
  await expect(row(main.message).locator('[data-msg-lane]')).toHaveCount(0)
  await expect(row(master.message).locator('[data-msg-lane]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/lanes-message-and-strip.png`, animations: 'disabled' })
})

test('the team strip gets a chip per active feature session, with its own state and meter, next to its role', async ({ page, qa }) => {
  await withLanes(page)
  await qa.open('/p/demo')

  // Order: each role's chips follow that role's pill; nothing else moves.
  const buttons = strip(page).locator('button:not([aria-label="Role settings"])')
  await expect(buttons).toHaveText([/^Manager/, /^Lead 1/, /^Lead 2/, /^Product/, /^Frontend/, /^F-36/, /^F-40/, /^Backend/, /^F-36/, /^Qa/])
  await expect(chip(page, 'frontend:F-36')).toHaveText(/F-36.*31%$/)
  await expect(chip(page, 'frontend:F-40')).toHaveText(/F-40.*100%$/) // over its limit: capped, like every meter (F-42)
  await expect(chip(page, 'backend:F-36')).toHaveText(/F-36.*44%$/)
  await expect(strip(page).getByRole('button', { name: /^Frontend\b/ })).toHaveText(/8%$/) // the role's own pill is its main session
  await expect(strip(page).getByRole('button', { name: /^Frontend\b/ })).toHaveCount(1)

  // The dot is the lane's own state: working, idle, queued.
  const dot = (key: string) => chip(page, key).locator('span').first()
  await expect(dot('frontend:F-36')).toHaveClass(/bg-green-500/)
  await expect(dot('frontend:F-40')).toHaveClass(/bg-gray-300/)
  await expect(dot('backend:F-36')).toHaveClass(/bg-amber-400/)

  // Tooltips name the session and its state.
  await chip(page, 'frontend:F-36').hover()
  await expect(page.getByRole('tooltip')).toContainText('Frontend · F-36 · running')
  await away(page)
  await chip(page, 'frontend:F-40').hover()
  await expect(page.getByRole('tooltip')).toContainText('Frontend · F-40 · idle · context full, rotates after its next turn')
  await away(page)

  // A role with no lanes keeps just its pill.
  await expect(strip(page).locator('[data-lane-chip^="manager:"], [data-lane-chip^="qa:"]')).toHaveCount(0)

  // Clicking a chip opens that role's Live view on that session.
  await withLaneTurns(page)
  await chip(page, 'frontend:F-36').click()
  await expect(page).toHaveURL(/c=dm-frontend&tab=live&lane=F-36/)
  await expect(picker(page)).toHaveText('Frontend · F-36')
})

test('the Live view picks a session; each one shows only its own turns', async ({ page, qa }) => {
  await withLanes(page)
  await withLaneTurns(page)
  await qa.open('/p/demo?c=dm-frontend&tab=live')

  // Main is the default: the seeded turns, none of the lanes'.
  await expect(picker(page)).toHaveText('Frontend (main)')
  const list = page.locator('[data-live-session] > div').first()
  await expect(list.getByRole('button', { name: /#9001/ })).toHaveCount(0)
  await expect(list.getByRole('button', { name: /#\d+/ }).first()).toBeVisible()

  // The list: main, then each session with its state; F-12 has turns on screen but no live session any more.
  await picker(page).click()
  const options = page.getByRole('option')
  await expect(options).toHaveText([/^Frontend \(main\).*idle/, /^Frontend · F-12.*closed/, /^Frontend · F-36.*running/, /^Frontend · F-40.*idle/])
  await page.screenshot({ path: `${SHOTS}/lanes-live-picker.png`, animations: 'disabled' })

  // F-36: only its turn, and its transcript.
  await options.filter({ hasText: 'Frontend · F-36' }).click()
  await expect(page).toHaveURL(/lane=F-36/)
  await expect(picker(page)).toHaveText('Frontend · F-36')
  await expect(list.getByRole('button', { name: /#\d+/ })).toHaveCount(1)
  await expect(list.getByRole('button', { name: /#9001/ })).toBeVisible()
  await expect(page.locator('[data-term]')).toContainText('Editing the F-36 PR card.')
  await expect(page.getByText('claude --resume sess-f36 --setting-sources local')).toBeVisible()

  // F-12 (closed) is still readable; F-40 has no turns yet; back to main.
  await picker(page).click()
  await page.getByRole('option', { name: /F-12/ }).click()
  await expect(list.getByRole('button', { name: /#9000/ })).toBeVisible()
  await picker(page).click()
  await page.getByRole('option', { name: /F-40/ }).click()
  await expect(page.getByText('no turns yet')).toBeVisible()
  await picker(page).click()
  await page.getByRole('option', { name: /Frontend \(main\)/ }).click()
  await expect(page).not.toHaveURL(/lane=/)
  await expect(list.getByRole('button', { name: /#9001/ })).toHaveCount(0)
})

test('with no feature sessions the Live view has no picker, as before', async ({ page, qa }) => {
  await qa.open('/p/demo?c=dm-frontend&tab=live')
  await expect(page.locator('[data-live-session="main"]')).toBeVisible()
  await expect(picker(page)).toHaveCount(0)
})

test('a stall on a feature session names it', async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/features(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const fs = await r.json()
    const f4 = fs.find((f: any) => f.key === 'F-4')
    f4.stall = { who: 'frontend', lane: 'F-4', kind: 'message', ref: 'msg:5', step: 1, stuck: false, since: NOW - 300 }
    await route.fulfill({ response: r, json: fs })
  })
  await qa.open('/p/demo?c=feature-4')
  await expect(page.locator('main [data-stall-chip]')).toHaveText('Waiting on Frontend · F-4')
  await expect(page.locator('aside [data-stall-chip]')).toHaveText('waiting · Frontend · F-4')
  await page.locator('main [data-stall-chip]').hover()
  await expect(page.getByRole('tooltip').first()).toContainText('asks Frontend · F-4 to act')
})

test('role settings: Turns at once and Sessions per role are numbers, sent only when edited', async ({ page, qa }) => {
  // The sandbox pins max_concurrent to 0 (scheduler off), so a plain Save must leave it alone.
  await qa.open('/p/demo?c=general')
  await page.getByRole('button', { name: 'Role settings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Role settings' })
  const turns = dialog.getByLabel('Turns at once')
  const lanes = dialog.getByLabel('Sessions per role')
  await expect(turns).toHaveValue('0')
  await expect(lanes).toHaveValue('2') // the default while the daemon sends none
  await expect(dialog.locator('[data-settings-load]')).toContainText('across all roles')
  await page.screenshot({ path: `${SHOTS}/lanes-settings.png`, animations: 'disabled' })

  const sent = async () => {
    const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/api/settings'))
    await dialog.getByRole('button', { name: 'Save' }).click()
    return (await put).postDataJSON()
  }
  await page.route(/\/api\/settings$/, (route) => (route.request().method() === 'PUT' ? route.fulfill({ json: route.request().postDataJSON() }) : route.fallback()))

  // Unedited: neither key is sent.
  const plain = await sent()
  expect(plain).not.toHaveProperty('max_concurrent')
  expect(plain).not.toHaveProperty('max_lanes_per_role')

  // Edited out of range: the sentence shows, nothing is sent.
  await page.getByRole('button', { name: 'Role settings' }).click()
  await turns.fill('13')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toContainText('Turns at once must be a whole number from 1 to 12')
  await turns.fill('6')
  await lanes.fill('0')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toContainText('Sessions per role must be a whole number from 1 to 6')

  // Valid: both go as numbers.
  await lanes.fill('3')
  expect(await sent()).toMatchObject({ max_concurrent: 6, max_lanes_per_role: 3 })
  await expect(dialog).toHaveCount(0)
})

// The real daemon, no rewriting: `lane_turn` makes Frontend work on F-4 in its own session (an active session, a running turn on the lane,
// and a message from it), the way a turn of a feature with its own worktree does. This is the wire shape the cases above assume.
test('through the daemon: a feature session shows as a chip, a tagged message and a Live session, and goes idle when its turn ends', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-4')
  await expect(strip(page).locator('[data-lane-chip]')).toHaveCount(0)

  const fired = await qa.fire('lane_turn', { role: 'frontend', feature: 'F-4', context_tokens: 45000, text: 'U1 is built against the contract.' })
  expect(fired.lane).toBe('F-4')

  // The wire: the role row keeps its main-session fields and lists the lane; the turn and the message carry `lane`.
  const team = (await qa.api<{ roles: any[] }>('/team')).roles.find((t) => t.role === 'frontend')
  expect(team.lanes).toEqual([expect.objectContaining({ lane: 'F-4', state: 'running', turn_id: fired.turn })])
  expect(team.lanes[0].context_pct).toBeGreaterThan(0)
  const turns = await qa.api<any[]>('/turns?role=frontend')
  expect(turns.find((t) => t.id === fired.turn)?.lane).toBe('F-4')
  expect(turns.filter((t) => t.id !== fired.turn).every((t) => (t.lane ?? '') === '')).toBe(true)

  // Live, over the websocket: the chip appears (busy), the message is tagged, and a main-session post is not.
  await expect(chip(page, 'frontend:F-4')).toBeVisible()
  await expect(chip(page, 'frontend:F-4').locator('span').first()).toHaveClass(/bg-green-500/)
  await expect(chip(page, 'frontend:F-4')).toHaveText(/F-4.*[1-9]\d?%$/)
  const row = page.locator(`#m-${fired.message}`)
  await expect(row.locator('[data-msg-lane]')).toHaveAttribute('data-msg-lane', 'F-4')
  await expect(row).toContainText('U1 is built against the contract.')
  const main = await qa.fire('message', { conversation: 'feature-4', role: 'frontend', text: 'A note from my main session.' })
  await expect(page.locator(`#m-${main.message}`)).toContainText('A note from my main session.')
  await expect(page.locator(`#m-${main.message} [data-msg-lane]`)).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/lanes-daemon-strip.png`, animations: 'disabled' })

  // The chip opens that session in the Live view: the picker names it, and its turn is the only one listed.
  await chip(page, 'frontend:F-4').click()
  await expect(page).toHaveURL(/c=dm-frontend&tab=live&lane=F-4/)
  await expect(picker(page)).toHaveText('Frontend · F-4')
  const list = page.locator('[data-live-session] > div').first()
  await expect(list.getByRole('button', { name: /#\d+/ })).toHaveCount(1)
  await expect(list.getByRole('button', { name: new RegExp(`#${fired.turn}\\b`) })).toContainText('running')
  await picker(page).click()
  await expect(page.getByRole('option')).toHaveText([/^Frontend \(main\)/, /^Frontend · F-4.*running/])
  await page.keyboard.press('Escape')

  // Its turn ends: the lane stays (the session is still active) and goes idle; the new turn joins the list live.
  const done = await qa.fire('lane_turn', { role: 'frontend', feature: 'F-4', state: 'done' })
  await expect(chip(page, 'frontend:F-4').locator('span').first()).toHaveClass(/bg-gray-300/)
  await expect(list.getByRole('button', { name: new RegExp(`#${done.turn}\\b`) })).toBeVisible()
})
