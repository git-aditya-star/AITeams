import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-31: every chat message shows its number (#<id>, what roles cite) at the right end of its header line (author, time, type chip).
// Checked on the seeded F-1 conversation: a plain message, a card (approval, commit, artifact) and a System line.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'

type Msg = { id: number; type: string; ref: string | null }

// The messages of F-1's chat, as the daemon has them.
const f1 = (qa: { api: <T>(p: string) => Promise<T> }) => qa.api<Msg[]>('/messages?conversation=feature-1&limit=200')

// The number sits inside the header line (the row's first flex line), as its last child, at its right edge.
async function expectNumber(page: Page, id: number) {
  const row = page.locator(`#m-${id}`)
  const num = row.locator('[data-msg-id]')
  await expect(num).toHaveCount(1)
  await expect(num).toHaveText(`#${id}`)
  await expect(num).toBeVisible()
  const header = row.locator('div:has(> [data-msg-id])')
  await expect(header).toHaveCount(1)
  expect(await header.evaluate((h) => h.lastElementChild?.hasAttribute('data-msg-id'))).toBe(true) // after the author, time and chip
  const h = (await header.boundingBox())!
  const n = (await num.boundingBox())!
  expect(Math.abs(h.x + h.width - (n.x + n.width))).toBeLessThanOrEqual(2) // right-aligned in the header line
  expect(n.y).toBeGreaterThanOrEqual(h.y - 1)
  expect(n.y + n.height).toBeLessThanOrEqual(h.y + h.height + 1) // on the header line, not below it
  return { header, num }
}

const size = (l: Locator) => l.evaluate((e) => getComputedStyle(e).fontSize)

test('a plain message, a card and a System line each show their own #id at the right end of the header line', async ({ page, qa }) => {
  const msgs = await f1(qa)
  const plain = msgs.find((m) => m.type === 'Message' && !m.ref)!
  const card = msgs.find((m) => m.ref?.startsWith('approval:'))!
  const commit = msgs.find((m) => m.ref?.startsWith('commit:'))!
  const artifact = msgs.find((m) => m.ref?.startsWith('artifact:'))!
  const system = msgs.find((m) => m.type === 'System')! // the seeded "Lead 1 approved commit #1" line
  for (const m of [plain, card, commit, artifact, system]) expect(m, 'the seed has this kind of message').toBeTruthy()

  await qa.open('/p/demo?c=feature-1')
  for (const m of [plain, card, commit, artifact, system]) await expectNumber(page, m.id)

  // 10px muted digits, selectable (to copy), not a link
  const { num } = await expectNumber(page, plain.id)
  expect(await size(num)).toBe('10px')
  await expect(num).toHaveCSS('user-select', 'text')
  await expect(num.locator('a')).toHaveCount(0)

  // every row on screen has exactly its own number (a run of 3 replies in a row is folded until opened)
  const ids = await page.locator('[id^="m-"]').evaluateAll((els) => els.map((e) => Number(e.id.slice(2))))
  expect(ids.length).toBeGreaterThan(5)
  await expect(page.locator('[id^="m-"] [data-msg-id]')).toHaveCount(ids.length)
  await expect(page.locator('[id^="m-"] [data-msg-id]')).toHaveText(ids.map((id) => `#${id}`))

  await page.locator(`#m-${card.id}`).scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/message-number.png`, animations: 'disabled' })
})

test('a folded run shows each message number once it is opened', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator('#m-3')).toHaveCount(0) // messages 2-4 are a folded run of replies
  await page.getByText('3 replies').click()
  await expectNumber(page, 2)
  await expectNumber(page, 3)
  await expectNumber(page, 4)
})

test('a message that arrives live gets its number too', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const id = (await qa.fire('message', { conversation: 'feature-1', role: 'backend', text: 'Fresh one' })).message as number
  await expectNumber(page, id)
  await expect(page.locator(`#m-${id} [data-msg-id]`)).toHaveText(`#${id}`)
})

test('when the column is tight the name truncates first: the time, the chip and the number stay whole on one line', async ({ page, qa }) => {
  await page.setViewportSize({ width: 490, height: 800 }) // the sidebar is fixed, so this leaves the message column about 160px
  const target = (await f1(qa)).find((m) => m.type === 'Approval')! // the widest header: a name, the time and a type chip
  await qa.open('/p/demo?c=feature-1')
  const { header, num } = await expectNumber(page, target.id)
  const name = page.locator(`#m-${target.id}`).locator('div:has(> [data-msg-id]) > span').first()
  expect(await name.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true) // "Product" is cut short...
  expect((await header.boundingBox())!.height).toBeLessThan(26) // ...so nothing wrapped under it
  const n = (await num.boundingBox())!
  expect(n.width).toBeGreaterThan(8) // the number kept its full width
  const row = (await page.locator(`#m-${target.id}`).boundingBox())!
  expect(n.x + n.width).toBeLessThanOrEqual(row.x + row.width + 1) // and was not pushed out of the row
})
