import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-38: while the whole team is idle, an open feature that waits on a role gets a chip, "Waiting on <role>" (grey) while the
// daemon nudges that role and then the Manager, and "Stuck · Waiting on <role>" (amber) once neither moved it. It shows in the
// feature header and, shorter, in the sidebar row; it clears live when anyone replies. A role whose turn has run or queued for a
// long time gets an amber "stuck" chip in the team strip.
//
// The sandbox seeds F-4 (build, Lead 2, owner backend) with nothing waiting on a role. A message from the Manager that @mentions
// Backend makes it wait on Backend; `stall_check` runs one rung of the idle ladder (the sandbox runs no turns, so the real "team
// went idle" trigger never fires) and `long_turn {role, minutes}` queues a wake that old.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'

const header = (page: Page) => page.locator('main [data-stall-chip]')
const sidebar = (page: Page) => page.locator('aside [data-stall-chip]')

// The Manager asks Backend for something in F-4: from now on F-4's newest word waits on Backend.
const ask = (qa: { fire: (k: string, b?: Record<string, unknown>) => Promise<any> }) =>
  qa.fire('message', { conversation: 'feature-4', role: 'manager', text: '@backend please file the schedule request', mentions: ['backend'] })

const rung = async (qa: { fire: (k: string, b?: Record<string, unknown>) => Promise<any> }) => ((await qa.fire('stall_check')).changes as { feature: string; step: number; who: string }[])

test('nothing waits on a role: no chip anywhere, and a feature waiting on the Master gets none either', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-4')
  await expect(page.locator('aside').getByRole('button', { name: /F-4\s+Order export schedule/ })).toBeVisible()
  await expect(header(page)).toHaveCount(0)
  await expect(sidebar(page)).toHaveCount(0)
  // F-1 holds pending Master items (an approval, a question, a commit): it waits on the Master, so even a message that
  // mentions Backend never starts a ladder there.
  await qa.fire('message', { conversation: 'feature-1', role: 'manager', text: '@backend status?', mentions: ['backend'] })
  expect(await rung(qa)).toEqual([])
  await expect(sidebar(page)).toHaveCount(0)
})

test('the ladder: grey "Waiting on Backend" twice, then amber "Stuck · Waiting on Backend", live in the header and the sidebar', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-4')
  await ask(qa)
  await expect(header(page)).toHaveCount(0) // a message alone starts nothing: the check runs when the team goes idle

  // Step 1: the role is nudged.
  expect(await rung(qa)).toEqual([{ feature: 'F-4', step: 1, who: 'backend' }])
  await expect(header(page)).toHaveText('Waiting on Backend')
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'waiting')
  await expect(header(page).locator('[data-slot="badge"]')).toHaveClass(/bg-gray-100/)
  await expect(sidebar(page)).toHaveCount(1)
  await expect(sidebar(page)).toHaveText('waiting · Backend')
  await expect(page.locator('aside').getByRole('button', { name: /F-4\s+Order export schedule/ }).locator('[data-stall-chip]')).toHaveCount(1)
  await expect(page.getByText(/^Nudged @backend:/)).toBeVisible() // the daemon's System line in the conversation
  await page.screenshot({ path: `${SHOTS}/stall-chip-waiting.png`, animations: 'disabled' })

  // Step 2: the Manager is told. Still grey: the daemon is still on it.
  expect(await rung(qa)).toEqual([{ feature: 'F-4', step: 2, who: 'backend' }])
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'waiting')
  await expect(header(page).locator('[data-slot="badge"]')).toHaveClass(/bg-gray-100/)
  await header(page).hover()
  await expect(page.getByRole('tooltip').first()).toContainText('nudged, the Manager told')

  // Step 3: nothing moved. Amber, and no more automatic wakes.
  expect(await rung(qa)).toEqual([{ feature: 'F-4', step: 3, who: 'backend' }])
  await expect(header(page)).toHaveText('Stuck · Waiting on Backend')
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'stuck')
  await expect(header(page).locator('[data-slot="badge"]')).toHaveClass(/bg-amber-100/)
  await expect(sidebar(page)).toHaveText('stuck · Backend')
  await expect(sidebar(page).locator('[data-slot="badge"]')).toHaveClass(/bg-amber-100/)
  await expect(page.getByText(/^Stuck: still waiting on Backend/)).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/stall-chip-stuck.png`, animations: 'disabled' })

  // The ladder ends there: a fourth idle changes nothing and the chip stays.
  expect(await rung(qa)).toEqual([])
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'stuck')
})

test('a reply clears the chip live in the header and the sidebar, at the grey step and at the amber one', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-4')
  await ask(qa)
  await rung(qa)
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'waiting')
  await qa.fire('message', { conversation: 'feature-4', role: 'backend', text: 'On it, filing it now.' })
  await expect(header(page)).toHaveCount(0)
  await expect(sidebar(page)).toHaveCount(0)

  // Asked again, nudged, told the Manager, stuck: a reply still clears it, and the ladder starts over at step 1.
  await ask(qa)
  for (let i = 0; i < 3; i++) await rung(qa)
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'stuck')
  await qa.fire('message', { conversation: 'feature-4', role: 'backend', text: 'Sorry, filed.' })
  await expect(header(page)).toHaveCount(0)
  await expect(sidebar(page)).toHaveCount(0)
  expect(((await qa.api<any[]>('/features')) as any[]).find((f) => f.key === 'F-4').stall).toBeNull()
  await ask(qa)
  expect(await rung(qa)).toEqual([{ feature: 'F-4', step: 1, who: 'backend' }])
  await expect(header(page)).toHaveAttribute('data-stall-chip', 'waiting')
})

// How each kind and step reads, from the payload alone: F-1's row in the /features answers is rewritten.
test('each kind of wait names the role and says what it is waiting for in the tooltip', async ({ page, qa }) => {
  const now = Date.now() / 1000
  const stalls: Record<string, any> = {
    review: { who: 'lead-2', kind: 'review', ref: 'commit:7', step: 1, stuck: false, since: now - 4 * 60 },
    message: { who: 'backend', kind: 'message', ref: 'msg:12', step: 2, stuck: false, since: now - 90 * 60 },
    unaddressed: { who: 'manager', kind: 'unaddressed', ref: 'msg:30', step: 3, stuck: true, since: now - 30 },
  }
  let current = stalls.review
  await page.route(/\/api\/p\/demo\/features(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: list.map((f: any) => (f.key === 'F-1' ? { ...f, stall: current } : f)) })
  })
  const cases: [string, string, string, string[]][] = [
    ['review', 'Waiting on Lead 2', 'waiting · Lead 2', ['Commit request #7 waits on its review', 'waiting 4 min', 'nudged']],
    ['message', 'Waiting on Backend', 'waiting · Backend', ['Message #12 asks Backend to act, with no reply', 'waiting 2 h', 'nudged, the Manager told']],
    ['unaddressed', 'Stuck · Waiting on Manager', 'stuck · Manager', ['Message #30 mentions nobody', 'waiting under a minute', 'no more automatic wakes']],
  ]
  for (const [kind, full, short, tip] of cases) {
    current = stalls[kind]
    await qa.open('/p/demo?c=feature-1')
    await expect(header(page)).toHaveText(full)
    await expect(sidebar(page)).toHaveText(short)
    await expect(header(page)).toHaveAttribute('data-stall-who', stalls[kind].who)
    await header(page).hover()
    for (const part of tip) await expect(page.getByRole('tooltip').first()).toContainText(part)
    await page.mouse.move(0, 0)
  }
})

test('a turn queued for a long time shows an amber "stuck" chip on that role in the team strip, live', async ({ page, qa }) => {
  await qa.open('/p/demo')
  const strip = page.locator('main > div').filter({ has: page.getByRole('button', { name: /^Manager\b/ }) }).first()
  const pill = (name: string) => strip.getByRole('button', { name: new RegExp(`^${name}\\b`) })
  await expect(strip.locator('[data-stuck]')).toHaveCount(0)

  const fired = await qa.fire('long_turn', { role: 'qa', minutes: 25 })
  expect(fired.role).toBe('qa')
  await expect(pill('Qa').locator('[data-stuck]')).toHaveText('stuck')
  await expect(pill('Qa').locator('[data-stuck]')).toHaveClass(/bg-amber-100/)
  await expect(strip.locator('[data-stuck]')).toHaveCount(1) // only the slow role
  await pill('Qa').hover()
  await expect(page.getByRole('tooltip').first()).toContainText('stuck: running or queued for a long time, not stopped')
  await page.screenshot({ path: `${SHOTS}/stall-team-strip.png`, animations: 'disabled' })

  // The same flag is on the /team answer a reload would read.
  const { roles } = await qa.api<{ roles: any[] }>('/team')
  expect(roles.filter((t) => t.stuck).map((t) => t.role)).toEqual(['qa'])
})
