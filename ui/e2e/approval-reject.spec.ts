import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-25: a Reject button next to Approve / Request changes, a red "rejected" badge, and History's Approvals tab with an
// outcome filter. The sandbox seeds three resolved approvals: F-1's design (approved), and on F-3 a "Use skill" one
// (changes requested, with a note) and a scope lock (rejected, with a note).

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const decide = (page: Page) => page.waitForRequest((r) => r.method() === 'POST' && /\/approvals\/\d+\/decide$/.test(r.url()))

// Fire an approval the way request_approval posts one, and return its card in the feature conversation.
async function pending(qa: { fire: (k: string, b?: Record<string, unknown>) => Promise<any> }, page: Page, title: string) {
  const { approval } = await qa.fire('approval', { text: title })
  const card = page.locator(`[data-card="approval-${approval}"]`)
  return { id: approval as number, card }
}

test('a pending card offers Approve, Request changes and Reject; Reject with a note closes it as rejected', async ({ page, qa }) => {
  const { card, id } = await pending(qa, page, 'Use skill: team-skills:local-preview')
  await qa.open('/p/demo?c=feature-1')
  await expect(card).toBeVisible()
  await expect(card.locator('[data-action]')).toHaveText(['Approve', 'Request changes', 'Reject'])
  await expect(card.locator('[data-action="reject"]')).toHaveClass(/red/) // red text on the outline button, not a filled one
  await expect(card.locator('[data-action="approve"]')).not.toHaveClass(/red/)

  await card.getByPlaceholder('Note (optional)').fill('No, not for this feature')
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/approval-reject-card.png`, animations: 'disabled' })
  const req = decide(page)
  await card.locator('[data-action="reject"]').click()
  expect((await req).postDataJSON()).toMatchObject({ decision: 'reject', note: 'No, not for this feature' })

  // The card closes with a red "rejected" badge and keeps the note; the buttons are gone.
  const badge = card.locator('[data-state-chip]')
  await expect(badge).toHaveText('rejected')
  await expect(badge).toHaveClass(/red/)
  await expect(card.locator('[data-action]')).toHaveCount(0)
  await expect(card).toContainText('Note: No, not for this feature')
  // The requester hears it in the conversation, as a Decision from the Master.
  const line = page.locator('[id^="m-"]', { hasText: 'Rejected: Use skill: team-skills:local-preview' })
  await expect(line).toContainText('Master')
  await expect(line).toContainText('No, not for this feature')
  await expect.poll(async () => ((await qa.api<any[]>('/approvals?state=rejected')) as any[]).map((a) => a.id)).toContain(id)
})

test('Reject needs no reason: one click, no note in the request', async ({ page, qa }) => {
  const { card } = await pending(qa, page, 'Scope lock: nothing to say')
  await qa.open('/p/demo?c=feature-1')
  const req = decide(page)
  await card.locator('[data-action="reject"]').click()
  const body = (await req).postDataJSON()
  expect(body.decision).toBe('reject')
  expect(body.note).toBeUndefined()
  await expect(card.locator('[data-state-chip]')).toHaveText('rejected')
  await expect(card).not.toContainText('Note:')
})

test('Request changes now reads amber, Approve green and Reject red', async ({ page, qa }) => {
  const a = await pending(qa, page, 'Design: first')
  const b = await pending(qa, page, 'Design: second')
  await qa.open('/p/demo?c=feature-1')
  await a.card.locator('[data-action="changes"]').click()
  await expect(a.card.locator('[data-state-chip]')).toHaveText('changes requested')
  await expect(a.card.locator('[data-state-chip]')).toHaveClass(/amber/)
  await b.card.locator('[data-action="approve"]').click()
  await expect(b.card.locator('[data-state-chip]')).toHaveText('approved')
  await expect(b.card.locator('[data-state-chip]')).toHaveClass(/emerald/)
})

test('History has Features and Approvals tabs; Features is unchanged and opens first', async ({ page, qa }) => {
  await qa.open('/p/demo?c=history')
  await expect(page.getByRole('tab', { name: 'Features' })).toHaveAttribute('data-state', 'active')
  await expect(page.getByRole('tab', { name: 'Approvals' })).toHaveAttribute('data-state', 'inactive')
  await expect(page.getByRole('columnheader', { name: 'feature' })).toBeVisible()
  await expect(page.getByRole('button', { name: /F-3\s+Order notes/ })).toBeVisible()
})

test('History > Approvals lists resolved approvals only, filtered by outcome', async ({ page, qa }) => {
  await pending(qa, page, 'Design: still waiting') // pending ones live in the Inbox, not in History
  await qa.open('/p/demo?c=history')
  await page.getByRole('tab', { name: 'Approvals' }).click()
  const list = page.locator('[data-approvals-list]')
  const cards = list.locator('[data-card^="approval-"]')
  const badge = (outcome: string) => list.locator('[data-slot="badge"]', { hasText: new RegExp(`^${outcome}$`) })
  const outcome = page.getByRole('combobox', { name: 'Outcome' })

  // All: the three seeded outcomes, each with its feature and colour, and no pending card.
  await expect(outcome).toHaveText('All')
  await expect(cards).toHaveCount(3)
  await expect(list).not.toContainText('still waiting')
  await expect(badge('approved')).toHaveClass(/emerald/)
  await expect(badge('changes requested')).toHaveClass(/amber/)
  await expect(badge('rejected')).toHaveClass(/red/)
  await expect(list.locator('[data-card^="approval-"]', { hasText: 'Scope lock: Order notes export' })).toContainText('F-3')
  await expect(list).toContainText('Note: PDF is out of scope for this release.')
  await page.screenshot({ path: `${SHOTS}/approval-outcomes.png`, animations: 'disabled' })

  const pick = async (label: string) => {
    await outcome.click()
    await page.getByRole('option', { name: label, exact: true }).click()
    await expect(outcome).toHaveText(label)
  }
  await pick('Rejected')
  await expect(cards).toHaveCount(1)
  await expect(cards.first()).toContainText('Scope lock: Order notes export')
  await expect(badge('rejected')).toBeVisible()
  await pick('Approved')
  await expect(cards).toHaveCount(1)
  await expect(cards.first()).toContainText('Design: Export orders to CSV')
  await pick('Changes requested')
  await expect(cards).toHaveCount(1)
  await expect(cards.first()).toContainText('Use skill: team-skills:local-preview')
  await pick('All')
  await expect(cards).toHaveCount(3)
})

test('an outcome nobody has gets the empty line', async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/approvals\?state=all$/, async (route) => {
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: list.filter((a: any) => a.state !== 'rejected') })
  })
  await qa.open('/p/demo?c=history')
  await page.getByRole('tab', { name: 'Approvals' }).click()
  await page.getByRole('combobox', { name: 'Outcome' }).click()
  await page.getByRole('option', { name: 'Rejected', exact: true }).click()
  await expect(page.locator('[data-approvals-empty]')).toHaveText('No approvals with this outcome.')
  await expect(page.locator('[data-approvals-list]')).toHaveCount(0)
})
