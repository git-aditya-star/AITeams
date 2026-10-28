import { expect, test } from './fixtures'

const ROLES = ['Manager', 'Lead 1', 'Lead 2', 'Product', 'Frontend', 'Backend', 'Qa']

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

// F-42: the meter reads against the rotation limit (150k tokens), so a session started before the upgrade can read 395%. The top bar,
// the sidebar meter and the dashboard ring all show at most 100%, and at 100% the tooltip says when the role rotates.
test('a role over its context limit reads 100% everywhere, and the tooltip says it rotates', async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/team$/, async (route) => {
    const r = await route.fetch()
    const d = await r.json()
    const by = (role: string) => d.roles.find((t: any) => t.role === role)
    Object.assign(by('backend'), { state: 'idle', kind: null, context_pct: 3.95, current: null }) // idle, 593k of 150k
    Object.assign(by('lead'), { state: 'running', kind: 'turn', context_pct: 1 }) // exactly at the limit, mid turn
    Object.assign(by('manager'), { state: 'idle', kind: null, context_pct: 0.34, current: null }) // under it: nothing changes
    await route.fulfill({ response: r, json: d })
  })
  await qa.open('/p/demo')
  const strip = page.locator('main > div').filter({ has: page.getByRole('button', { name: /^Manager\b/ }) }).first()
  const pill = (name: string) => strip.getByRole('button', { name: new RegExp(`^${name}\\b`) })
  const meter = (role: string) => page.locator(`aside [data-ctx-meter="${role}"]`)
  const tip = page.getByRole('tooltip')
  const away = async () => {
    await page.mouse.move(700, 5)
    await page.mouse.move(1390, 890)
    await expect(tip).toHaveCount(0)
  }

  // Top bar: capped, never 395% or 100.0%.
  await expect(pill('Backend')).toHaveText(/\D100%$/)
  await expect(pill('Backend')).not.toContainText('395')
  await expect(pill('Lead 1')).toHaveText(/\D100%$/)
  await expect(pill('Manager')).toHaveText(/\D34%$/)
  await pill('Backend').hover()
  await expect(tip).toContainText('context full, rotates after its next turn') // idle: its next turn ends in a rotation
  await away()
  await pill('Lead 1').hover()
  await expect(tip).toContainText('context full, rotates after this turn') // running: this turn
  await away()
  await pill('Manager').hover()
  await expect(tip).not.toContainText('rotates')
  await away()

  // Sidebar meter: full bar, same note.
  await expect(meter('backend').locator('> span')).toHaveAttribute('style', /width: 100%/)
  await expect(meter('manager').locator('> span')).toHaveAttribute('style', /width: 34%/)
  await meter('backend').hover()
  await expect(tip).toContainText('context 100% · rotates after its next turn')
  await away()
  await meter('manager').hover()
  await expect(tip).toHaveText('context 34%')
  await away()

  // Dashboard ring: capped too.
  const ring = page.locator('[data-card="team"] [data-ring="backend"]')
  await expect(ring.locator('[data-ring-detail]')).toHaveText(/· 100%$/)
  await page.screenshot({ path: `${process.env.SHOT_DIR || 'test-results/shots'}/context-over-limit.png`, animations: 'disabled' })
})

// F-42: the sidebar meter's colours are relative to the rotation limit: green below 70% (105k tokens), amber from 70%, red from 90% (135k).
// The old cut-offs were 40% and 60%, so 40% and 60% are in here too: both are green now.
test('the sidebar meter turns amber at 70% of the limit and red at 90%', async ({ page, qa }) => {
  const pcts: Record<string, number> = { manager: 0.69, lead: 0.7, 'lead-2': 0.89, product: 0.9, frontend: 0.6, backend: 0.4, qa: 2.5 }
  await page.route(/\/api\/p\/demo\/team$/, async (route) => {
    const r = await route.fetch()
    const d = await r.json()
    for (const t of d.roles) t.context_pct = pcts[t.role]
    await route.fulfill({ response: r, json: d })
  })
  await qa.open('/p/demo')
  const fill = (role: string) => page.locator(`aside [data-ctx-meter="${role}"] > span`)
  const want: Record<string, RegExp> = { manager: /bg-green-500/, lead: /bg-amber-400/, 'lead-2': /bg-amber-400/, product: /bg-red-500/, frontend: /bg-green-500/, backend: /bg-green-500/, qa: /bg-red-500/ }
  for (const [role, cls] of Object.entries(want)) {
    await expect(fill(role), role).toHaveClass(cls)
    await expect(fill(role), role).toHaveAttribute('style', new RegExp(`width: ${Math.min(100, Math.round(pcts[role] * 100))}%`))
  }
  await page.screenshot({ path: `${process.env.SHOT_DIR || 'test-results/shots'}/context-meter-colours.png`, animations: 'disabled' })
})
