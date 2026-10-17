import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-15: the prominent "New task" button (sidebar + Dashboard) opens a modal for the brief and the starter.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const featureCount = async (qa: { api: (p: string) => Promise<any> }) => ((await qa.api('/features?history=1')) as any[]).length
const modal = (page: Page) => page.getByRole('dialog', { name: 'What should the team build?' })

test('from the sidebar: focus, empty guard, pick Lead, Start opens the new conversation', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  const button = page.locator('aside').getByRole('button', { name: 'New task' })
  await expect(button).toHaveCSS('height', '40px')
  await page.screenshot({ path: `${SHOTS}/new-task-sidebar.png`, animations: 'disabled' })
  await button.click()

  const dialog = modal(page)
  const brief = dialog.getByLabel('Brief')
  const start = dialog.getByRole('button', { name: 'Start' })
  await expect(brief).toBeFocused()
  await expect(start).toBeDisabled()
  await brief.fill('   \n ')
  await expect(start).toBeDisabled()

  // The starter is a toggle group: Product by default, and clicking the active one doesn't empty it.
  const starter = dialog.getByRole('radiogroup', { name: 'Starts with' })
  await expect(starter.getByRole('radio', { name: 'Product' })).toBeChecked()
  await starter.getByRole('radio', { name: 'Product' }).click()
  await expect(starter.getByRole('radio', { name: 'Product' })).toBeChecked()
  await starter.getByRole('radio', { name: 'Lead' }).click()
  await expect(starter.getByRole('radio', { name: 'Lead' })).toBeChecked()

  await brief.fill('Add a CSV import\nso ops can bulk-load customers.')
  await page.screenshot({ path: `${SHOTS}/new-task-modal.png`, animations: 'disabled' })
  await start.click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Add a CSV import' })).toBeVisible()
  await expect(page.locator('main .mention', { hasText: '@lead' })).toBeVisible()
})

test('from the Dashboard: Cmd/Ctrl+Enter starts with Product', async ({ page, qa }) => {
  await qa.open('/p/demo')
  await page.screenshot({ path: `${SHOTS}/new-task-dashboard.png`, animations: 'disabled' })
  await page.locator('main').getByRole('button', { name: 'New task' }).click()
  const brief = modal(page).getByLabel('Brief')
  await brief.fill('Show a weekly digest on the Dashboard')
  await brief.press('ControlOrMeta+Enter')
  await expect(modal(page)).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Show a weekly digest on the Dashboard' })).toBeVisible()
  await expect(page.locator('main .mention', { hasText: '@product' })).toBeVisible()
})

test('Esc creates nothing and keeps the draft for the next open', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  const before = await featureCount(qa)
  const button = page.locator('aside').getByRole('button', { name: 'New task' })
  await button.click()
  await modal(page).getByLabel('Brief').fill('Half a thought')
  await page.keyboard.press('Escape')
  await expect(modal(page)).toHaveCount(0)
  expect(await featureCount(qa)).toBe(before)

  await button.click()
  await expect(modal(page).getByLabel('Brief')).toHaveValue('Half a thought')
})

test('a failed start shows the error inline and keeps the text', async ({ page, qa }) => {
  await page.route(/\/api\/p\/demo\/features$/, (route) =>
    route.request().method() === 'POST' ? route.fulfill({ status: 500, json: { detail: 'could not create the feature' } }) : route.fallback(),
  )
  await qa.open('/p/demo?c=general')
  await page.locator('aside').getByRole('button', { name: 'New task' }).click()
  const dialog = modal(page)
  await dialog.getByLabel('Brief').fill('Something that fails')
  await dialog.getByRole('button', { name: 'Start' }).click()
  await expect(dialog.locator('.text-red-600')).toBeVisible()
  await expect(dialog).toHaveCount(1)
  await expect(dialog.getByLabel('Brief')).toHaveValue('Something that fails')
})
