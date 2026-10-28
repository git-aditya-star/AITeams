import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-40: an approval card says what the Master is asked at a glance. Top line: the type (Design, Scope lock, Use skill, Artifact,
// Done, Branch, PR, or plain Approval), the feature as "F-n Title", who it is from, `#id · ago`. Below it three labelled parts
// (What / If you approve / Trade-offs), and an "Open <doc>" button that shows the doc in a side panel beside the Inbox, chat or
// History, so the card can be answered while reading it. There is no phase dropdown: approving a `Done:` card moves the feature to
// done. An approval filed before the brief (no `brief`) renders its title and summary as it always did.
//
// The sandbox's `approval` fire sends a brief by default (what, if_approved, two trade-offs, doc design.md); `brief: null` gives an
// old-style row and `brief: {…}` overrides parts of it.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'

const card = (page: Page, id: number) => page.locator(`[data-card="approval-${id}"]`)
const panel = (page: Page) => page.locator('[data-doc-panel]')
const states = async (qa: { api: <T>(p: string) => Promise<T> }) => Object.fromEntries(((await qa.api<any[]>('/approvals?state=all')) as any[]).map((a) => [a.id, a.state]))

test('a card with a brief: the type, the feature, who it is from, and the three labelled parts', async ({ page, qa }) => {
  const { approval: id } = await qa.fire('approval', { text: 'Design: Export orders to CSV', role: 'lead' })
  await qa.open('/p/demo?c=feature-1')
  const c = card(page, id as number)
  await c.scrollIntoViewIfNeeded()
  await expect(c.locator('[data-approval-type]')).toHaveText('Design')
  await expect(c.locator('[data-approval-type]')).toHaveClass(/violet/)
  await expect(c.locator('[data-approval-feature]')).toHaveText('F-1 Export orders to CSV') // always, not only in the Inbox
  await expect(c).toContainText('from Lead 1')
  await expect(c).toContainText(`#${id} · `)
  await expect(c.locator('[data-brief="what"]')).toHaveText('Sign off the CSV export design.')
  await expect(c.locator('[data-brief="if_approved"]')).toHaveText('Backend and Frontend build it as written.')
  await expect(c.locator('[data-brief="tradeoffs"] li')).toHaveText(['Excel export waits for a later feature', 'One more migration'])
  for (const label of ['What', 'If you approve', 'Trade-offs']) await expect(c.locator('dt', { hasText: new RegExp(`^${label}$`) })).toBeVisible()
  // the title is not repeated: "Design: Export orders to CSV" is the type plus the feature line
  await expect(c.getByText('Design: Export orders to CSV')).toHaveCount(0)
  await expect(c.getByRole('combobox', { name: 'Set phase' })).toHaveCount(0) // the dropdown is gone
  await expect(c.locator('[data-state-chip]')).toHaveCount(0) // a state chip appears once it is decided
  await expect(c.locator('[data-action]')).toHaveText(['Approve', 'Request changes', 'Reject'])
  await expect(c.getByPlaceholder('Note (optional)')).toBeVisible()
})

test('each type gets its own chip; anything else is a plain Approval, and a title that adds to the feature line is kept', async ({ page, qa }) => {
  const ids: [string, string][] = []
  for (const [title, type] of [
    ['Scope lock: Order notes export', 'Scope lock'],
    ['Use skill: team-skills:local-preview', 'Use skill'],
    ['Artifact: artifact-card.html', 'Artifact'],
    ['Done: Export orders to CSV', 'Done'],
    ['PRD ready: CSV export scope', 'Approval'],
    ['Design: Order notes export', 'Design'],
  ] as const)
    ids.push([String((await qa.fire('approval', { text: title })).approval), type])
  await qa.open('/p/demo?c=inbox')
  const chips = new Set<string>()
  for (const [id, type] of ids) {
    const chip = card(page, Number(id)).locator('[data-approval-type]')
    await expect(chip).toHaveText(type)
    chips.add((await chip.getAttribute('class')) ?? '')
  }
  expect(chips.size).toBe(6) // Scope lock, Use skill, Artifact, Done and Design each have their own colour, and a plain Approval is grey
  // The part of the title after its prefix is shown when the feature line does not already say it.
  await expect(card(page, Number(ids[0][0]))).toContainText('Order notes export')
  await expect(card(page, Number(ids[4][0]))).toContainText('PRD ready: CSV export scope')
})

test('an approval filed before the brief renders its title and summary, with a button for a path summary', async ({ page, qa }) => {
  const plain = (await qa.fire('approval', { text: 'Design: older request', brief: null, summary: 'A plain summary, as roles wrote them.' })).approval
  const path = (await qa.fire('approval', { text: 'Design: older request with a doc', brief: null, summary: '.aiteams/features/F-1/design.md' })).approval
  await qa.open('/p/demo?c=inbox')
  const a = card(page, plain as number)
  await expect(a.locator('[data-approval-brief]')).toHaveCount(0)
  await expect(a).toContainText('Design: older request')
  await expect(a).toContainText('A plain summary, as roles wrote them.')
  await expect(a.locator('[data-doc-open]')).toHaveCount(0) // not a lone path
  const b = card(page, path as number)
  await expect(b.locator('[data-approval-brief]')).toHaveCount(0)
  await expect(b.locator('[data-doc-open]')).toHaveText('Open design.md')
  await b.locator('[data-doc-open]').click()
  await expect(panel(page)).toHaveAttribute('data-doc-panel', 'design.md')
  // the seeded PRD approval (#1) predates the brief too and has no doc button
  await expect(card(page, 1).locator('[data-approval-brief]')).toHaveCount(0)
  await expect(card(page, 1).locator('[data-doc-open]')).toHaveCount(0)
})

test('Open design.md shows the doc in a side panel; another doc replaces it, a view change keeps it, and it closes', async ({ page, qa }) => {
  const d = (await qa.fire('approval', { text: 'Design: Export orders to CSV' })).approval as number
  const m = (await qa.fire('approval', { text: 'Design: the mock', brief: { doc: 'mock-export.html' } })).approval as number
  await qa.open('/p/demo?c=inbox')
  await expect(panel(page)).toHaveCount(0)
  await card(page, d).locator('[data-doc-open]').click()
  await expect(panel(page)).toHaveAttribute('data-doc-panel', 'design.md')
  await expect(panel(page).locator('[data-doc-panel-name]')).toHaveText('design.md')
  await expect(panel(page).getByRole('heading', { name: 'F-1 design' })).toBeVisible() // markdown, rendered
  await expect(panel(page)).toHaveCSS('width', '440px')
  await expect(card(page, d).locator('[data-doc-open]')).toHaveClass(/ring-2/) // the open doc's button is marked
  await page.screenshot({ path: `${SHOTS}/approval-card-panel.png`, animations: 'disabled' })

  // another doc replaces it: a mock shows framed and sandboxed
  await card(page, m).locator('[data-doc-open]').click()
  await expect(panel(page)).toHaveAttribute('data-doc-panel', 'mock-export.html')
  await expect(panel(page)).toHaveCount(1)
  const frame = panel(page).locator('iframe')
  await expect(frame).toBeVisible()
  expect(await frame.getAttribute('sandbox')).not.toContain('allow-same-origin')

  // a view change keeps it (Inbox -> the feature's chat)
  await page.locator('aside').getByRole('button', { name: /F-1\s+Export orders/ }).click()
  await expect(page.getByRole('heading', { name: 'Export orders to CSV' }).first()).toBeVisible()
  await expect(panel(page)).toHaveAttribute('data-doc-panel', 'mock-export.html')

  await panel(page).getByRole('button', { name: 'Close doc panel' }).click()
  await expect(panel(page)).toHaveCount(0)
})

test('with the panel open, Approve, Request changes and Reject still answer a card', async ({ page, qa }) => {
  const a = (await qa.fire('approval', { text: 'Design: one' })).approval as number
  const b = (await qa.fire('approval', { text: 'Design: two' })).approval as number
  const c = (await qa.fire('approval', { text: 'Design: three' })).approval as number
  await qa.open('/p/demo?c=inbox')
  await card(page, a).locator('[data-doc-open]').click()
  await expect(panel(page)).toBeVisible()
  await card(page, a).locator('[data-action="approve"]').click()
  await card(page, b).locator('[data-action="changes"]').click()
  await card(page, c).locator('[data-action="reject"]').click()
  await expect.poll(async () => states(qa)).toMatchObject({ [a]: 'approved', [b]: 'changes_requested', [c]: 'rejected' })
  await expect(panel(page)).toBeVisible() // answering does not close it
})

test('approving a Done request moves the feature to done, with no phase dropdown', async ({ page, qa }) => {
  const done = (await qa.fire('approval', { text: 'Done: Export orders to CSV' })).approval as number
  await qa.open('/p/demo?c=feature-1')
  const c = card(page, done)
  await c.scrollIntoViewIfNeeded()
  await expect(c.getByRole('combobox', { name: 'Set phase' })).toHaveCount(0)
  await c.locator('[data-action="approve"]').click()
  await expect.poll(async () => ((await qa.api<any[]>('/features?history=1')) as any[]).find((f) => f.key === 'F-1').phase).toBe('done')
  await expect(c.locator('[data-state-chip]')).toHaveText('approved')
})

test('a decided card keeps its parts and the doc button, and says the Master note', async ({ page, qa }) => {
  const id = (await qa.fire('approval', { text: 'Design: Export orders to CSV' })).approval as number
  await qa.open('/p/demo?c=inbox')
  await card(page, id).getByPlaceholder('Note (optional)').fill('ship it')
  await card(page, id).locator('[data-action="approve"]').click()
  // decided, it leaves the Inbox; its card in the feature's chat keeps the brief
  await page.locator('aside').getByRole('button', { name: /F-1\s+Export orders/ }).click()
  const c = card(page, id)
  await c.scrollIntoViewIfNeeded()
  await expect(c.locator('[data-state-chip]')).toHaveText('approved')
  await expect(c.locator('[data-brief="what"]')).toBeVisible()
  await expect(c).toContainText('ship it')
  await expect(c.locator('[data-doc-open]')).toHaveText('Open design.md')
  await expect(c.locator('[data-action]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/approval-card-decided.png`, animations: 'disabled' })
})
