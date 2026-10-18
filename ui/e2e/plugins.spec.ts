import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-23: the Plugins tab in Role settings. The list tests mock GET /api/settings/plugins -> {rows, status} and answer the
// PUT /api/settings the way the daemon validates it; the missing-badge and toast tests use the sandbox's real
// `plugin_missing` fire kind, which runs the daemon's own check.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const NOTES = 'project-notes@project-notes'
const SKILLS = 'team-skills@team-skills'
const SKILLS_LOADED = 'team-skills@example' // the sandbox's fake init lists this source, so a row with it is loaded

type Plugin = { id: string; on: boolean; ask: boolean }

// Same rule as the daemon's validate_plugins (name@marketplace); a bad id answers 400 with a `detail` sentence.
const ID_RE = /^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$/

async function mockPlugins(page: Page, init: { rows?: Plugin[]; older?: boolean } = {}) {
  const state = { rows: init.rows ?? [], status: {}, puts: [] as any[] }
  await page.route(/\/api\/settings\/plugins$/, (route) =>
    init.older ? route.fulfill({ status: 404, json: { detail: 'Not Found' } }) : route.fulfill({ json: { rows: state.rows, status: state.status } }),
  )
  await page.route(/\/api\/settings$/, async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback()
    const body = route.request().postDataJSON()
    state.puts.push(body)
    const bad = (body.plugins ?? []).find((r: Plugin) => !ID_RE.test(r.id))
    if (bad) return route.fulfill({ status: 400, json: { detail: `plugins: "${bad.id}" is not a plugin id like name@marketplace` } })
    if (body.plugins) state.rows = body.plugins
    await route.fulfill({ json: body })
  })
  return state
}

const openPlugins = async (page: Page) => {
  await page.getByRole('button', { name: 'Role settings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Role settings' })
  await dialog.getByRole('tab', { name: 'Plugins' }).click()
  return dialog
}
const save = async (page: Page) => {
  const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/api/settings'))
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  return (await put).postDataJSON()
}
const hhmm = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

test('an empty list says Claude Code defaults apply; add, edit, remove and save survive a reload', async ({ page, qa }) => {
  const state = await mockPlugins(page)
  await qa.open('/p/demo?c=general')
  const dialog = await openPlugins(page)
  await expect(dialog.locator('[data-plugins-empty]')).toContainText("No plugins listed. Claude Code's defaults apply.")
  await expect(dialog.locator('[data-plugins-empty]')).toContainText(NOTES)
  await page.screenshot({ path: `${SHOTS}/plugins-empty.png`, animations: 'disabled' })

  // Add two rows; the new row's id box has focus, and a row starts On with Ask me first off.
  await dialog.getByRole('button', { name: 'Add plugin' }).click()
  await expect(dialog.locator('[data-plugins-empty]')).toHaveCount(0)
  const ids = dialog.getByRole('textbox', { name: 'Plugin id' })
  await expect(ids).toBeFocused()
  await expect(ids).toHaveAttribute('placeholder', 'name@marketplace')
  await expect(dialog.getByRole('switch', { name: 'On' })).toBeChecked()
  await expect(dialog.getByRole('switch', { name: 'Ask me first' })).not.toBeChecked()
  await page.keyboard.type(NOTES)
  await dialog.getByRole('button', { name: 'Add plugin' }).click()
  await ids.nth(1).fill(SKILLS)
  await dialog.getByRole('switch', { name: 'Ask me first' }).nth(1).click()
  await dialog.getByRole('button', { name: 'Add plugin' }).click() // a blank row is dropped on save, not sent
  expect(await save(page)).toMatchObject({
    plugins: [
      { id: NOTES, on: true, ask: false },
      { id: SKILLS, on: true, ask: true },
    ],
  })
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Reload: the saved list comes back from the daemon with the same switches.
  await page.reload()
  const again = await openPlugins(page)
  await expect(again.getByRole('textbox', { name: 'Plugin id' })).toHaveCount(2)
  await expect(again.getByRole('textbox', { name: 'Plugin id' }).nth(0)).toHaveValue(NOTES)
  await expect(again.getByRole('textbox', { name: 'Plugin id' }).nth(1)).toHaveValue(SKILLS)
  await expect(again.locator(`[data-plugin-row="${NOTES}"]`).getByRole('switch', { name: 'Ask me first' })).not.toBeChecked()
  await expect(again.locator(`[data-plugin-row="${SKILLS}"]`).getByRole('switch', { name: 'Ask me first' })).toBeChecked()

  // Edit the first id, switch it off, remove the second.
  await again.getByRole('textbox', { name: 'Plugin id' }).first().fill('project-notes@notes2')
  await again.getByRole('switch', { name: 'On' }).first().click()
  await again.locator(`[data-plugin-row="${SKILLS}"]`).getByRole('button', { name: 'Remove plugin' }).click()
  await expect(again.getByRole('textbox', { name: 'Plugin id' })).toHaveCount(1)
  expect(await save(page)).toMatchObject({ plugins: [{ id: 'project-notes@notes2', on: false, ask: false }] })
  expect(state.rows).toEqual([{ id: 'project-notes@notes2', on: false, ask: false }])

  // Removing the last row brings the empty state back, and saving sends an empty list.
  await page.reload()
  const last = await openPlugins(page)
  await last.getByRole('button', { name: 'Remove plugin' }).click()
  await expect(last.locator('[data-plugins-empty]')).toBeVisible()
  expect(await save(page)).toMatchObject({ plugins: [] })
})

test("Cancel drops the plugin edits; a bad id keeps the dialog open with the server's sentence", async ({ page, qa }) => {
  const state = await mockPlugins(page, { rows: [{ id: NOTES, on: true, ask: false }] })
  await qa.open('/p/demo?c=general')
  let dialog = await openPlugins(page)
  await dialog.getByRole('textbox', { name: 'Plugin id' }).fill('changed@x')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  dialog = await openPlugins(page)
  await expect(dialog.getByRole('textbox', { name: 'Plugin id' })).toHaveValue(NOTES)
  expect(state.puts).toHaveLength(0)

  await dialog.getByRole('button', { name: 'Add plugin' }).click()
  await page.keyboard.type('not a plugin id')
  await dialog.getByRole('button', { name: 'Save' }).click()
  // the sentence from `detail`, not the raw "PUT /api/settings -> 400 {...}" line
  await expect(dialog).toContainText('plugins: "not a plugin id" is not a plugin id like name@marketplace')
  await expect(dialog).not.toContainText('-> 400')
  await expect(dialog.getByRole('textbox', { name: 'Plugin id' })).toHaveCount(2) // the draft is kept
  expect(state.rows).toEqual([{ id: NOTES, on: true, ask: false }])

  // Fix it and save again.
  await dialog.getByRole('textbox', { name: 'Plugin id' }).nth(1).fill('fixed@market')
  expect(await save(page)).toMatchObject({ plugins: [{ id: NOTES }, { id: 'fixed@market' }] })
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('a switched-on plugin the last turn did not load shows a missing badge with who and when', async ({ page, qa }) => {
  // The sandbox's own kind: it adds an On row and runs the daemon's real check against a turn that loaded only team-skills.
  await qa.fire('plugin_missing', { role: 'frontend' }) // project-notes@project-notes: not loaded -> missing
  await qa.fire('plugin_missing', { id: SKILLS_LOADED, role: 'frontend' }) // the init lists it -> loaded, no badge
  const { rows, status } = await (await page.request.get('/api/settings/plugins')).json()
  expect(rows.map((r: Plugin) => r.id)).toEqual([NOTES, SKILLS_LOADED])
  expect(status[NOTES]).toMatchObject({ missing: true, role: 'frontend' })
  await qa.open('/p/demo?c=general')
  const dialog = await openPlugins(page)
  const badge = dialog.locator('[data-plugin-missing]')
  await expect(badge).toHaveCount(1)
  await expect(badge).toHaveText('missing')
  await expect(badge).toHaveClass(/red/)
  await expect(dialog.locator(`[data-plugin-row="${NOTES}"] [data-plugin-missing]`)).toBeVisible()
  await expect(dialog.locator(`[data-plugin-row="${SKILLS_LOADED}"] [data-plugin-missing]`)).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/plugins-tab.png`, animations: 'disabled' })
  await badge.hover()
  await expect(page.getByRole('tooltip')).toHaveText(`Not loaded by Frontend's turn at ${hhmm(status[NOTES].checked_at)}`)

  // Switching the row off in the draft takes the badge away: only on rows are checked.
  await page.mouse.move(5, 5)
  await dialog.locator(`[data-plugin-row="${NOTES}"]`).getByRole('switch', { name: 'On' }).click()
  await expect(badge).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  // A later turn that loads project-notes clears its flag. That fake init lists nothing else, so the other row is the one
  // flagged now: the badge follows each row's own status, from the daemon, not a copy kept in the dialog.
  await qa.fire('plugin_missing', { loaded: true })
  const again = await openPlugins(page)
  await expect(again.getByRole('textbox', { name: 'Plugin id' })).toHaveCount(2)
  await expect(again.locator('[data-plugin-missing]')).toHaveCount(1)
  await expect(again.locator(`[data-plugin-row="${NOTES}"] [data-plugin-missing]`)).toHaveCount(0)
  await expect(again.locator(`[data-plugin-row="${SKILLS_LOADED}"] [data-plugin-missing]`)).toBeVisible()
})

test('the plugin_missing event shows one toast per flip, not one per turn', async ({ page, qa }) => {
  await qa.open('/p/demo')
  await expect(page.locator('[data-toast]')).toHaveCount(0)
  await qa.fire('plugin_missing', { role: 'frontend' })
  const toast = page.locator('[data-toast="plugin_missing"]')
  await expect(toast).toHaveCount(1)
  await expect(toast.locator('button[title]')).toHaveAttribute('title', `Plugin ${NOTES} didn't load in Frontend's turn`)
  await expect(toast).toContainText('Plugin missing')
  await page.screenshot({ path: `${SHOTS}/plugins-toast.png`, animations: 'disabled' })

  // Another turn that still lacks it: the daemon stays quiet. Frames arrive in order, so once the standup toast is up
  // any second plugin toast would be too.
  await qa.fire('plugin_missing', { role: 'qa' })
  await qa.fire('standup')
  await expect(page.locator('[data-toast="standup"]')).toHaveCount(1)
  await expect(toast).toHaveCount(1)

  // It loads, then goes missing again: that is a new flip, so a second toast.
  await qa.fire('plugin_missing', { loaded: true })
  await qa.fire('plugin_missing', { role: 'backend' })
  await expect(toast).toHaveCount(2)
  await expect(toast.locator(`button[title="Plugin ${NOTES} didn't load in Backend's turn"]`)).toHaveCount(1) // Sonner's DOM order isn't visual order
})

test('on a daemon without the endpoint the tab says so and Save leaves the list alone', async ({ page, qa }) => {
  const state = await mockPlugins(page, { older: true })
  await qa.open('/p/demo?c=general')
  const dialog = await openPlugins(page)
  await expect(dialog).toContainText('Not available from this daemon; run aiteams upgrade.')
  await expect(dialog.getByRole('button', { name: 'Add plugin' })).toHaveCount(0)
  await dialog.getByRole('tab', { name: 'Roles' }).click() // the rest of the dialog still works
  expect(await save(page)).not.toHaveProperty('plugins')
  expect(state.puts).toHaveLength(1)
})
