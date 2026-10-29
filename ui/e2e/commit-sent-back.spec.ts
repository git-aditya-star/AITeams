import { appendFileSync } from 'node:fs'
import { join } from 'node:path'
import type { APIRequestContext, Locator, Page } from '@playwright/test'
import { expect, test, PID, TOKEN } from './fixtures'

// F-45: the Master commits only what the Lead reviewed. A file edited after the review makes the approve commit nothing and
// send the request (the whole group) back to Lead review; there is no "commit anyway". The sandbox has no kind that edits a
// file, so the spec writes into the sandbox repo itself (its path is in GET /api/projects).
const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const AUTH = { Authorization: `Bearer ${TOKEN}` }

async function editAfterReview(request: APIRequestContext, rel: string) {
  const projects = await (await request.get('/api/projects', { headers: AUTH })).json()
  const repo = projects.find((p: { id: string }) => p.id === PID).path as string
  appendFileSync(join(repo, rel), 'edited after the Lead reviewed it\n')
}

const toast = (page: Page) => page.locator('[data-toast="sent_back"]')
const anyway = (scope: Locator | Page) => scope.getByRole('button', { name: /anyway/i })

test('an edited file: Approve in the Inbox sends the request back with a toast, no "anyway", and the card says why', async ({ page, qa, request }) => {
  const fired = await qa.fire('commit_request', { text: 'add the importer' })
  const id = fired.commit as number
  const file = (await qa.api<any[]>('/commits?state=master_review')).find((c) => c.id === id).files[0] as string
  await qa.open('/p/demo?c=inbox')

  const card = page.locator(`[data-card="commit-${id}"]`)
  await expect(card.getByRole('button', { name: 'Approve' })).toBeVisible()
  await expect(anyway(card)).toHaveCount(0)

  await editAfterReview(request, file)
  await card.getByRole('button', { name: 'Approve' }).click()

  await expect(toast(page).locator('button[title]')).toHaveAttribute('title', `Sent back to Lead review: ${file}`)
  await page.screenshot({ path: `${SHOTS}/commit-sent-back-toast.png`, animations: 'disabled' })
  await expect(card).toHaveCount(0) // it leaves through the commit broadcast
  await expect.poll(async () => (await qa.api<any[]>('/commits?state=lead_review')).find((c) => c.id === id)?.note).toBe(`changed after review: ${file}`)
  expect((await qa.api<any[]>('/commits?state=committed')).some((c) => c.id === id)).toBe(false)

  // The conversation card shows the note and has no buttons: the Lead has to approve it again.
  await qa.open('/p/demo?c=feature-1')
  const back = page.locator(`main [data-card="commit-${id}"]`)
  await back.scrollIntoViewIfNeeded()
  await expect(back).toContainText('Waiting on Lead review.')
  await expect(back.locator('[data-sent-back]')).toHaveText(`changed after review: ${file}`)
  await expect(back.getByRole('button', { name: /^(Approve|Reject)$/ })).toHaveCount(0)
  await expect(anyway(back)).toHaveCount(0)
  await back.screenshot({ path: `${SHOTS}/commit-sent-back-card.png`, animations: 'disabled' })
})

test('the review view warns that files changed since review, and Approve there updates the view in place', async ({ page, qa, request }) => {
  const id = (await qa.fire('commit_request', { text: 'add the exporter' })).commit as number
  const file = (await qa.api<any[]>('/commits?state=master_review')).find((c) => c.id === id).files[0] as string
  await editAfterReview(request, file)
  await qa.open(`/p/demo?c=inbox&review=${id}`)

  const warning = page.locator('[data-stale-warning]')
  await expect(warning).toHaveText(`Files changed since review: ${file}. Approving sends the request back to the Lead.`)
  await expect(anyway(page)).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/commit-sent-back-review.png`, animations: 'disabled' })

  // The Inbox stays mounted behind the review, so the review's own Approve / Reject section is the scope.
  const actions = page.locator('section', { hasText: 'Commit message' })
  await expect(anyway(actions)).toHaveCount(0)
  await actions.getByRole('button', { name: 'Approve' }).click()
  await expect(toast(page)).toHaveCount(1)
  await expect(page.locator('[data-sent-back]')).toHaveText(`changed after review: ${file}`)
  await expect(actions).toHaveCount(0) // Approve and Reject are gone
  await expect(page.getByRole('heading', { name: `Commit #${id}` }).locator('..')).toContainText('lead review')
  await expect(page.locator('[data-stale-warning]')).toHaveCount(0)
})

test('a group with one edited member: Commit group sends every member back, with a toast naming the file', async ({ page, qa, request }) => {
  const fired = await qa.fire('commit_group')
  const [base, ui] = fired.commits as number[]
  const open = await qa.api<any[]>('/commits?state=master_review')
  const baseFile = open.find((c) => c.id === base).files[0] as string
  await qa.open('/p/demo?c=inbox')

  const group = page.locator(`[data-card="commit-group-${fired.group}"]`)
  await expect(group.getByRole('button', { name: 'Commit group' })).toBeEnabled()
  await expect(anyway(group)).toHaveCount(0)

  await editAfterReview(request, baseFile)
  await group.getByRole('button', { name: 'Commit group' }).click()

  await expect(toast(page).locator('button[title]')).toHaveAttribute('title', `Sent back to Lead review: ${baseFile}`)
  await expect(group).toHaveCount(0)
  const states = async () => {
    const all = await qa.api<any[]>('/commits?state=lead_review')
    return [base, ui].map((n) => all.find((c) => c.id === n)?.note)
  }
  await expect.poll(states).toEqual([`changed after review: ${baseFile}`, `changed after review: #${base} ${baseFile} (same group)`])
  expect((await qa.api<any[]>('/commits?state=committed')).some((c) => c.id === base || c.id === ui)).toBe(false)

  // Both members now wait on the Lead; the one that did not change says it went back with its group.
  await qa.open('/p/demo?c=feature-1')
  for (const n of [base, ui]) await expect(page.locator(`main [data-card="commit-${n}"] [data-sent-back]`)).toContainText('changed after review')
  await expect(page.locator(`main [data-card="commit-${ui}"] [data-sent-back]`)).toContainText('(same group)')
})
