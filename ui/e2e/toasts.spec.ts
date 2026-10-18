import { expect, test } from './fixtures'

// One case per /qa/fire kind: the toast text (from ui/src/toasts.ts) and the conversation a click opens.
const CASES: { kind: string; body?: Record<string, unknown>; text: string | RegExp; conv: string | null }[] = [
  { kind: 'question', body: { text: 'Which browser first?' }, text: 'Lead 1 asks: Which browser first?', conv: 'dm-lead' },
  { kind: 'question_message', body: { text: '@master Can I drop the old export?' }, text: 'Lead 1 asks: Can I drop the old export?', conv: 'feature-1' },
  { kind: 'blocker', body: { text: '@master CI is red on main' }, text: 'Blocker from Lead 1: CI is red on main', conv: 'feature-1' },
  { kind: 'approval', body: { text: 'Design sign-off' }, text: 'Approval requested by Lead 1: Design sign-off', conv: 'feature-1' },
  { kind: 'commit_request', body: { text: 'add importer' }, text: 'Commit request from Lead 1: F-1: add importer', conv: 'feature-1' },
  { kind: 'committed', body: { text: 'add importer' }, text: /^Committed [0-9a-f]{7} F-1: add importer$/, conv: 'feature-1' },
  { kind: 'commit_failed', body: { text: 'add importer' }, text: 'Commit failed: F-1: add importer', conv: 'feature-1' },
  { kind: 'standup', text: 'Standup report ready', conv: 'standup' },
  { kind: 'stopped', text: "Frontend's turn was stopped by the Master", conv: 'dm-frontend' },
  { kind: 'interrupted', text: 'Manager interrupted Frontend: off task', conv: 'dm-frontend' },
  { kind: 'fresh_session', text: 'Frontend started a fresh session (context 61%)', conv: 'dm-frontend' },
  { kind: 'rate_limit', text: /^Rate limited, turns held until \d{1,2}:\d{2}/, conv: null },
]
const TOAST_KIND: Record<string, string> = { question_message: 'question' }

for (const c of CASES) {
  test(`${c.kind} shows its toast and a click opens the conversation`, async ({ page, qa }) => {
    await qa.open('/p/demo')
    await qa.fire(c.kind, c.body)
    const toast = page.locator(`[data-toast="${TOAST_KIND[c.kind] || c.kind}"]`)
    const body = toast.locator('button[title]')
    await expect(body).toHaveAttribute('title', c.text)
    if (!c.conv) return
    await body.click()
    await expect(page).toHaveURL(new RegExp(`[?&]c=${c.conv}(&|$)`))
    await expect(toast).toHaveCount(0)
  })
}

test('a long toast keeps the full text (clamped on screen, full on hover)', async ({ page, qa }) => {
  await qa.open('/p/demo')
  const words = Array.from({ length: 120 }, (_, i) => `word${i}`).join(' ')
  await qa.fire('question_message', { text: `@master ${words} END` })
  const body = page.locator('[data-toast="question"] button[title]')
  await expect(body).toHaveAttribute('title', `Lead 1 asks: ${words} END`)
  await expect(body).toContainText('word119 END')
  const clamp = body.locator('.line-clamp-5')
  expect(await clamp.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
})

test('toasts stack, dismiss with ×, and auto-dismiss after 15 s', async ({ page, qa }) => {
  await page.clock.install()
  await qa.open('/p/demo')
  await qa.fire('standup')
  await qa.fire('fresh_session')
  await expect(page.locator('[data-toast]')).toHaveCount(2)
  await page.locator('[data-toast="fresh_session"]').getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.locator('[data-toast="fresh_session"]')).toHaveCount(0)
  await page.mouse.move(10, 10) // off the stack: hovering it pauses the timers
  const standup = page.locator('[data-toast="standup"]')
  await page.clock.runFor(13_000)
  await expect(standup).toBeVisible()
  await page.clock.runFor(3_000)
  await expect(standup).toHaveCount(0)
})
