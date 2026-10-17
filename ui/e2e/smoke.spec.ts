import { expect, test, TOKEN } from './fixtures'

test('without the cookie the app shows the sign-in page', async ({ browser, baseURL }) => {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  await page.goto(`${baseURL}/p/demo`)
  await expect(page.getByText('aiteams token --link')).toBeVisible()
  await ctx.close()
  expect(TOKEN).toBeTruthy()
})

test('sidebar, dashboard, feature, Inbox, History and the Live tab', async ({ page, qa }) => {
  await qa.open('/p/demo')
  const sidebar = page.locator('aside')
  await expect(sidebar.getByRole('combobox', { name: 'Project' })).toHaveText('demo')
  await expect(sidebar.getByRole('button', { name: /Export orders to CSV/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Needs you' })).toBeVisible()

  await sidebar.getByRole('button', { name: /Export orders to CSV/ }).click()
  await expect(page.getByRole('heading', { name: 'Export orders to CSV' })).toBeVisible()
  // Product, Lead and Backend's three messages in a row are folded into one line until opened.
  await page.getByRole('button', { name: /3 replies/ }).click()
  await expect(page.getByText('PRD is in')).toBeVisible()

  await sidebar.getByRole('button', { name: /^Inbox/ }).click()
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible()
  await expect(page.locator('[data-card="approval-1"]')).toContainText('PRD ready: CSV export scope')
  await expect(page.locator('[data-card="commit-1"]')).toContainText('F-1: add CSV export endpoint')
  await expect(page.getByText('Where should the Export CSV button go?').first()).toBeVisible()

  await sidebar.getByRole('button', { name: /^History/ }).click()
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Order search by customer/ })).toBeVisible()

  await sidebar.getByRole('button', { name: 'Frontend' }).click()
  await page.getByRole('tab', { name: 'Live session' }).click()
  await expect(page).toHaveURL(/c=dm-frontend.*tab=live/)
  await expect(page.getByText('Reading the design notes before I add the button.')).toBeVisible()
  await expect(page.getByText('claude --resume sess-demo-frontend --setting-sources local')).toBeVisible()
})
