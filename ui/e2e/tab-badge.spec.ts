import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-35: the browser tab carries the AITeams icon, and while the Inbox needs the Master it shows "(N) AITeams" and the
// icon with a red dot. Both follow the sidebar's Inbox badge live (no reload) and clear at zero.
// The icon files are served from /assets: the daemon serves nothing else static (see useTabBadge).

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const PLAIN = '/assets/favicon.svg'
const DOT = '/assets/favicon-dot.svg'
const PNG = '/assets/favicon-32.png'

const icon = (page: Page) => page.locator('link[rel="icon"]')
const sidebarBadge = (page: Page) => page.getByRole('button', { name: /^Inbox/ }).locator('[data-slot="badge"]')

// What the tab would show right now: the real page's title and icon, read live.
const tab = (page: Page) => page.evaluate(() => ({ title: document.title, href: document.querySelector('link[rel="icon"]')!.getAttribute('href')! }))

// Playwright cannot capture the browser's own tab strip, so draw one from the real title and icon of each state:
// the icon at its real 16px (rendered at 3x), on a light and a dark strip, plus the 64px render.
async function tabShot(page: Page, states: { title: string; href: string }[], file: string) {
  const origin = new URL(page.url()).origin
  const strip = (s: { title: string; href: string }, dark: boolean) => `
    <div style="display:flex;align-items:center;gap:8px;width:230px;height:34px;padding:0 12px;border-radius:8px 8px 0 0;
      background:${dark ? '#35363a' : '#fff'};color:${dark ? '#e8eaed' : '#202124'};font:12px system-ui">
      <img src="${origin}${s.href}" width="16" height="16"><span>${s.title}</span></div>`
  const shot = await page.context().newPage()
  await shot.setViewportSize({ width: 760, height: 100 + states.length * 60 })
  await shot.setContent(`<body style="margin:0;padding:16px;background:#dee1e6;font:12px system-ui;color:#555">
    ${states.map((s) => `<div style="display:flex;align-items:flex-end;gap:16px;margin-bottom:20px">
      ${strip(s, false)}${strip(s, true)}<img src="${origin}${s.href}" width="64" height="64"></div>`).join('')}</body>`)
  await shot.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))
  await shot.screenshot({ path: `${SHOTS}/${file}`, animations: 'disabled' })
  await shot.close()
}

test('the seeded Inbox puts "(N) AITeams" and the dotted icon on the tab, N being the sidebar badge', async ({ page, qa }) => {
  await qa.open('/p/demo')
  await expect(sidebarBadge(page)).toBeVisible()
  const n = Number(await sidebarBadge(page).textContent())
  expect(n).toBeGreaterThan(0)
  await expect(page).toHaveTitle(`(${n}) AITeams`)
  await expect(icon(page)).toHaveAttribute('href', DOT)
  // The PNG fallback is a different link tag and stays as the page shipped it.
  await expect(page.locator('link[rel="alternate icon"]')).toHaveAttribute('href', PNG)
  await expect(page.locator('link[rel~="icon"]')).toHaveCount(2)
})

test('resolving every Inbox item through the UI clears the title and the dot, with no reload', async ({ page, qa }) => {
  await qa.open('/p/demo?c=inbox')
  await expect(sidebarBadge(page)).toBeVisible()
  const n = Number(await sidebarBadge(page).textContent())
  await expect(page).toHaveTitle(`(${n}) AITeams`)
  await page.evaluate(() => ((window as any).__same = true)) // gone after a reload
  const before = await tab(page)

  const steps = [
    () => page.locator('[data-card^="approval-"]').first().getByRole('button', { name: 'Approve' }).click(),
    () => page.locator('[data-question-card]').first().getByRole('button', { name: /^Orders header/ }).click(),
    // Approving the commit runs a real commit in the sandbox repo, which can outlast the default 5 s.
    () => page.locator('[data-card^="commit-"]').first().getByRole('button', { name: 'Approve' }).click(),
  ]
  expect(steps.length).toBe(n) // the seed: one approval, one question, one commit request
  let left = n
  for (const step of steps) {
    await step()
    left -= 1
    await expect(page).toHaveTitle(left ? `(${left}) AITeams` : 'AITeams', { timeout: 15_000 })
  }
  await expect(page.getByText('Nothing needs you.')).toBeVisible()
  await expect(sidebarBadge(page)).toHaveCount(0)
  await expect(page).toHaveTitle('AITeams')
  await expect(icon(page)).toHaveAttribute('href', PLAIN)
  expect(await page.evaluate(() => (window as any).__same)).toBe(true)

  await tabShot(page, [before, await tab(page)], 'tab-badge.png')
})

test('a new approval brings the count and the dot back, live', async ({ page, qa }) => {
  await qa.open('/p/demo?c=inbox')
  const n = Number(await sidebarBadge(page).textContent())
  await qa.fire('approval', { text: 'Design: tab badge' })
  await expect(page).toHaveTitle(`(${n + 1}) AITeams`)
  await expect(sidebarBadge(page)).toHaveText(String(n + 1))
  await expect(icon(page)).toHaveAttribute('href', DOT)
})

test('the icon files the built page links to, and the dotted one, are served', async ({ page, request }) => {
  // The HTML as shipped (the hook has not run on it): the same links a browser reads before any script.
  const html = await (await page.request.get('/p/demo')).text()
  const linked = [...html.matchAll(/<link rel="(?:alternate )?icon"[^>]*href="([^"]+)"/g)].map((m) => m[1])
  expect(linked).toEqual([PLAIN, PNG])
  const type: Record<string, string> = { [PLAIN]: 'image/svg+xml', [DOT]: 'image/svg+xml', [PNG]: 'image/png' }
  for (const href of [...linked, DOT]) {
    const r = await request.get(href)
    expect(r.status(), href).toBe(200)
    expect(r.headers()['content-type'], href).toContain(type[href])
  }
  // Not the app's index.html: the first bytes are the file's own.
  expect((await (await request.get(PLAIN)).text()).startsWith('<svg')).toBe(true)
  expect((await (await request.get(PNG)).body()).subarray(1, 4).toString()).toBe('PNG')
})
