import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-21: the feature header's design-approval chip. The sandbox seeds an approved `Design:` approval on F-1.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'

// Rewrite F-1's `design` in the /features payload the UI loads.
const withDesign = async (page: Page, design: { state: string; approval_id: number | null; gated: boolean }) =>
  page.route(/\/api\/p\/demo\/features(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: list.map((f: any) => (f.key === 'F-1' ? { ...f, design } : f)) })
  })

test("F-1's header shows the design state from the payload", async ({ page, qa }) => {
  const f1 = ((await qa.api<any[]>('/features?history=1')) as any[]).find((f) => f.key === 'F-1')
  expect(f1.design.state).toBe('approved')
  await qa.open('/p/demo?c=feature-1')
  const chip = page.locator('[data-design-chip]')
  await expect(chip).toHaveText('design approved')
  await expect(chip).toHaveAttribute('data-design-chip', 'approved')
  await expect(chip).toHaveClass(/emerald/)
  await page.screenshot({ path: `${SHOTS}/design-chip.png`, animations: 'disabled' })
  await page.locator('[data-design-link]').hover()
  await expect(page.getByRole('tooltip')).toContainText(`Design approval #${f1.design.approval_id}`)
})

test('clicking the chip scrolls the chat to the design approval card', async ({ page, qa }) => {
  // The seed's design approval has no chat card, so post one the way request_approval does, then bury it.
  const { approval } = (await qa.fire('approval', { text: 'Design: Export orders to CSV' })) as any
  for (let i = 0; i < 25; i++) await qa.fire('message', { conversation: 'feature-1', role: 'lead', text: `filler ${i}` })
  await withDesign(page, { state: 'pending', approval_id: approval, gated: true })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${approval}"]`)
  await expect(page.locator('[data-design-chip]')).toHaveText('design pending')
  await expect(card).toHaveCount(1)
  await expect(card).not.toBeInViewport()
  await page.locator('[data-design-link]').click()
  await expect(card).toBeInViewport()
})

for (const c of [
  { state: 'pending', text: 'design pending', cls: /amber/ },
  { state: 'changes', text: 'design: changes', cls: /red/ },
  { state: 'none', text: 'no design', cls: /gray/, approval_id: null },
]) {
  test(`a ${c.state} design reads "${c.text}"`, async ({ page, qa }) => {
    await withDesign(page, { state: c.state, approval_id: c.approval_id === undefined ? 7 : c.approval_id, gated: true })
    await qa.open('/p/demo?c=feature-1')
    const chip = page.locator('[data-design-chip]')
    await expect(chip).toHaveText(c.text)
    await expect(chip).toHaveClass(c.cls)
    if (c.state === 'none') await expect(page.locator('[data-design-link]')).toHaveCount(0) // no approval to find
  })
}

test('a feature that predates the gate and never asked for a design shows no chip', async ({ page, qa }) => {
  await withDesign(page, { state: 'none', approval_id: null, gated: false })
  await qa.open('/p/demo?c=feature-1')
  await expect(page.getByRole('heading', { name: 'Export orders to CSV' })).toBeVisible()
  await expect(page.locator('[data-design-chip]')).toHaveCount(0)
})
