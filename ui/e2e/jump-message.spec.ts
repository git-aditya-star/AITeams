import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-47: a jump (`?m=<id>`) scrolls to the message once, tints it amber for 4s, and drops `m` from the URL (a history replace, the other
// params stay). A message that arrives later does not pull the view back, and the same link again scrolls again.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'

type Fire = { fire: (k: string, b?: Record<string, unknown>) => Promise<Record<string, unknown>> }

// Enough messages in F-1's chat to scroll (master and backend alternate, so no run of 3 folds). Returns their ids, oldest first.
async function fill(qa: Fire, n = 30) {
  const ids: number[] = []
  for (let i = 0; i < n; i++) ids.push((await qa.fire('message', { conversation: 'feature-1', role: i % 2 ? 'backend' : 'master', text: `Filler ${i}` })).message as number)
  return ids
}

// What `bg-amber-50` computes to in this build, measured on a probe element.
const amber = (page: Page) =>
  page.evaluate(() => {
    const e = document.createElement('div')
    e.className = 'bg-amber-50'
    document.body.appendChild(e)
    const v = getComputedStyle(e).backgroundColor
    e.remove()
    return v
  })

const list = (page: Page) => page.locator('div.flex-1.overflow-y-auto.py-2').filter({ has: page.locator('[id^="m-"]') })
const scrollTop = (page: Page) => list(page).evaluate((el) => el.scrollTop)
const frames = (page: Page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))) // let React's effects run

test('a jump scrolls to the message, tints it amber and drops m from the URL; the other params stay', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  const target = ids[10]
  const tint = await amber(page)

  await page.goto(`/p/demo?c=feature-1&lane=f1&m=${target}`)
  const row = page.locator(`#m-${target}`)
  await expect(row).toBeInViewport()
  await expect(row).toHaveCSS('background-color', tint)
  await expect(page).not.toHaveURL(/[?&]m=/)
  await expect(page).toHaveURL(/[?&]c=feature-1(&|$)/)
  await expect(page).toHaveURL(/[?&]lane=f1(&|$)/)
  await page.mouse.move(0, 0)
  await page.screenshot({ path: `${SHOTS}/jump-message.png`, animations: 'disabled' })
})

test('a jump is a history replace: Back leaves the page the jump started from', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  const toasted = (await qa.fire('blocker', { text: '@master look at this' })).message as number // lands in F-1's chat, with a toast that jumps there
  await page.locator('[data-toast="blocker"] button[title]').click()
  await expect(page).toHaveURL(/[?&]c=feature-1(&|$)/)
  await expect(page.locator(`#m-${toasted}`)).toBeInViewport()
  await expect(page).not.toHaveURL(/[?&]m=/)
  await page.goBack()
  await expect(page).toHaveURL(/[?&]c=general(&|$)/) // one entry for the jump, not two
})

// Scrolls the list to the top the way a user does (a real wheel, which also ends the settle window), then waits for it to land.
async function wheelToTop(page: Page) {
  await list(page).hover()
  await page.mouse.wheel(0, -100000)
  await expect.poll(() => scrollTop(page)).toBe(0)
}

// A message arrives while the user is looking at the top of a long chat, far from the jumped-to row: the view stays where it is.
async function noPullBack(page: Page, qa: Fire, row: Locator) {
  await expect(row).not.toBeInViewport()
  const fresh = (await qa.fire('message', { conversation: 'feature-1', role: 'backend', text: 'Arrived after the jump' })).message as number
  await expect(page.locator(`#m-${fresh}`)).toHaveCount(1)
  await frames(page)
  expect(await scrollTop(page)).toBe(0)
  await expect(row).not.toBeInViewport()
}

test('a message that arrives after the user scrolled away does not pull the view back', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  await page.goto(`/p/demo?c=feature-1&m=${ids[10]}`)
  const row = page.locator(`#m-${ids[10]}`)
  await expect(row).toBeInViewport()
  await expect(page).not.toHaveURL(/[?&]m=/)
  await wheelToTop(page)
  await noPullBack(page, qa, row)
})

test('a message that arrives after the settle window has run out does not pull the view back', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  await page.goto(`/p/demo?c=feature-1&m=${ids[10]}`)
  const row = page.locator(`#m-${ids[10]}`)
  await expect(row).toBeInViewport()
  await page.waitForTimeout(2300) // no input at all: the window ends by itself after 2s
  await list(page).evaluate((el) => { el.scrollTop = 0 })
  await noPullBack(page, qa, row)
})

test('the amber tint is gone after about 4 seconds', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  const tint = await amber(page)

  await page.goto(`/p/demo?c=feature-1&m=${ids[10]}`)
  const row = page.locator(`#m-${ids[10]}`)
  await expect(row).toHaveCSS('background-color', tint)
  const t0 = Date.now()
  await expect(row).not.toHaveCSS('background-color', tint, { timeout: 8000 })
  expect(Date.now() - t0).toBeGreaterThan(2000) // it stayed for a while, it did not flicker away
  await expect(row).toBeInViewport() // the tint going does not move the view
})

test('the same message again scrolls to it again, with the tint', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  const target = ids[10]
  const tint = await amber(page)
  const url = `/p/demo?c=feature-1&m=${target}`

  await page.goto(url)
  const row = page.locator(`#m-${target}`)
  await expect(row).toBeInViewport()
  await expect(page).not.toHaveURL(/[?&]m=/)
  await list(page).evaluate((el) => { el.scrollTop = 0 })
  await expect(row).not.toBeInViewport()

  // What a second click on the same link does: an in-app navigation (react-router follows popstate) to the same `?m=`, no reload.
  await page.evaluate((u) => {
    history.pushState(null, '', u)
    dispatchEvent(new PopStateEvent('popstate'))
  }, url)
  await expect(row).toBeInViewport()
  await expect(row).toHaveCSS('background-color', tint)
  await expect(page).not.toHaveURL(/[?&]m=/)
})

// F-47 settle: content that renders after the jump's one scroll (a diagram, an image preview) grows the list above the row and would
// push it out of view. For 2s after a jump, the row is re-centred on each resize; the first user input on the list ends that window.

// A block above the row, as late-rendering content would be. Chrome's own scroll anchoring is off, so the measurement is only ours.
const growAbove = (page: Page, px: number) =>
  list(page).evaluate((el, h) => {
    el.style.overflowAnchor = 'none'
    const block = document.createElement('div')
    block.style.height = `${h}px`
    el.firstElementChild!.prepend(block) // the wrapper around the rows
  }, px)

test("the seed's late-rendering diagram and image above a toast jump do not push the row out of view", async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  const target = (await qa.fire('blocker', { text: '@master look at this' })).message as number // lands in F-1's chat, below the seeded diagram (#14) and image preview
  await fill(qa)
  await page.locator('[data-toast="blocker"] button[title]').click()
  const row = page.locator(`#m-${target}`)
  await expect(row).toBeInViewport({ ratio: 1 })
  await page.waitForTimeout(1500) // the diagram and the preview render after the scroll
  await expect(row).toBeInViewport({ ratio: 1 })
  await page.mouse.move(0, 0)
  await page.screenshot({ path: `${SHOTS}/jump-message-settled.png`, animations: 'disabled' })
})

test('content that grows above the row after the jump is followed: the row stays in view', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  await page.goto(`/p/demo?c=feature-1&m=${ids[10]}`)
  const row = page.locator(`#m-${ids[10]}`)
  await expect(row).toBeInViewport({ ratio: 1 })
  await growAbove(page, 700) // more than half the list's height: without the follow-up the row would be below the fold
  await expect(row).toBeInViewport({ ratio: 1 })
})

test('a wheel right after the jump ends the follow-up: the view is not pulled back', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const ids = await fill(qa)
  await page.goto(`/p/demo?c=feature-1&m=${ids[10]}`)
  const row = page.locator(`#m-${ids[10]}`)
  await expect(row).toBeInViewport({ ratio: 1 })

  const before = await scrollTop(page)
  await list(page).hover()
  await page.mouse.wheel(0, 40)
  await expect.poll(() => scrollTop(page)).not.toBe(before)
  const after = await scrollTop(page)

  await growAbove(page, 700)
  await frames(page)
  await page.waitForTimeout(300)
  expect(await scrollTop(page)).toBe(after)
  await expect(row).not.toBeInViewport()
})
