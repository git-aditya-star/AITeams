import { expect, test } from './fixtures'

// F-17: the Dashboard's Shipped panel. The sandbox seed has two done features: one whose prd.md has a
// `**Summary:**` line and one without.

test('Shipped shows the counters and the done features, newest first; a row opens its conversation', async ({ page, qa }) => {
  const dash = await qa.api<any>('/dashboard')
  const recent = dash.shipped.recent as { key: string; title: string; summary: string | null; conversation_id: string }[]
  expect(recent.length).toBeGreaterThanOrEqual(2)

  await qa.open('/p/demo')
  const panel = page.locator('[data-shipped]')
  await expect(panel.locator('[data-shipped-week]')).toHaveText(String(dash.shipped.week))
  await expect(panel.locator('[data-shipped-total]')).toHaveText(String(dash.shipped.total))

  // Rows follow the API's newest-first order.
  await expect(panel.locator('[data-shipped-row]')).toHaveCount(recent.length)
  for (const [i, f] of recent.entries()) await expect(panel.locator('[data-shipped-row]').nth(i)).toHaveAttribute('data-shipped-row', f.key)

  const withSummary = recent.find((f) => f.summary)!
  const without = recent.find((f) => !f.summary)!
  await expect(panel.locator(`[data-shipped-row="${withSummary.key}"] [data-shipped-summary]`)).toHaveText(withSummary.summary!)
  await expect(panel.locator(`[data-shipped-row="${without.key}"] [data-shipped-summary]`)).toHaveCount(0)
  await expect(panel.locator(`[data-shipped-row="${without.key}"]`)).toContainText(without.title)
  await page.screenshot({ path: `${process.env.SHOT_DIR || 'test-results/shots'}/shipped.png`, animations: 'disabled' })

  await panel.locator(`[data-shipped-row="${withSummary.key}"] button`).click()
  await expect(page).toHaveURL(new RegExp(`c=${withSummary.conversation_id}`))
  await expect(page.getByRole('heading', { name: withSummary.title })).toBeVisible()
})
