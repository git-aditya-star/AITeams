import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-22: two Leads. Lead 1 keeps the internal id `lead`, Lead 2 is `lead-2`; people see "Lead 1" / "Lead 2".

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const NAMES = ['Manager', 'Lead 1', 'Lead 2', 'Product', 'Frontend', 'Backend', 'Qa']
const IDS = ['manager', 'lead', 'lead-2', 'product', 'frontend', 'backend', 'qa']

// Rewrite F-1's `lead` in the /features payload the UI loads.
const withLead = async (page: Page, lead: string | null) =>
  page.route(/\/api\/p\/demo\/features(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: list.map((f: any) => (f.key === 'F-1' ? { ...f, lead } : f)) })
  })

test('the Dashboard Team card shows Lead 1 and Lead 2 as whole blocks, at 1400 and at 1100', async ({ page, qa }) => {
  for (const width of [1400, 1100]) {
    await page.setViewportSize({ width, height: 900 })
    await qa.open('/p/demo')
    const card = page.locator('[data-card="team"]')
    await expect(card.locator('[data-ring]')).toHaveCount(7)
    await expect(card.locator('[data-ring-name]')).toHaveText(NAMES)
    const box = (await card.boundingBox())!
    for (const id of IDS) {
      const b = (await card.locator(`[data-ring="${id}"]`).boundingBox())!
      expect(b.x).toBeGreaterThanOrEqual(box.x)
      expect(b.x + b.width).toBeLessThanOrEqual(box.x + box.width + 1)
      expect(b.y + b.height).toBeLessThanOrEqual(box.y + box.height + 1)
      expect(await card.locator(`[data-ring="${id}"] [data-ring-name]`).evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
    }
    await expect(card.locator('[data-ring="lead"] span.rounded-full').last()).toHaveText('L1')
    await expect(card.locator('[data-ring="lead-2"] span.rounded-full').last()).toHaveText('L2')
    await page.screenshot({ path: `${SHOTS}/two-leads-dashboard-${width}.png`, animations: 'disabled' })
  }
})

test('the top bar and the DMs list Lead 1 and Lead 2; Lead 2 has its own DM', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  const strip = page.locator('main > div').filter({ has: page.getByRole('button', { name: /^Manager\b/ }) }).first()
  await expect(strip.getByRole('button', { name: /^Lead 1\b/ })).toBeVisible()
  await expect(strip.getByRole('button', { name: /^Lead 2\b/ })).toBeVisible()
  const dms = page.locator('aside').getByRole('button', { name: /^(Manager|Lead 1|Lead 2|Product|Frontend|Backend|Qa)$/ })
  await expect(dms).toHaveText(NAMES)
  await dms.nth(2).click()
  await expect(page).toHaveURL(/c=dm-lead-2/)
  await expect(page.getByPlaceholder('Message Lead 2')).toBeVisible()
})

test('messages from Lead 2 get its own avatar and name', async ({ page, qa }) => {
  await qa.fire('message', { conversation: 'general', role: 'lead-2', text: 'Lead 2 reporting in' })
  await qa.fire('message', { conversation: 'general', role: 'lead', text: 'Lead 1 reporting in' })
  await qa.open('/p/demo?c=general')
  const row = (text: string) => page.locator('[id^="m-"]', { hasText: text })
  await expect(row('Lead 2 reporting in').locator('span.font-bold')).toHaveText('Lead 2')
  await expect(row('Lead 2 reporting in').locator('span.rounded-full').first()).toHaveText('L2')
  await expect(row('Lead 1 reporting in').locator('span.font-bold')).toHaveText('Lead 1')
  await expect(row('Lead 1 reporting in').locator('span.rounded-full').first()).toHaveText('L1')
  await page.screenshot({ path: `${SHOTS}/two-leads-chat.png`, animations: 'disabled' })
})

test('@lead-1 and @lead-2 autocomplete and highlight whole; a bare @lead still highlights', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  const box = page.getByPlaceholder('Message # general')
  await box.fill('@lead')
  await expect(page.getByRole('option')).toHaveText(['@lead-1 Lead 1', '@lead-2 Lead 2'])
  await box.press('-')
  await expect(page.getByRole('option')).toHaveCount(2) // the hyphen keeps the picker open on the same two
  await box.press('2')
  await expect(page.getByRole('option')).toHaveText(['@lead-2 Lead 2'])
  await page.keyboard.press('Enter')
  await expect(box).toHaveValue('@lead-2 ')
  await box.pressSequentially('and @lead-1 and @lead please look')
  await expect(page.getByRole('option')).toHaveCount(0)

  await qa.fire('message', { conversation: 'general', role: 'product', text: '@lead-2 and @lead-1 and @lead, also `@lead-2` in code', mentions: ['lead-2'] })
  const msg = page.locator('[id^="m-"]', { hasText: 'also' }).last()
  await expect(msg.locator('.mention')).toHaveText(['@lead-2', '@lead-1', '@lead']) // whole, never `@lead` + "-2"; code is untouched
  await page.screenshot({ path: `${SHOTS}/two-leads-mentions.png`, animations: 'disabled' })
})

test("F-1's header names its Lead, and picking the other one PATCHes {lead}", async ({ page, qa }) => {
  const f1 = ((await qa.api<any[]>('/features?history=1')) as any[]).find((f) => f.key === 'F-1')
  expect(f1.lead).toBe('lead')
  await qa.open('/p/demo?c=feature-1')
  const lead = page.getByRole('combobox', { name: 'Lead', exact: true })
  await expect(lead).toHaveText('Lead 1')
  await page.screenshot({ path: `${SHOTS}/two-leads-header.png`, animations: 'disabled' })
  await lead.click()
  await expect(page.getByRole('option')).toHaveText(['not assigned', 'Lead 1', 'Lead 2'])
  await expect(page.getByRole('option', { name: 'not assigned' })).toHaveAttribute('aria-disabled', 'true') // not a choice
  const patch = page.waitForRequest((r) => r.method() === 'PATCH' && r.url().endsWith(`/features/${f1.id}`))
  await page.getByRole('option', { name: 'Lead 2' }).click()
  expect((await patch).postDataJSON()).toEqual({ lead: 'lead-2' })
  await expect(lead).toHaveText('Lead 2')
  await expect.poll(async () => (((await qa.api<any[]>('/features?history=1')) as any[]).find((f) => f.key === 'F-1')).lead).toBe('lead-2')
})

test('a feature with no Lead yet reads "not assigned"', async ({ page, qa }) => {
  await withLead(page, null)
  await qa.open('/p/demo?c=feature-1')
  await expect(page.getByRole('combobox', { name: 'Lead', exact: true })).toHaveText('not assigned')
})

test("a request filed by Lead 2 reads as the Lead's own, not reviewed", async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/inbox$/, async (route) => {
    const r = await route.fetch()
    const inbox = await r.json()
    await route.fulfill({ response: r, json: { ...inbox, commits: inbox.commits.map((c: any) => (c.id === 1 ? { ...c, role: 'lead-2' } : c)) } })
  })
  await qa.open('/p/demo?c=inbox')
  const badge = page.locator('[data-card="commit-1"] [data-lead]')
  await expect(badge).toHaveText("Lead's own, not reviewed")
  await expect(badge).toHaveAttribute('data-lead', 'own')
})
