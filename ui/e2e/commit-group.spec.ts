import { expect, test } from './fixtures'

// F-11: linked commit requests (`requires`) show as one Inbox item and land together. The sandbox kind
// `commit_group` files a backend request plus a frontend request that requires it, in F-1.

test('a group is one Inbox item, and Commit group waits until every member is Lead-approved', async ({ page, qa }) => {
  const fired = await qa.fire('commit_group', { lead_review: true })
  const [base, ui] = fired.commits as number[]
  await qa.open('/p/demo?c=inbox')

  const group = page.locator(`[data-card="commit-group-${fired.group}"]`)
  await expect(group).toContainText('F-1: group backend half')
  await expect(group).toContainText('(2 requests, 2 files)')
  await expect(group.locator('[data-member]')).toHaveCount(2)
  await expect(group.locator(`[data-member="${ui}"]`)).toContainText('lead review')
  // Members aren't listed again as single requests.
  await expect(page.locator(`[data-card="commit-${base}"], [data-card="commit-${ui}"]`)).toHaveCount(0)

  const commit = group.getByRole('button', { name: 'Commit group' })
  await expect(commit).toBeDisabled()
  await group.locator('[data-group-commit]').hover()
  await expect(page.getByRole('tooltip')).toContainText('Lead approval')
  await page.screenshot({ path: `${process.env.SHOT_DIR || 'test-results/shots'}/commit-group.png`, animations: 'disabled' })
})

test('a ready group commits both from one button; the conversation card points to the Inbox', async ({ page, qa }) => {
  const fired = await qa.fire('commit_group')
  const [base, ui] = fired.commits as number[]

  // In the feature conversation each member says it's part of a group and has no Approve of its own.
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`main [data-card="commit-${ui}"]`)
  await expect(card.locator('[data-group-note]')).toHaveText(`Part of a commit group with #${base}: commit it from the Inbox.`)
  await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  await expect(card.getByRole('button', { name: 'Reject' })).toBeVisible()
  // The base half doesn't list `requires`, but it is required by the UI half.
  await expect(page.locator(`main [data-card="commit-${base}"] [data-group-note]`)).toContainText(`#${ui}`)

  await page.locator('aside').getByText('Inbox', { exact: true }).click()
  const group = page.locator(`[data-card="commit-group-${fired.group}"]`)
  await group.getByRole('button', { name: 'Commit group' }).click()
  await expect(group).toHaveCount(0)

  const states = async () => {
    const all = (await qa.api<any[]>('/commits?state=all')).filter((c) => c.id === base || c.id === ui)
    return all.map((c) => c.state)
  }
  await expect.poll(states).toEqual(['committed', 'committed'])
})
