import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-29 Part B: the link card for a page a role published to claude.ai, and the artifact entry in the Docs tab. Nothing here
// publishes anything: the daemon's answers are faked with page.route (a real publish is outward-facing, so tests never do one).

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const SANDBOX = 'allow-scripts allow-forms allow-popups allow-modals'
const ART_URL = 'https://claude.ai/artifact/AbCdEf123'

type Art = { id: number; path: string; url: string | null; title: string; version: number }
const now = () => Math.floor(Date.now() / 1000)
const artifact = (a: Art) => ({ feature_id: 1, role: 'frontend', created_at: now() - 300, updated_at: now() - 120, ...a })

// Append link-card messages to #feature-1 (ids above every real one; the store sorts by id).
async function withCards(page: Page, cards: { a: Art; text: string }[]) {
  await page.route(/\/api\/p\/demo\/messages\?.*conversation=feature-1/, async (route) => {
    const resp = await route.fetch()
    const list = await resp.json()
    const top = Math.max(...list.map((m: { id: number }) => m.id))
    const extra = cards.map(({ a, text }, i) => ({
      id: top + 1 + i,
      conversation_id: 'feature-1',
      author: 'frontend',
      type: 'Message',
      text,
      mentions: [],
      turn_id: null,
      created_at: now() - 120 + i,
      receipts: [],
      ref: `artifact:${a.id}`,
      artifact: artifact(a),
    }))
    await route.fulfill({ response: resp, json: [...list, ...extra] })
  })
}

// Add artifact entries to F-1's docs list, after the real docs and mocks (as the daemon lists them).
async function withDocs(page: Page, arts: Art[]) {
  await page.route(/\/api\/p\/demo\/features\/1\/docs$/, async (route) => {
    const resp = await route.fetch()
    const list = await resp.json()
    const extra = arts.map((a) => ({ name: a.title, kind: 'artifact', size: 0, updated_at: now() - 120, editor: 'frontend', url: a.url, version: a.version, path: a.path }))
    await route.fulfill({ response: resp, json: [...list, ...extra] })
  })
}

const SRC = '.aiteams/features/F-1/mock-export.html' // the seeded F-1 mock stands in for an artifact's local source

test('a published artifact is a link card: title, Open to claude.ai in a new tab, the source path, no embedded page', async ({ page, qa }) => {
  await withCards(page, [{ a: { id: 7, path: SRC, url: ART_URL, title: 'Orders export', version: 1 }, text: `Published Orders export at ${ART_URL}\n\nSource: ${SRC}` }])
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator('[data-artifact-card="7"]')
  await expect(card).toBeVisible()
  await expect(card.locator('[data-artifact-title]')).toHaveText('Orders export')
  await expect(card.locator('[data-artifact-source]')).toHaveText(SRC)
  const open = card.getByRole('link', { name: 'Open' })
  await expect(open).toHaveAttribute('href', ART_URL)
  await expect(open).toHaveAttribute('target', '_blank')
  await expect(open).toHaveAttribute('rel', /noreferrer/)
  await expect(card.locator('[data-artifact-version]')).toHaveCount(0) // version 1 is not "updated"
  await expect(card.locator('iframe, img')).toHaveCount(0) // the page is only linked
  // the message names its source file, but the card replaces the thumbnail that a plain mock link would get
  await expect(page.locator('[id^="m-"]').last().locator('[data-mock-card]')).toHaveCount(0)
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/artifacts-chat-card.png`, animations: 'disabled' })
})

test('an updated artifact shows a "v<n> · updated" chip', async ({ page, qa }) => {
  await withCards(page, [{ a: { id: 8, path: SRC, url: ART_URL, title: 'Orders export', version: 3 }, text: `Updated Orders export (v3)` }])
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator('[data-artifact-card="8"] [data-artifact-version]')).toHaveText('v3 · updated')
  await page.locator('[data-artifact-card="8"]').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/artifacts-chat-card-updated.png`, animations: 'disabled' })
})

test('only an https URL becomes a link', async ({ page, qa }) => {
  await withCards(page, [
    { a: { id: 9, path: SRC, url: 'javascript:alert(1)', title: 'Bad scheme', version: 1 }, text: 'Published Bad scheme' },
    { a: { id: 10, path: SRC, url: null, title: 'No url yet', version: 1 }, text: 'Published No url yet' },
  ])
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator('[data-artifact-card="9"]')).toBeVisible()
  await expect(page.locator('[data-artifact-card="10"]')).toBeVisible()
  await expect(page.locator('[data-artifact-card="9"] a, [data-artifact-card="10"] a')).toHaveCount(0)
})

test('Docs lists an artifact with its own icon; its pane has the title, version, Open and the local source in a sandboxed frame', async ({ page, qa }) => {
  await withDocs(page, [{ id: 7, path: SRC, url: ART_URL, title: 'Orders export', version: 2 }])
  await qa.open('/p/demo?c=feature-1&tab=docs')
  const entry = page.locator('[data-doc-kind="artifact"]', { hasText: /^Orders export$/ })
  await expect(entry).toHaveText('Orders export')
  await expect(entry.locator('xpath=..').locator('svg')).toBeVisible()
  await entry.click()

  const pane = page.locator('[data-doc-artifact="Orders export"]')
  await expect(pane.locator('[data-artifact-title]')).toHaveText('Orders export')
  await expect(pane.locator('[data-artifact-version]')).toHaveText('v2 · updated')
  await expect(pane.getByRole('link', { name: 'Open' })).toHaveAttribute('href', ART_URL)
  await expect(pane.getByRole('link', { name: 'Open' })).toHaveAttribute('target', '_blank')
  await expect(pane.locator('[data-artifact-source]')).toHaveText(SRC)
  // the local source goes through MockView: the same sandbox as any mock, and it is live
  await expect(pane.locator('[data-mock-frame="mock-export.html"]')).toHaveAttribute('sandbox', SANDBOX)
  await expect(pane.frameLocator('[data-mock-frame]').getByRole('heading', { name: 'Orders' })).toBeVisible()
  await expect(page.locator('[data-doc-artifact] iframe')).toHaveCount(1) // claude.ai's page is not embedded
  await page.screenshot({ path: `${SHOTS}/artifacts-docs-pane.png`, animations: 'disabled' })

  // the real mock keeps its own pane (no clash with an artifact whose source it is)
  await page.locator('[data-doc-kind="html"]', { hasText: /^mock-export\.html$/ }).click()
  await expect(page.locator('[data-doc-mock="mock-export.html"]')).toBeVisible()
  await expect(page.locator('[data-doc-artifact]')).toHaveCount(0)
})

test('an artifact whose source file is gone still shows its title and Open, and says the file is missing', async ({ page, qa }) => {
  await withDocs(page, [{ id: 11, path: '.aiteams/features/F-1/artifact-gone.html', url: ART_URL, title: 'Gone page', version: 1 }])
  await qa.open('/p/demo?c=feature-1&tab=docs')
  await page.locator('[data-doc-kind="artifact"]', { hasText: /^Gone page$/ }).click()
  const pane = page.locator('[data-doc-artifact="Gone page"]')
  await expect(pane.getByRole('link', { name: 'Open' })).toHaveAttribute('href', ART_URL)
  await expect(pane.locator('[data-artifact-no-source]')).toHaveText('source file not found: artifact-gone.html')
  await expect(pane.locator('iframe')).toHaveCount(0)
  await expect(pane.locator('[data-artifact-version]')).toHaveCount(0)
})

test('an artifact titled like a doc does not steal its selection', async ({ page, qa }) => {
  await withDocs(page, [{ id: 12, path: SRC, url: ART_URL, title: 'design.md', version: 1 }])
  await qa.open('/p/demo?c=feature-1&tab=docs')
  const names = page.locator('[data-doc-kind]')
  await expect(names).toContainText(['design.md', 'prd.md', 'flow.svg', 'mock-export.html', 'shot.png', 'Export orders mock', 'design.md'])
  await names.last().click()
  await expect(page.locator('[data-doc-artifact="design.md"]')).toBeVisible()
  await names.first().click() // the real design.md is still a Markdown doc
  await expect(page.locator('[data-doc-artifact]')).toHaveCount(0)
  await expect(page.locator('[data-doc-mock]')).toHaveCount(0)
})

// The sandbox seeds F-1 with one artifact (backend's B2): "Export orders mock", v2, whose source is the seeded mock-export.html.
test('the seeded artifact shows as a link card in chat and as an entry in Docs', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator('[data-artifact-card]')
  await expect(card).toHaveCount(1)
  await expect(card.locator('[data-artifact-title]')).toHaveText('Export orders mock')
  await expect(card.locator('[data-artifact-version]')).toHaveText('v2 · updated')
  await expect(card.locator('[data-artifact-source]')).toHaveText(SRC)
  await expect(card.getByRole('link', { name: 'Open' })).toHaveAttribute('href', 'https://claude.ai/artifact/demo')
  await expect(page.locator('[id^="m-"]').last()).toContainText('Updated Export orders mock (v2)')
  await expect(page.locator('[id^="m-"]').last().locator('[data-mock-card]')).toHaveCount(0)
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/artifacts-seeded-chat.png`, animations: 'disabled' })

  await qa.open('/p/demo?c=feature-1&tab=docs')
  await page.locator('[data-doc-kind="artifact"]').click()
  const pane = page.locator('[data-doc-artifact="Export orders mock"]')
  await expect(pane.getByRole('link', { name: 'Open' })).toHaveAttribute('href', 'https://claude.ai/artifact/demo')
  await expect(pane.locator('[data-artifact-source]')).toHaveText(SRC)
  await expect(pane.frameLocator('[data-mock-frame]').getByRole('button', { name: 'Export CSV' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/artifacts-seeded-docs.png`, animations: 'disabled' })
})
