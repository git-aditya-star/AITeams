import { expect, test } from './fixtures'

test('review view lists the three seeded files with counts, binary collapsed', async ({ page, qa }) => {
  await qa.open('/p/demo?c=inbox')
  await page.locator('[data-card="commit-1"]').getByRole('button', { name: 'Review' }).click()
  await expect(page).toHaveURL(/review=1/)
  await expect(page.getByRole('heading', { name: 'Commit #1' })).toBeVisible()
  await expect(page.getByText('Approved by Lead')).toBeVisible()

  const row = (path: string) => page.locator(`[data-file="${path}"]`)
  await expect(page.locator('[data-file]')).toHaveCount(3)
  await expect(row('src/orders.py')).toContainText('+2 −1')
  await expect(row('src/export_csv.py')).toContainText('new')
  await expect(row('src/export_csv.py')).toContainText('+14 −0')
  await expect(row('assets/logo.png')).toContainText('binary')
  await expect(row('assets/logo.png')).toContainText('+0 −0')

  const section = (path: string) => page.locator(`[id="diff-${path}"]`)
  await expect(section('src/orders.py').locator('table')).toContainText('customer=None')
  await expect(section('assets/logo.png').locator('table')).toHaveCount(0)
  await expect(section('assets/logo.png')).not.toContainText('Binary file, no text diff.')
  await section('assets/logo.png').getByRole('button').click()
  await expect(section('assets/logo.png')).toContainText('Binary file, no text diff.')

  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(page).not.toHaveURL(/review=/)
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible()
})
