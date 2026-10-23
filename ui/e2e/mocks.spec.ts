import type { Page } from '@playwright/test'
import { mockRefs } from '../src/mockRefs'
import { expect, test } from './fixtures'

// F-29: mocks (html pages and images) and Mermaid diagrams in the Docs tab and as chat previews. The sandbox seeds F-1's
// docs folder with mock-export.html (its script probes whether it can reach the page or /api), shot.png, flow.svg and a
// mermaid block in design.md, and two F-1 chat messages: one links mock-export.html and shot.png, one has a mermaid block.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const SANDBOX = 'allow-scripts allow-forms allow-popups allow-modals'
const docsTab = (qa: { open: (r: string) => Promise<void> }) => qa.open('/p/demo?c=feature-1&tab=docs')
const pick = (page: Page, name: string) => page.locator('[data-doc-kind]', { hasText: new RegExp(`^${name.replace('.', '\\.')}$`) }).click()
const naturalWidth = (page: Page, sel: string) => page.locator(sel).evaluate((el: HTMLImageElement) => el.naturalWidth)

test('the Docs tab lists .md files first, then the mocks, each with its kind', async ({ page, qa }) => {
  await docsTab(qa)
  const names = page.locator('[data-doc-kind]')
  await expect(names).toHaveText(['design.md', 'prd.md', 'flow.svg', 'mock-export.html', 'shot.png'])
  expect(await names.evaluateAll((els) => els.map((e) => e.getAttribute('data-doc-kind')))).toEqual(['md', 'md', 'image', 'html', 'image'])
  await expect(page.locator('[data-doc-kind="html"]').locator('xpath=..').locator('svg')).toBeVisible() // a small icon per kind
})

test('an html mock renders live in a sandboxed frame, with Open in new tab', async ({ page, qa }) => {
  await docsTab(qa)
  await pick(page, 'mock-export.html')
  const frame = page.locator('[data-mock-frame="mock-export.html"]')
  await expect(frame).toHaveAttribute('sandbox', SANDBOX) // exactly this: never allow-same-origin
  expect(await frame.getAttribute('sandbox')).not.toContain('allow-same-origin')
  const body = page.frameLocator('[data-mock-frame="mock-export.html"]')
  await expect(body.getByRole('heading', { name: 'Orders' })).toBeVisible()
  await expect(body.getByRole('button', { name: 'Export CSV' })).toBeVisible() // live: its script ran and it is clickable
  await page.screenshot({ path: `${SHOTS}/mocks-docs-tab.png`, animations: 'disabled' })
  const open = page.locator('[data-doc-mock] a', { hasText: 'Open in new tab' })
  await expect(open).toHaveAttribute('href', '/api/p/demo/features/1/files/mock-export.html')
  await expect(open).toHaveAttribute('target', '_blank')
})

test('the mock cannot reach the page that frames it, nor /api with the Master cookie', async ({ page, qa }) => {
  await docsTab(qa)
  await pick(page, 'mock-export.html')
  const body = page.frameLocator('[data-mock-frame="mock-export.html"]')
  await expect(body.locator('#probe-parent')).toHaveText('parent: blocked')
  await expect(body.locator('#probe-fetch')).toHaveText('fetch: blocked')
  // and the page it tried to reach is untouched: nothing of the mock leaked into it
  await expect(page.locator('[data-mock-frame]')).toHaveCount(1)
})

test('images render in the Docs tab, svg included', async ({ page, qa }) => {
  await docsTab(qa)
  await pick(page, 'shot.png')
  await expect(page.locator('[data-mock-image="shot.png"]')).toBeVisible()
  await expect.poll(() => naturalWidth(page, '[data-mock-image="shot.png"]')).toBeGreaterThan(0)
  await pick(page, 'flow.svg')
  await expect(page.locator('[data-mock-image="flow.svg"]')).toBeVisible()
  await expect.poll(() => naturalWidth(page, '[data-mock-image="flow.svg"]')).toBeGreaterThan(0)
  await expect(page.locator('[data-mock-frame]')).toHaveCount(0) // an image is not framed
})

test('the .md docs still render as Markdown, mermaid included', async ({ page, qa }) => {
  await docsTab(qa)
  await pick(page, 'design.md')
  await expect(page.locator('[data-doc-mock]')).toHaveCount(0)
  const diagram = page.locator('[data-mermaid] svg')
  await expect(diagram).toBeVisible({ timeout: 20_000 })
  await expect(diagram).toContainText('GET export.csv')
  await page.screenshot({ path: `${SHOTS}/mocks-mermaid-doc.png`, animations: 'disabled' })
})

test('a chat message that names mocks shows a thumbnail each; clicking one opens the full view', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const html = page.locator('[data-mock-card="mock-export.html"]')
  const png = page.locator('[data-mock-card="shot.png"]')
  await expect(html).toBeVisible()
  await expect(png).toBeVisible()
  await expect(page.locator('[data-mock-card]')).toHaveCount(2)

  // a fixed 240x150 box; the html page inside is live but inert
  const box = (await html.locator('[data-mock-thumb]').boundingBox())!
  expect([Math.round(box.width), Math.round(box.height)]).toEqual([240, 150])
  const thumbFrame = html.locator('iframe')
  await expect(thumbFrame).toHaveAttribute('sandbox', SANDBOX)
  await expect(thumbFrame).toHaveCSS('pointer-events', 'none')
  await expect(page.frameLocator('[data-mock-card="mock-export.html"] iframe').getByRole('heading', { name: 'Orders' })).toBeAttached()
  await expect.poll(() => naturalWidth(page, '[data-mock-card="shot.png"] img')).toBeGreaterThan(0)
  await page.screenshot({ path: `${SHOTS}/mocks-chat-previews.png`, animations: 'disabled' })

  await html.click()
  const dialog = page.locator('[data-mock-dialog="mock-export.html"]')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute('href', '/api/p/demo/features/1/files/mock-export.html')
  await expect(dialog.locator('[data-mock-frame]')).toHaveAttribute('sandbox', SANDBOX)
  await expect(dialog.frameLocator('[data-mock-frame]').getByRole('button', { name: 'Export CSV' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/mocks-chat-dialog.png`, animations: 'disabled' })
  await expect(dialog).toBeFocused() // not the frame: keys typed in a frame never reach the page, so Esc would do nothing
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  await png.click() // an image opens the same dialog
  await expect(page.locator('[data-mock-dialog="shot.png"] [data-mock-image]')).toBeVisible()
})

test('a mermaid block in chat renders as a diagram', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const diagram = page.locator('[id^="m-"] [data-mermaid] svg')
  await expect(diagram).toBeVisible({ timeout: 20_000 })
  await expect(diagram).toContainText('stream rows')
  await expect(page.locator('[id^="m-"] [data-mermaid-error]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/mocks-mermaid-chat.png`, animations: 'disabled' })
})

test('a diagram with a syntax error shows the code and a red line instead', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  await qa.fire('message', { conversation: 'feature-1', role: 'frontend', text: 'Broken one:\n\n```mermaid\nthis is not a diagram\n```' })
  const bad = page.locator('[data-mermaid-error]')
  await expect(bad).toBeVisible({ timeout: 20_000 })
  await expect(bad).toContainText('this is not a diagram')
  await expect(bad).toContainText('This diagram has an error')
  await expect(bad.locator('.text-red-600')).toBeVisible()
  await expect(page.locator('body > [id^="dmermaid"]')).toHaveCount(0) // the failed render left nothing in <body>
})

test('a path to a missing file shows "mock not found", not a broken frame', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  await qa.fire('message', { conversation: 'feature-1', role: 'frontend', text: 'See `.aiteams/features/F-1/nope.png` and .aiteams/features/F-1/gone.html.' })
  await expect(page.locator('[data-mock-missing="nope.png"]')).toHaveText('mock not found: nope.png')
  await expect(page.locator('[data-mock-missing="gone.html"]')).toHaveText('mock not found: gone.html')
  await expect(page.locator('[data-mock-card="nope.png"]')).toHaveCount(0)
})

test('at most 4 distinct mocks per message, a repeated path counts once', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const p = (n: string) => `.aiteams/features/F-1/${n}`
  await qa.fire('message', { conversation: 'feature-1', role: 'frontend', text: `All: ${p('shot.png')} ${p('shot.png')} ${p('flow.svg')} ${p('mock-export.html')} ${p('m1.png')} ${p('m2.png')}` })
  const msg = page.locator('[id^="m-"]', { hasText: 'All:' })
  await expect(msg.locator('[data-mock-previews] > *')).toHaveCount(4) // shot, flow, mock-export, m1; m2 is the fifth
  await expect(msg.locator('[data-mock-card]')).toHaveCount(3)
  await expect(msg.locator('[data-mock-missing]')).toHaveCount(1)
})

test('the mermaid library stays out of the page until a diagram needs it', async ({ page, qa }) => {
  const scripts: string[] = []
  page.on('request', (r) => /\/assets\/[^?]+\.js$/.test(r.url()) && scripts.push(r.url()))
  await qa.open('/p/demo?c=general')
  await expect(page.getByPlaceholder('Message # general')).toBeVisible()
  expect(scripts).toHaveLength(1) // the main bundle alone
  await page.locator('aside').getByRole('button', { name: /^F-1\b/ }).click()
  await expect(page.locator('[data-mermaid] svg')).toBeVisible({ timeout: 20_000 })
  expect(scripts.length).toBeGreaterThan(1) // the diagram libraries came on demand
})

test('mockRefs reads paths as plain text, in code spans and as link targets', () => {
  const at = (n: string) => `.aiteams/features/F-7/${n}`
  const names = (t: string) => mockRefs(t).map((r) => `${r.fid}/${r.name}`)
  expect(names(`see ${at('a.html')} now`)).toEqual(['7/a.html'])
  expect(names(`see \`${at('b.png')}\``)).toEqual(['7/b.png'])
  expect(names(`[mock](${at('c.svg')})`)).toEqual(['7/c.svg'])
  expect(names(`it is ${at('d.JPG')}.`)).toEqual(['7/d.JPG']) // the end of a sentence, any case
  expect(names(`and ${at('e.jpeg')}, then ${at('f.png')}`)).toEqual(['7/e.jpeg', '7/f.png'])
  expect(names(`not ${at('g.html.bak')} or ${at('h.md')} or ${at('i.py')}`)).toEqual([])
  expect(names('a/b.png and F-7/b.png and .aiteams/features/F-x/b.png')).toEqual([])
  expect(names(`${at('a.png')} ${at('a.png')}`)).toEqual(['7/a.png'])
  expect(names('.aiteams/features/F-2/x.png and .aiteams/features/F-3/x.png')).toEqual(['2/x.png', '3/x.png']) // another feature's mock
  expect(names(Array.from({ length: 6 }, (_, i) => at(`m${i}.png`)).join(' '))).toHaveLength(4)
})
