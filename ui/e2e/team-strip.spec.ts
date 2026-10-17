import { expect, test } from './fixtures'

const ROLES = ['Manager', 'Lead', 'Product', 'Frontend', 'Backend', 'Qa']

// F-8: each role pill in the top bar shows its name and context %, and no per-role $ cost.
test('top bar role pills show name and context %, no $', async ({ page, qa }) => {
  await qa.open('/p/demo')
  const strip = page.locator('main > div').filter({ has: page.getByRole('button', { name: /^Manager\b/ }) }).first()
  for (const name of ROLES) {
    const pill = strip.getByRole('button', { name: new RegExp(`^${name}\\b`) })
    await expect(pill).toBeVisible()
    await expect(pill).toHaveText(/\d+%/)
    await expect(pill).not.toContainText('$')
  }
})
