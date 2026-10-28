import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-34: a role can pull back its own Inbox request (or, as the feature's Lead, any in its feature) with a one-line reason.
// A withdrawn item gets a grey "withdrawn" chip and "Withdrawn: <reason>" with no buttons, on an approval card, a commit
// card and a question card; it is not red (that is a no) and it never shows the Lead's review badge. History > Approvals
// has a Withdrawn filter.
//
// Part 1 pins how each state renders: the items are created with the existing fire kinds, and the daemon's answers are
// rewritten with page.route to the withdrawn state. Part 2 (below) drives the real withdrawal through the sandbox's
// `withdraw {item, id, role?, reason?}` fire kind (the item's kind is `item`: `kind` is the fire kind itself).

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const WHY = 'superseded by #97'

// Rewrite the messages of #feature-1 so the card of each listed item reads as withdrawn (the daemon's frame would).
async function asWithdrawn(page: Page, ids: { approval?: number; commit?: number; question?: number }, note: string | null = WHY, extra?: (m: any) => void) {
  await page.route(/\/api\/p\/demo\/messages\?.*conversation=feature-1/, async (route) => {
    const res = await route.fetch()
    const list = await res.json()
    for (const m of list) {
      if (m.approval?.id === ids.approval) m.approval = { ...m.approval, state: 'withdrawn', note }
      if (m.commit?.id === ids.commit) m.commit = { ...m.commit, state: 'withdrawn', note }
      if (m.question?.id === ids.question) m.question = { ...m.question, state: 'withdrawn', note }
      extra?.(m)
    }
    await route.fulfill({ response: res, json: list })
  })
}

const chip = (card: ReturnType<Page['locator']>) => card.locator('[data-slot="badge"]').first()
// F-40: an approval card's first badge is its type (Design, Scope lock, ...); its state is `[data-state-chip]`.
const stateChip = (card: ReturnType<Page['locator']>) => card.locator('[data-state-chip]')

test('a withdrawn approval is a grey chip and "Withdrawn: <reason>", with no buttons', async ({ page, qa }) => {
  const approval = (await qa.fire('approval', { text: 'Design: pull this back' })).approval as number
  await asWithdrawn(page, { approval })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${approval}"]`)
  await expect(card).toBeVisible()
  await expect(stateChip(card)).toHaveText('withdrawn')
  await expect(stateChip(card)).toHaveClass(/bg-gray-100/)
  await expect(stateChip(card)).not.toHaveClass(/red/)
  await expect(card.locator('[data-withdrawn]')).toHaveText(`Withdrawn: ${WHY}`)
  await expect(card.locator('[data-action]')).toHaveCount(0)
  await expect(card.getByPlaceholder('Note (optional)')).toHaveCount(0)
  await expect(card).not.toContainText('Note:') // not the "Note:" line a decided approval gets
})

test('a withdrawn commit request is grey, shows the reason, has no buttons and no Lead badge', async ({ page, qa }) => {
  const commit = (await qa.fire('commit_request', { text: 'feat: pull this back' })).commit as number // by a Lead: straight to the Master
  await asWithdrawn(page, { commit })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="commit-${commit}"]`)
  await expect(card).toBeVisible()
  await expect(chip(card)).toHaveText('withdrawn')
  await expect(chip(card)).toHaveClass(/bg-gray-100/)
  await expect(card.locator('[data-withdrawn]')).toHaveText(`Withdrawn: ${WHY}`)
  await expect(card.getByRole('button', { name: /^(Approve|Reject|Commit anyway)$/ })).toHaveCount(0)
  await expect(card.getByPlaceholder(/^Note/)).toHaveCount(0)
  await expect(card.locator('[data-lead]')).toHaveCount(0) // "Lead's own, not reviewed" says nothing about a withdrawn request
  await expect(card).toContainText('feat: pull this back') // the message stays readable, as plain text
})

test('a withdrawn question card shows the chip and the reason; without one it names who asked', async ({ page, qa }) => {
  const question = (await qa.fire('card', { role: 'product' })).question as number
  await asWithdrawn(page, { question })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-question-card="${question}"]`)
  await expect(card).toBeVisible()
  await expect(card).toHaveAttribute('data-state', 'withdrawn')
  const withdrawn = card.locator('[data-withdrawn] [data-slot="badge"]') // the card's first badge is its purpose ("Interview")
  await expect(withdrawn).toHaveText('withdrawn')
  await expect(withdrawn).toHaveClass(/bg-gray-100/)
  await expect(card.locator('[data-withdrawn]')).toContainText(`Withdrawn: ${WHY}`)
  await expect(card.getByRole('button')).toHaveCount(0)

  // a card withdrawn before the reason column existed (an older row) keeps the old line
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await asWithdrawn(page, { question }, null)
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator(`[data-question-card="${question}"] [data-withdrawn]`)).toContainText('Withdrawn by Product')
})

test('a withdrawn partner no longer ties a request to a commit group', async ({ page, qa }) => {
  const a = (await qa.fire('commit_request', { text: 'feat: base' })).commit as number
  const b = (await qa.fire('commit_request', { text: 'feat: depends on the base' })).commit as number
  let baseWithdrawn = false
  await page.route(/\/api\/p\/demo\/messages\?.*conversation=feature-1/, async (route) => {
    const res = await route.fetch()
    const list = await res.json()
    for (const m of list) {
      if (m.commit?.id === b) m.commit = { ...m.commit, requires: [a] }
      if (m.commit?.id === a && baseWithdrawn) m.commit = { ...m.commit, state: 'withdrawn', note: WHY }
    }
    await route.fulfill({ response: res, json: list })
  })
  await qa.open('/p/demo?c=feature-1')
  const note = page.locator(`[data-card="commit-${b}"] [data-group-note]`)
  await expect(note).toContainText(`Part of a commit group with #${a}`) // while the base is open, they are one group

  baseWithdrawn = true
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator(`[data-card="commit-${a}"] [data-withdrawn]`)).toBeVisible()
  await expect(page.locator(`[data-card="commit-${b}"]`)).toBeVisible()
  await expect(note).toHaveCount(0) // a withdrawn partner is not an open one
})

test('History > Approvals has a Withdrawn filter that lists only withdrawn approvals, with the reason', async ({ page, qa }) => {
  const rows = [
    { id: 41, feature_id: 1, role: 'backend', turn_id: null, title: 'Scope lock: export all', summary: 'Everything.', state: 'withdrawn', note: WHY, created_at: Date.now() / 1000 - 600, decided_at: Date.now() / 1000 - 300 },
    { id: 42, feature_id: 1, role: 'product', turn_id: null, title: 'Design: kept', summary: 'Fine.', state: 'approved', note: null, created_at: Date.now() / 1000 - 900, decided_at: Date.now() / 1000 - 800 },
  ]
  await page.route(/\/api\/p\/demo\/approvals\?state=all$/, (route) => route.fulfill({ json: rows }))
  await qa.open('/p/demo?c=history')
  await page.getByRole('tab', { name: 'Approvals' }).click()
  await expect(page.locator('[data-approvals-list] [data-card^="approval-"]')).toHaveCount(2)

  await page.getByRole('combobox', { name: 'Outcome' }).click()
  await expect(page.getByRole('option')).toHaveText(['All', 'Approved', 'Changes requested', 'Rejected', 'Withdrawn'])
  await page.getByRole('option', { name: 'Withdrawn' }).click()
  const list = page.locator('[data-approvals-list] [data-card^="approval-"]')
  await expect(list).toHaveCount(1)
  await expect(list).toContainText('Scope lock: export all')
  await expect(list.locator('[data-withdrawn]')).toHaveText(`Withdrawn: ${WHY}`)
  await expect(stateChip(list)).toHaveText('withdrawn')

  await page.getByRole('combobox', { name: 'Outcome' }).click()
  await page.getByRole('option', { name: 'Rejected' }).click()
  await expect(page.locator('[data-approvals-empty]')).toBeVisible() // none of those here
})


// ---------------------------------------------------------------- part 2: the real withdrawal

const TOKEN = 'qa-sandbox-token' // the sandbox's default, as in fixtures.ts
const inboxCount = async (page: Page) => Number((await page.locator('aside').getByRole('button', { name: /^Inbox/ }).innerText()).replace(/\D+/g, '') || 0)
type Sandbox = { fire: (k: string, b?: Record<string, unknown>) => Promise<Record<string, any>> }

const withdraw = (qa: Sandbox, item: 'approval' | 'commit' | 'card', id: number, extra: Record<string, unknown> = {}) => qa.fire('withdraw', { item, id, reason: WHY, ...extra })

test('a withdrawn approval leaves the Inbox at once and its card in the conversation turns grey', async ({ page, qa }) => {
  const approval = (await qa.fire('approval', { text: 'Design: live withdraw' })).approval as number
  await qa.open('/p/demo?c=inbox')
  const inboxCard = page.locator(`[data-card="approval-${approval}"]`)
  await expect(inboxCard.locator('[data-action="approve"]')).toBeVisible()
  const before = await inboxCount(page)

  const r = await withdraw(qa, 'approval', approval)
  expect(r).toMatchObject({ ok: true, item: 'approval', id: approval, state: 'withdrawn', note: WHY, conversation: 'feature-1' })
  await expect(inboxCard).toHaveCount(0) // gone from the Inbox without a reload
  await expect.poll(() => inboxCount(page)).toBe(before - 1)

  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${approval}"]`)
  await expect(stateChip(card)).toHaveText('withdrawn')
  await expect(card.locator('[data-withdrawn]')).toHaveText(`Withdrawn: ${WHY}`)
  await expect(card.locator('[data-action]')).toHaveCount(0)
  await expect(page.locator('[id^="m-"]', { hasText: `Approval #${approval} withdrawn by lead: ${WHY}` })).toBeVisible() // the chat line
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/withdraw-approval-card.png`, animations: 'disabled' })
})

test('a withdrawn commit request leaves the Inbox and its card shows the reason, with no Lead badge', async ({ page, qa }) => {
  const commit = (await qa.fire('commit_request', { text: 'feat: live withdraw' })).commit as number
  await qa.open('/p/demo?c=inbox')
  const inboxCard = page.locator(`[data-card="commit-${commit}"]`)
  await expect(inboxCard.getByRole('button', { name: 'Approve' })).toBeVisible()

  const r = await withdraw(qa, 'commit', commit)
  expect(r).toMatchObject({ item: 'commit', id: commit, state: 'withdrawn', note: WHY })
  await expect(inboxCard).toHaveCount(0)

  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="commit-${commit}"]`)
  await expect(chip(card)).toHaveText('withdrawn')
  await expect(chip(card)).toHaveClass(/bg-gray-100/)
  await expect(card.locator('[data-withdrawn]')).toHaveText(`Withdrawn: ${WHY}`)
  await expect(card.getByRole('button', { name: /^(Approve|Reject|Commit anyway)$/ })).toHaveCount(0)
  await expect(card.locator('[data-lead]')).toHaveCount(0)
  await expect(page.locator('[id^="m-"]', { hasText: `Commit request #${commit} withdrawn by lead: ${WHY}` })).toBeVisible()
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/withdraw-commit-card.png`, animations: 'disabled' })
})

test('a withdrawn question card leaves the Inbox and shows its chip and reason', async ({ page, qa }) => {
  const question = (await qa.fire('card', { role: 'product' })).question as number
  await qa.open('/p/demo?c=inbox')
  const inboxCard = page.locator(`[data-question-card="${question}"]`)
  await expect(inboxCard).toHaveAttribute('data-state', 'pending')

  const r = await withdraw(qa, 'card', question)
  expect(r).toMatchObject({ item: 'card', id: question, state: 'withdrawn', note: WHY })
  await expect(inboxCard).toHaveCount(0)

  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-question-card="${question}"]`)
  await expect(card).toHaveAttribute('data-state', 'withdrawn')
  await expect(card.locator('[data-withdrawn] [data-slot="badge"]')).toHaveText('withdrawn')
  await expect(card.locator('[data-withdrawn]')).toContainText(`Withdrawn: ${WHY}`)
  await expect(card.getByRole('button')).toHaveCount(0)
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/withdraw-question-card.png`, animations: 'disabled' })
})

test("the feature's Lead can withdraw another role's request; a role that is neither is refused and nothing changes", async ({ page, qa, request }) => {
  const approval = (await qa.fire('approval', { role: 'backend', text: 'Scope lock: not mine to pull back' })).approval as number
  await qa.open('/p/demo?c=inbox')
  const card = page.locator(`[data-card="approval-${approval}"]`)
  await expect(card.locator('[data-action="approve"]')).toBeVisible()
  const before = await inboxCount(page)

  const refused = await request.post('/qa/fire', { headers: { Authorization: `Bearer ${TOKEN}` }, data: { kind: 'withdraw', item: 'approval', id: approval, role: 'qa', reason: 'not mine' } })
  expect(refused.status()).toBe(403)
  await expect(card.locator('[data-action="approve"]')).toBeVisible() // still pending, still the Master's to decide
  expect(await inboxCount(page)).toBe(before)

  await withdraw(qa, 'approval', approval, { role: 'lead' }) // F-1's Lead
  await expect(card).toHaveCount(0)
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator('[id^="m-"]', { hasText: `Approval #${approval} withdrawn by lead: ${WHY}` })).toBeVisible()
  await expect(page.locator(`[data-card="approval-${approval}"] [data-withdrawn]`)).toHaveText(`Withdrawn: ${WHY}`)
})

test('History > Approvals lists a withdrawn approval under the Withdrawn filter, with its reason', async ({ page, qa }) => {
  const approval = (await qa.fire('approval', { text: 'Use skill: team-skills:local-preview' })).approval as number
  await withdraw(qa, 'approval', approval, { reason: 'filed by mistake' })
  await qa.open('/p/demo?c=history')
  await page.getByRole('tab', { name: 'Approvals' }).click()
  await page.getByRole('combobox', { name: 'Outcome' }).click()
  await page.getByRole('option', { name: 'Withdrawn', exact: true }).click()
  const list = page.locator('[data-approvals-list] [data-card^="approval-"]')
  await expect(list).toHaveCount(1)
  await expect(list).toContainText('team-skills:local-preview')
  await expect(list.locator('[data-withdrawn]')).toHaveText('Withdrawn: filed by mistake')
  await expect(stateChip(list)).toHaveText('withdrawn')
  await page.getByRole('combobox', { name: 'Outcome' }).click()
  await page.getByRole('option', { name: 'Approved', exact: true }).click()
  await expect(list.filter({ hasText: 'team-skills:local-preview' })).toHaveCount(0) // not under another outcome
})

test('withdrawing the half of a commit group that depends on the other leaves the other as an ordinary request', async ({ page, qa, request }) => {
  const fired = await qa.fire('commit_group')
  const commits = fired.commits as number[]
  const group = fired.group as number
  const [base, dependent] = commits
  await qa.open('/p/demo?c=inbox')
  const groupCard = page.locator(`[data-card="commit-group-${group}"]`)
  await expect(groupCard).toBeVisible()
  await expect(groupCard.locator(`[data-member="${base}"]`)).toBeVisible()
  await expect(groupCard.locator(`[data-member="${dependent}"]`)).toBeVisible()

  // the base cannot go while the dependent is open (the daemon says why); the group is untouched
  const refused = await request.post('/qa/fire', { headers: { Authorization: `Bearer ${TOKEN}` }, data: { kind: 'withdraw', item: 'commit', id: base, reason: WHY } })
  expect(refused.status()).toBe(409)
  expect(JSON.stringify(await refused.json())).toContain(`#${dependent} requires #${base}`)
  await expect(groupCard).toBeVisible()

  await withdraw(qa, 'commit', dependent) // the dependent goes first: the group dissolves
  await expect(groupCard).toHaveCount(0)
  const single = page.locator(`[data-card="commit-${base}"]`)
  await expect(single.getByRole('button', { name: 'Approve' })).toBeVisible() // the base is a plain Inbox item now
  await expect(single.locator('[data-group-note]')).toHaveCount(0)

  await withdraw(qa, 'commit', base)
  await expect(single).toHaveCount(0)
})
