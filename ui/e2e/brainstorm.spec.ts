import { expect, test } from './fixtures'

// F-10 R2: the Master starts a brainstorm round from a feature conversation's header.

test('the Brainstorm button starts a round; the System line names the roles; a second round is refused inline', async ({ page, qa }) => {
  await qa.open('/p/demo?c=inbox')
  await page.locator('aside').getByText('Export orders to CSV').click()
  await page.getByRole('button', { name: 'Brainstorm' }).click()
  const dialog = page.getByRole('dialog', { name: 'Brainstorm' })
  const start = dialog.getByRole('button', { name: 'Start' })

  // An empty (or blank) topic can't start.
  await expect(start).toBeDisabled()
  await dialog.getByLabel('Topic').fill('   ')
  await expect(start).toBeDisabled()

  await dialog.getByLabel('Topic').fill('How should big exports reach the user?')
  await page.screenshot({ path: process.env.SHOT_OUT || 'test-results/shots/brainstorm.png', animations: 'disabled' })
  await start.click()
  await expect(dialog).toHaveCount(0)

  // The round's System line arrives live, with the voices it is waiting for (Product and Lead always have one).
  const line = page.locator('main').getByText(/Brainstorm #\d+ on How should big exports reach the user\?/)
  await expect(line).toBeVisible()
  await expect(line).toContainText('waiting for')
  await expect(line).toContainText(/product/i)
  await expect(line).toContainText(/lead/i)

  // One active round per feature: the 409 shows in the dialog, which stays open.
  await page.getByRole('button', { name: 'Brainstorm' }).click()
  await dialog.getByLabel('Topic').fill('Another topic')
  await dialog.getByRole('button', { name: 'Start' }).click()
  await expect(dialog.locator('.text-red-600')).toBeVisible()
  await expect(dialog).toHaveCount(1)
})
