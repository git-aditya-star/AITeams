import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-9: the gear in the top bar opens the Role settings dialog (global model + effort per role).

const pick = async (page: Page, label: string, option: string | RegExp) => {
  await page.getByRole('combobox', { name: label }).click()
  // exact for strings: 'high' would also match 'xhigh', 'Sonnet 5' would match 'Sonnet 5.5'
  await page.getByRole('option', { name: option, exact: typeof option === 'string' }).click()
}

const qaTooltip = async (page: Page) => {
  await page.locator('main').getByRole('button', { name: /^Qa/ }).hover()
  return page.getByRole('tooltip')
}

test('set QA to Sonnet 5.5 + low, it persists, and back to Default', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  await page.getByRole('button', { name: 'Role settings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Role settings' })
  await expect(dialog).toContainText("Global for all projects. Applies from each role's next turn.")

  // Labels in the list, the pinned ID in the PUT (it goes to --model unchanged).
  await pick(page, 'Qa model', 'Sonnet 5.5')
  await pick(page, 'Qa effort', 'low')
  const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/api/settings'))
  await dialog.getByRole('button', { name: 'Save' }).click()
  expect((await put).postDataJSON()).toMatchObject({ models: { qa: 'claude-sonnet-5-5' }, efforts: { qa: 'low' } })
  await expect(dialog).toHaveCount(0)

  // The team refetch after Save updates the pill tooltip without waiting for the 30 s poll.
  await expect(await qaTooltip(page)).toContainText('Sonnet 5.5 · low effort')

  await page.reload()
  await page.getByRole('button', { name: 'Role settings' }).click()
  await expect(page.getByRole('combobox', { name: 'Qa model' })).toHaveText('Sonnet 5.5')
  await expect(page.getByRole('combobox', { name: 'Qa effort' })).toHaveText('low')

  // Back to inherit: the PUT drops the role keys, and the tooltip falls back to the defaults.
  await pick(page, 'Qa model', /^Default/)
  await pick(page, 'Qa effort', /^Default/)
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.mouse.move(5, 300)
  await expect(await qaTooltip(page)).toContainText('Opus (latest) · default effort')
})

test('Cancel discards the draft', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  await page.getByRole('button', { name: 'Role settings' }).click()
  await pick(page, 'Lead model', 'Haiku (latest)')
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('button', { name: 'Role settings' }).click()
  await expect(page.getByRole('combobox', { name: 'Lead model' })).toHaveText(/^Default/)
})

test('a level the row model does not support is disabled', async ({ page, qa }) => {
  // The probe decides the real table; force one gap so the disabled state is always exercised.
  await page.route('**/api/settings/options', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.effort_support = { ...body.effort_support, haiku: ['low', 'medium', 'high'] }
    await route.fulfill({ response: res, json: body })
  })
  await qa.open('/p/demo?c=general')
  await page.getByRole('button', { name: 'Role settings' }).click()
  await pick(page, 'Backend model', 'Haiku (latest)')
  await page.getByRole('combobox', { name: 'Backend effort' }).click()
  await expect(page.getByRole('option', { name: /^max/ })).toHaveAttribute('data-disabled', '')
  await expect(page.getByRole('option', { name: /^max/ })).toContainText('not supported by Haiku (latest)')
  await expect(page.getByRole('option', { name: 'low' })).not.toHaveAttribute('data-disabled')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('listbox')).toHaveCount(0)
  await page.screenshot({ path: process.env.SHOT_OUT || 'test-results/shots/role-settings.png' })
})

test('an alias shows as latest, and a saved ID that is no longer offered stays as it is', async ({ page, qa }) => {
  // The PUT only accepts offered IDs (or the stored value), and the sandbox can't seed the file, so a retired ID
  // saved earlier is injected into the GET and the PUT is answered here; the file side is backend's F6 pytest.
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() === 'PUT') return route.fulfill({ json: route.request().postDataJSON() })
    if (route.request().method() !== 'GET') return route.fallback()
    const res = await route.fetch()
    const body = await res.json()
    body.models = { ...body.models, qa: 'sonnet', backend: 'claude-opus-4' }
    await route.fulfill({ response: res, json: body })
  })
  await qa.open('/p/demo?c=general')
  await page.getByRole('button', { name: 'Role settings' }).click()
  await expect(page.getByRole('combobox', { name: 'Qa model' })).toHaveText('Sonnet (latest)')
  await expect(page.getByRole('combobox', { name: 'Backend model' })).toHaveText('claude-opus-4')

  // Saving after an unrelated change sends the retired ID back as it was.
  await pick(page, 'Lead effort', 'high')
  const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/api/settings'))
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  expect((await put).postDataJSON()).toMatchObject({ models: { qa: 'sonnet', backend: 'claude-opus-4' }, efforts: { lead: 'high' } })
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('the Live session shows the command the daemon ran', async ({ page, qa }) => {
  // Seed turns predate F-9, so append one aiteams_cmd event to the events the view fetches.
  await page.route(/\/api\/p\/demo\/turns\/\d+\/events/, async (route) => {
    const res = await route.fetch()
    const events = await res.json()
    if (Array.isArray(events) && events.length && route.request().url().includes('after_seq=0')) {
      // The daemon records it first, so it gets the first seq; the view skips seq <= 0.
      for (const ev of events) ev.seq += 1
      events.unshift({ turn_id: events[0].turn_id, seq: 1, ts: events[0].ts, type: 'aiteams_cmd',
        data: { type: 'aiteams_cmd', model: 'sonnet', effort: 'low', argv: ['claude', '-p', '--model', 'sonnet', '--effort', 'low', '--mcp-config', '<json>'] } })
    }
    await route.fulfill({ response: res, json: events })
  })
  await qa.open('/p/demo?c=dm-frontend&tab=live')
  await expect(page.locator('[data-aiteams-cmd]').first()).toHaveText('claude -p --model sonnet --effort low --mcp-config <json>')
})
