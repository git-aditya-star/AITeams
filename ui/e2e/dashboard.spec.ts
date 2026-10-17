import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-18: the visual Dashboard, one test per block. Expected values come from the sandbox's own /dashboard
// payload (seed: F-1 in build waiting on the Master, F-2/F-3 done, F-3 with a Summary, chat backdated 9h/5h/2h,
// a request committed 3h ago).

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const tooltip = (page: Page) => page.getByRole('tooltip')
// Move the pointer off every mark. Radix keeps a tooltip open while the pointer is in the grace area between
// the exit point and the tooltip, so a second move far from the first is needed to close it.
const away = async (page: Page) => {
  await page.mouse.move(700, 5)
  await page.mouse.move(1390, 890)
  await expect(tooltip(page)).toHaveCount(0)
}
// Hover a mark once the previous tooltip has closed, so the tooltip read next is this mark's.
const hoverTip = async (page: Page, target: Locator) => {
  await away(page)
  await target.hover()
}

test('Needs you: a tile shows its label on hover, zero tiles are greyed, a click opens the Inbox', async ({ page, qa }) => {
  const dash = await qa.api<any>('/dashboard')
  await qa.open('/p/demo')
  await page.screenshot({ path: `${SHOTS}/dashboard.png`, animations: 'disabled' })
  const tiles = page.getByRole('region', { name: 'Needs you' })
  for (const k of ['questions', 'approvals', 'commits', 'messages']) await expect(tiles.locator(`[data-tile="${k}"]`)).toHaveText(String(dash.needs_you[k]))
  const n = dash.needs_you.approvals
  const tile = tiles.locator('[data-tile="approvals"]')
  await tile.hover()
  await expect(tooltip(page)).toHaveText(`${n} approval${n === 1 ? '' : 's'}`)
  await expect(tile).toHaveAttribute('aria-label', `${n} approval${n === 1 ? '' : 's'}`)
  await expect(tiles.locator('[data-tile="messages"]')).toHaveClass(dash.needs_you.messages ? /text-gray-900/ : /text-gray-300/)
  await tile.click()
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible()
})

test('Activity: 24 hourly bars; a bar tooltip shows its counts, commit hours carry a dot', async ({ page, qa }) => {
  const hours = (await qa.api<any>('/dashboard')).activity_hourly as { start: number; messages: number; commits: number }[]
  expect(hours).toHaveLength(24)
  await qa.open('/p/demo')
  const spark = page.locator('[data-spark]')
  await expect(spark.locator('[data-spark-hour]')).toHaveCount(24)

  const busy = hours.findIndex((h) => h.messages > 0)
  expect(busy).toBeGreaterThanOrEqual(0)
  await spark.locator(`[data-spark-hour="${busy}"]`).hover()
  await expect(tooltip(page)).toContainText(`${hours[busy].messages} message`)

  const committed = hours.findIndex((h) => h.commits > 0)
  expect(committed).toBeGreaterThanOrEqual(0)
  await expect(spark.locator('[data-spark-commit]')).toHaveCount(hours.filter((h) => h.commits).length)
  await hoverTip(page, spark.locator(`[data-spark-hour="${committed}"]`))
  await expect(tooltip(page)).toContainText(`${hours[committed].commits} commit`)
})

test('Features: a phase segment lists its features with the red dot, a row opens the conversation', async ({ page, qa }) => {
  const dash = await qa.api<any>('/dashboard')
  const f1 = dash.features.find((f: any) => f.key === 'F-1')
  expect(f1.phase).toBe('build')
  await qa.open('/p/demo')
  const seg = page.locator('[data-phase-seg="build"]')
  await expect(seg).toHaveText(String(dash.phase_counts.build))
  await expect(page.locator('[data-phase-seg="done"]')).toHaveCount(0)
  await seg.hover()
  const row = page.locator('[data-phase-list="build"] [data-phase-row="F-1"]')
  await expect(row).toContainText(f1.title)
  const needsMaster = f1.stale || f1.waits_on?.who === 'master'
  await expect(row.locator('[data-attention]')).toHaveCount(needsMaster ? 1 : 0)
  await page.screenshot({ path: `${SHOTS}/dashboard-phase.png`, animations: 'disabled' })
  await row.click()
  await expect(page).toHaveURL(new RegExp(`c=${f1.conversation_id}`))
  await expect(page.getByRole('heading', { name: f1.title })).toBeVisible()
})

test('Shipped: the count is the 14-day timeline, a dot shows its summary and opens the conversation', async ({ page, qa }) => {
  const shipped = (await qa.api<any>('/dashboard')).shipped
  const f3 = shipped.timeline.find((f: any) => f.key === 'F-3')
  expect(f3.summary).toBeTruthy()
  await qa.open('/p/demo')
  const ship = page.locator('[data-ship]')
  await expect(ship.locator('[data-ship-count]')).toHaveText(String(shipped.timeline.length))
  await expect(ship.locator('[data-ship-dot]')).toHaveCount(shipped.timeline.length)
  const dot = ship.locator('[data-ship-dot="F-3"]')
  await dot.hover()
  await expect(tooltip(page)).toContainText(f3.summary)
  await expect(tooltip(page)).toContainText(f3.title)
  await dot.click()
  await expect(page).toHaveURL(new RegExp(`c=${f3.conversation_id}`))
  await expect(page.getByRole('heading', { name: f3.title })).toBeVisible()
})

test('Team: a ring shows the model and context on hover, a click opens Stop / Fresh start', async ({ page, qa }) => {
  const t = ((await qa.api<any>('/dashboard')).team as any[]).find((r) => r.role === 'frontend')
  await qa.open('/p/demo')
  const ring = page.locator('[data-ring="frontend"]')
  await expect(page.locator('[data-ring]')).toHaveCount(6)
  await ring.hover()
  await expect(tooltip(page)).toContainText(t.model_label || t.model)
  await expect(tooltip(page)).toContainText(`context ${Math.round(t.context_pct * 100)}%`)
  await expect(page.locator('[data-ring-feature="frontend"]')).toHaveText(t.current?.feature_key ?? '')
  await ring.click()
  const menu = page.locator('[data-role-actions="frontend"]')
  await expect(menu.getByRole('button', { name: 'Fresh start' })).toBeVisible()
  await away(page)
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
})

test('empty states: quiet sparkline, empty phase bar, nothing shipped in 14 days', async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/dashboard$/, async (route) => {
    const r = await route.fetch()
    const d = await r.json()
    d.features = []
    d.phase_counts = {}
    d.needs_you = { questions: 0, approvals: 0, commits: 0, messages: 0, total: 0 }
    d.activity_hourly = d.activity_hourly.map((h: any) => ({ ...h, messages: 0, commits: 0 }))
    d.shipped.timeline = []
    await route.fulfill({ response: r, json: d })
  })
  await qa.open('/p/demo')
  await expect(page.locator('[data-spark-quiet]')).toBeVisible()
  await expect(page.locator('[data-phase-empty]')).toBeVisible()
  await expect(page.locator('[data-ship-count]')).toHaveText('0')
  await expect(page.locator('[data-ship-dot]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/dashboard-empty.png`, animations: 'disabled' })
  await page.locator('[data-phase-empty]').hover()
  await expect(tooltip(page)).toHaveText('no active features')
  await hoverTip(page, page.locator('[data-ship-count]'))
  await expect(tooltip(page)).toContainText('nothing shipped in 14 days')
})
