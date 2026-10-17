import type { Locator } from '@playwright/test'
import { expect, test } from './fixtures'

// A badge without a note is not a tooltip trigger at all (Radix marks every trigger with data-state), so hovering
// it can never open one. Deterministic, unlike sampling role=tooltip while another tooltip is still closing.
const expectNoTooltip = async (badge: Locator) => {
  await badge.hover()
  await expect(badge).not.toHaveAttribute('data-state')
}

// F-7: Inbox commit cards say what the Lead did. Seed commit #1 is Backend's, approved by the Lead with a note;
// the `commit_request` kind files a Lead-own request straight to master_review.
test('Inbox commit cards show "Lead approved" with the note on hover, and "Lead\'s own, not reviewed"', async ({ page, qa }) => {
  const fired = await qa.fire('commit_request', { text: 'badge check' })
  await qa.open('/p/demo?c=inbox')

  const approved = page.locator('[data-card="commit-1"] [data-lead]')
  await expect(approved).toHaveText('Lead approved')
  await expect(approved).toHaveAttribute('data-lead', 'approved')
  await approved.hover()
  await expect(approved).toHaveAttribute('data-state', /open/)
  await expect(page.getByRole('tooltip')).toHaveText('Reviewed: streaming is fine, unit tests pass.')

  const own = page.locator(`[data-card="commit-${fired.commit}"] [data-lead]`)
  await expect(own).toHaveText("Lead's own, not reviewed")
  await expect(own).toHaveAttribute('data-lead', 'own')
  await expectNoTooltip(own)
})

test('the review view states the same Lead outcome', async ({ page, qa }) => {
  await qa.open('/p/demo?c=inbox&review=1')
  await expect(page.getByText('Approved by Lead: Reviewed: streaming is fine, unit tests pass.')).toBeVisible()
})

test('after the Master approves, the badge stays "Lead approved" and the Lead note tooltip goes, live', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator('[data-card="commit-1"]')
  await card.scrollIntoViewIfNeeded()
  await card.getByRole('button', { name: 'Approve' }).click()
  // Approving runs a real commit in the sandbox repo, which can outlast the default 5 s.
  await expect(card.getByText('committed', { exact: true })).toBeVisible({ timeout: 15_000 })
  const badge = card.locator('[data-lead]')
  await expect(badge).toHaveText('Lead approved')
  await expectNoTooltip(badge)
})
