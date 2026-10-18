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

const KPIS: [string, string][] = [
  ['questions', 'Questions'],
  ['approvals', 'Approvals'],
  ['commits', 'Commits'],
  ['messages', 'Messages'],
]
const TITLES: [string, string][] = [
  ['activity', 'Activity · last 24h'],
  ['features', 'Features by phase'],
  ['shipped', 'Shipped · last 14 days'],
  ['team', 'Team'],
]

test('KPI cards: number and visible label, zero ones greyed but labelled, a click opens the Inbox', async ({ page, qa }) => {
  const dash = await qa.api<any>('/dashboard')
  await qa.open('/p/demo')
  await page.screenshot({ path: `${SHOTS}/dashboard.png`, animations: 'disabled' })
  for (const [k, label] of KPIS) {
    const tile = page.locator(`[data-tile="${k}"]`)
    await expect(tile.locator('[data-tile-value]')).toHaveText(String(dash.needs_you[k]))
    await expect(tile.locator('[data-tile-label]')).toHaveText(label)
    await expect(tile.locator('[data-tile-label]')).toBeVisible()
    await expect(tile.locator('[data-tile-value]')).toHaveClass(dash.needs_you[k] ? /text-gray-900/ : /text-gray-300/)
  }
  const n = dash.needs_you.approvals
  const tile = page.locator('[data-tile="approvals"]')
  await expect(tile).toHaveAttribute('aria-label', `${n} approval${n === 1 ? '' : 's'}`)
  await tile.click()
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible()
})

test('layout: titled cards in a 12-column grid, edges and heights line up per row, header buttons match', async ({ page, qa }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await qa.open('/p/demo')
  for (const [id, title] of TITLES) await expect(page.locator(`[data-card="${id}"]`).getByRole('heading', { name: title })).toBeVisible()

  const box = async (sel: string) => (await page.locator(sel).boundingBox())!
  const grid = await box('[data-dashboard-grid]')
  const near = (a: number, b: number) => expect(Math.abs(a - b)).toBeLessThanOrEqual(1)
  const rows = [
    KPIS.map(([k]) => `[data-kpi="${k}"]`),
    ['[data-card="activity"]', '[data-card="features"]'],
    ['[data-card="shipped"]', '[data-card="team"]'],
  ]
  const lefts: number[][] = []
  for (const row of rows) {
    const boxes = await Promise.all(row.map(box))
    near(boxes[0].x, grid.x) // the row starts at the grid's left edge…
    near(boxes.at(-1)!.x + boxes.at(-1)!.width, grid.x + grid.width) // …and ends at its right edge
    for (const b of boxes) {
      near(b.y, boxes[0].y)
      near(b.height, boxes[0].height)
    }
    lefts.push(boxes.map((b) => b.x))
    // card titles in a row sit on one line, whether or not a card has a headline (the KPI cards have none)
    if (row[0].startsWith('[data-card')) {
      const titles = await Promise.all(row.map(async (sel) => (await page.locator(`${sel} [data-slot="card-title"]`).boundingBox())!))
      for (const t of titles) near(t.y, titles[0].y)
    }
  }
  // The wide cards share their column edges, and the narrow ones line up with the last KPI card's right edge.
  near(lefts[1][1], lefts[2][1])
  const lastKpi = await box('[data-kpi="messages"]')
  const team = await box('[data-card="team"]')
  near(lastKpi.x + lastKpi.width, team.x + team.width)

  // Header: Standup, the last-report clock (when there is one) and New task share one height.
  const actions = page.locator('[data-dashboard-actions]')
  const standup = await actions.getByRole('button', { name: 'Standup' }).boundingBox()
  const newTask = await actions.getByRole('button', { name: 'New task' }).boundingBox()
  near(standup!.height, newTask!.height)
  near(standup!.y, newTask!.y)
})

test('Activity: totals headline, hour axis, legend; empty hours draw nothing; a bar tooltip shows its counts', async ({ page, qa }) => {
  const hours = (await qa.api<any>('/dashboard')).activity_hourly as { start: number; messages: number; commits: number }[]
  expect(hours).toHaveLength(24)
  await qa.open('/p/demo')
  const card = page.locator('[data-card="activity"]')
  const spark = page.locator('[data-spark]')
  await expect(spark.locator('[data-spark-hour]')).toHaveCount(24)

  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
  const sum = (k: 'messages' | 'commits') => hours.reduce((n, h) => n + h[k], 0)
  await expect(card.locator('[data-spark-headline]')).toHaveText(`${plural(sum('messages'), 'message')} · ${plural(sum('commits'), 'commit')}`)

  // Axis: a label at every local hour divisible by 6 (unless it would crowd "now"), and "now" at the right end.
  const hh = (ts: number) => `${String(new Date(ts * 1000).getHours()).padStart(2, '0')}:00`
  const six = hours.filter((h, i) => new Date(h.start * 1000).getHours() % 6 === 0 && i < 22).map((h) => hh(h.start))
  expect(six.length).toBeGreaterThanOrEqual(3)
  await expect(spark.locator('[data-spark-tick]')).toHaveText([...six, 'now'])
  for (const t of await spark.locator('[data-spark-tick]').all()) await expect(t).toBeVisible()
  const legend = spark.locator('[data-spark-legend]')
  await expect(legend).toBeVisible()
  await expect(legend).toContainText('messages')
  await expect(legend).toContainText('commits')

  // An hour without messages draws no bar.
  const idle = hours.findIndex((h) => !h.messages && !h.commits)
  await expect(spark.locator(`[data-spark-hour="${idle}"] span`)).toHaveCount(0)

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

test('Features: headline, a legend with every phase, rows with phase and "waits on you", a row opens it', async ({ page, qa }) => {
  const dash = await qa.api<any>('/dashboard')
  const f1 = dash.features.find((f: any) => f.key === 'F-1')
  expect(f1.phase).toBe('build')
  expect(f1.waits_on?.who).toBe('master')
  await qa.open('/p/demo')
  const card = page.locator('[data-card="features"]')
  await expect(card.locator('[data-phase-headline]')).toHaveText(`${dash.features.length} active`)

  // One segment only: no bare number inside it.
  await expect(card.locator('[data-phase-seg="build"]')).toHaveText('')
  await expect(card.locator('[data-phase-seg="done"]')).toHaveCount(0)
  for (const p of ['intake', 'spec', 'design', 'build', 'qa']) {
    const n = dash.features.filter((f: any) => f.phase === p).length
    await expect(card.locator(`[data-phase-legend="${p}"]`)).toHaveText(`${p} ${n}`)
    await expect(card.locator(`[data-phase-legend="${p}"]`)).toBeVisible()
  }

  const row = card.locator('[data-phase-row="F-1"]')
  await expect(row).toContainText(f1.title)
  await expect(row).toContainText('build')
  await expect(row.locator('[data-waits]')).toHaveText('waits on you')
  await expect(card.locator('[data-phase-more]')).toHaveCount(0)
  await row.click()
  await expect(page).toHaveURL(new RegExp(`c=${f1.conversation_id}`))
  await expect(page.getByRole('heading', { name: f1.title })).toBeVisible()
})

test('Features: at most 5 rows, attention first, then "+N more" opens the first hidden one', async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/dashboard$/, async (route) => {
    const r = await route.fetch()
    const d = await r.json()
    const base = d.features[0]
    const phases = ['intake', 'spec', 'design', 'qa', 'build', 'spec']
    const extra = phases.map((phase, i) => ({ ...base, id: 100 + i, key: `F-${10 + i}`, title: `Extra ${i}`, phase, conversation_id: `feature-${10 + i}`,
      waits_on: null, stale: i === 5, last_activity: 1000 + i }))
    d.features = [...d.features, ...extra]
    await route.fulfill({ response: r, json: d })
  })
  await qa.open('/p/demo')
  const card = page.locator('[data-card="features"]')
  await expect(card.locator('[data-phase-headline]')).toHaveText('7 active')
  await expect(card.locator('[data-phase-row]')).toHaveCount(5)
  // F-1 waits on the Master and F-15 is stale: both come first, then the most recently active.
  await expect(card.locator('[data-phase-row]').nth(0)).toHaveAttribute('data-phase-row', 'F-1')
  await expect(card.locator('[data-phase-row]').nth(1)).toHaveAttribute('data-phase-row', 'F-15')
  await expect(card.locator('[data-phase-row="F-15"] [data-stale]')).toHaveText('stale')
  await expect(card.locator('[data-phase-seg]')).toHaveCount(5)
  await expect(card.locator('[data-phase-seg="spec"]')).toHaveText('2')
  const more = card.locator('[data-phase-more]')
  await expect(more).toHaveText('+2 more')
  await page.screenshot({ path: `${SHOTS}/dashboard-features-many.png`, animations: 'disabled' })
  await more.click()
  await expect(page).toHaveURL(/c=feature-11/)
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
  await expect(page.locator('[data-spark-quiet]')).toHaveText('No activity in 24h')
  await expect(page.locator('[data-spark-headline]')).toHaveText('0 messages · 0 commits')
  await expect(page.locator('[data-spark-tick]').last()).toHaveText('now')
  await expect(page.locator('[data-phase-empty]')).toHaveText('No active features')
  await expect(page.locator('[data-phase-headline]')).toHaveText('0 active')
  await expect(page.locator('[data-phase-legend="build"]')).toHaveText('build 0')
  await expect(page.locator('[data-ship-count]')).toHaveText('0')
  await expect(page.locator('[data-ship-dot]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/dashboard-empty.png`, animations: 'disabled' })
  await hoverTip(page, page.locator('[data-ship-count]'))
  await expect(tooltip(page)).toContainText('nothing shipped in 14 days')
})
