import type { Page } from '@playwright/test'
import { TONE_FREQ } from '../src/alertSound'
import { installAlertStubs, recorded, type AlertStubConfig } from './alertStub'
import { expect, test } from './fixtures'

// F-28: sound and desktop alerts for the Master. AudioContext and Notification are stubbed (alertStub.ts), so the specs
// read what the app played and showed from window.__alerts.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
type Group = keyof typeof TONE_FREQ

const open = async (page: Page, qa: { open: (r: string) => Promise<void> }, cfg: AlertStubConfig = {}, route = '/p/demo') => {
  await installAlertStubs(page, cfg)
  await qa.open(route)
}
// One alert is a sound then a pop-up, in that order.
const both = (group: Group, key: string, title: string) => [
  { via: 'sound', freq: TONE_FREQ[group] },
  { via: 'popup', key, title },
]

const CASES: { name: string; fire: [string, Record<string, unknown>?]; group: Group; key: (r: any) => string; title: string }[] = [
  { name: 'an approval', fire: ['approval', { text: 'Design: alerts' }], group: 'sign-off', key: (r) => `approval:${r.approval}`, title: 'Approval needed · F-1' },
  { name: 'a commit request', fire: ['commit_request'], group: 'sign-off', key: (r) => `commit:${r.commit}:master_review`, title: 'Commit request · F-1' },
  { name: 'a question card', fire: ['card'], group: 'answer', key: (r) => `question:${r.question}`, title: 'Question · F-1' },
  { name: 'a blocking question', fire: ['question'], group: 'answer', key: (r) => `question:${r.question}`, title: 'Question' },
  { name: 'a Question to @master', fire: ['question_message'], group: 'answer', key: (r) => `msg:${r.message}`, title: 'Question · F-1' },
  { name: 'a Blocker to @master', fire: ['blocker'], group: 'answer', key: (r) => `msg:${r.message}`, title: 'Blocker · F-1' },
  {
    name: 'a plain message to @master',
    fire: ['message', { role: 'product', text: '@master the export is done', mentions: ['master'], conversation: 'general' }],
    group: 'status',
    key: (r) => `msg:${r.message}`,
    title: 'Message for you',
  },
]

for (const c of CASES) {
  test(`${c.name} gives exactly one alert, in the ${c.group} tone`, async ({ page, qa }) => {
    await open(page, qa)
    expect(await recorded(page)).toEqual([])
    const fired = await qa.fire(...c.fire)
    // The approval, commit and card also post a chat copy that mentions @master: it must not alert a second time. A marker
    // item fired next arrives after every frame of the first (one socket, in order), so a duplicate would sit between them.
    const marker = await qa.fire('blocker')
    await expect
      .poll(() => recorded(page))
      .toEqual([...both(c.group, c.key(fired), c.title), ...both('answer', `msg:${marker.message}`, 'Blocker · F-1')])
  })
}

test('the three tones start on three different notes', () => {
  expect(new Set(Object.values(TONE_FREQ)).size).toBe(3)
})

test('messages between roles, and ones from the Master, give no alert', async ({ page, qa }) => {
  await open(page, qa)
  await qa.fire('message', { role: 'product', text: '@lead can you check the scope?', mentions: ['lead'], conversation: 'general' })
  await qa.fire('message', { role: 'master', text: '@master note to self', mentions: ['master'], conversation: 'general' })
  // the next real item arrives after both frames, so it is the only alert
  const { approval } = await qa.fire('approval', { text: 'Design: marker' })
  await expect.poll(() => recorded(page)).toEqual(both('sign-off', `approval:${approval}`, 'Approval needed · F-1'))
})

test('answering or withdrawing a card does not alert again', async ({ page, qa }) => {
  await open(page, qa)
  const { question } = await qa.fire('card')
  await expect.poll(() => recorded(page)).toHaveLength(2)
  await qa.fire('card_withdraw', { question })
  const { approval } = await qa.fire('approval', { text: 'Design: after the withdraw' })
  await expect.poll(() => recorded(page)).toEqual([...both('answer', `question:${question}`, 'Question · F-1'), ...both('sign-off', `approval:${approval}`, 'Approval needed · F-1')])
})

test('reloading plays nothing for items that already exist', async ({ page, qa }) => {
  await open(page, qa)
  await qa.fire('approval', { text: 'Design: before the reload' })
  await qa.fire('blocker')
  await expect.poll(() => recorded(page)).toHaveLength(4)
  await qa.open('/p/demo?c=inbox') // a fresh load of the same page: the stub (and __alerts) start empty again
  await expect(page.locator('[data-card^="approval-"]').first()).toBeVisible()
  expect(await recorded(page)).toEqual([])
  // and it is not just deaf: a new item still alerts
  const { approval } = await qa.fire('approval', { text: 'Design: after the reload' })
  await expect.poll(() => recorded(page)).toEqual(both('sign-off', `approval:${approval}`, 'Approval needed · F-1'))
})

test('the Sound switch and the Pop-up switch work separately and are remembered', async ({ page, qa }) => {
  await open(page, qa)
  const trigger = page.locator('[data-alerts-trigger]')
  const sound = page.locator('[data-alert-switch="sound"]')
  const popup = page.locator('[data-alert-switch="popup"]')
  await trigger.click()
  await expect(sound).toBeChecked() // both on by default
  await expect(popup).toBeChecked()
  await page.screenshot({ path: `${SHOTS}/alerts-popover.png`, animations: 'disabled' })

  await sound.click() // Sound off: the pop-up still shows
  const noSound = await qa.fire('approval', { text: 'Design: no sound' })
  await expect.poll(() => recorded(page)).toEqual([{ via: 'popup', key: `approval:${noSound.approval}`, title: 'Approval needed · F-1' }])

  await qa.open('/p/demo') // reload: the choice stays
  await trigger.click()
  await expect(sound).not.toBeChecked()
  await expect(popup).toBeChecked()
  await sound.click() // Sound back on, Pop-up off: the sound still plays
  await popup.click()
  await qa.fire('approval', { text: 'Design: no popup' })
  await expect.poll(() => recorded(page)).toEqual([{ via: 'sound', freq: TONE_FREQ['sign-off'] }])

  await qa.open('/p/demo')
  await trigger.click()
  await expect(sound).toBeChecked()
  await expect(popup).not.toBeChecked()
  await sound.click() // Pop-up is still off from before, so now both are off: nothing at all
  await expect(sound).not.toBeChecked()
  await qa.fire('approval', { text: 'Design: silence' })
  await expect(page.locator('[data-toast="approval"]')).toBeVisible() // the toast is not an alert and still shows
  expect(await recorded(page)).toEqual([])
})

test('several tabs: exactly one alerts, and the other takes over when it closes', async ({ page, qa }) => {
  await installAlertStubs(page.context())
  await qa.open('/p/demo')
  const first = page
  const second = await page.context().newPage()
  const socket = second.waitForEvent('websocket', (w) => w.url().includes('/ws/p/demo'))
  await second.goto('/p/demo')
  const sock = await socket
  await expect(async () => {
    const frame = sock.waitForEvent('framereceived', { timeout: 1000 })
    await qa.fire('message', { conversation: 'general', role: 'manager', text: 'ws probe' })
    await frame
  }).toPass({ timeout: 15_000 })

  await expect(first.locator('[data-alerts-trigger]')).toHaveAttribute('data-alerting', 'true')
  await expect(second.locator('[data-alerts-trigger]')).toHaveAttribute('data-alerting', 'false')
  const a = await qa.fire('approval', { text: 'Design: two tabs' })
  await expect(second.locator('[data-toast="approval"]')).toBeVisible() // the second tab saw the frame...
  await expect.poll(() => recorded(first)).toEqual(both('sign-off', `approval:${a.approval}`, 'Approval needed · F-1'))
  expect(await recorded(second)).toEqual([]) // ...and stayed quiet

  await first.close()
  await expect(second.locator('[data-alerts-trigger]')).toHaveAttribute('data-alerting', 'true')
  const b = await qa.fire('blocker')
  await expect.poll(() => recorded(second)).toEqual(both('answer', `msg:${b.message}`, 'Blocker · F-1'))
})

test('a blocked sound and blocked pop-ups say so; the first click clears the sound hint', async ({ page, qa }) => {
  await open(page, qa, { audio: 'suspended', permission: 'denied' })
  const soundHint = page.locator('[data-alert-hint="sound"]')
  const popupHint = page.locator('[data-alert-hint="popup"]')
  await expect(soundHint).toHaveText('Click anywhere to enable sound')
  await expect(popupHint).toHaveText('Pop-ups are blocked by the browser')
  await page.screenshot({ path: `${SHOTS}/alerts-hints.png`, animations: 'disabled' })

  // A suspended context would sound the tone late, at the next click: nothing is played, the pop-up is blocked too.
  await qa.fire('approval', { text: 'Design: blocked' })
  await expect(page.locator('[data-toast="approval"]')).toBeVisible()
  expect(await recorded(page)).toEqual([])

  await page.getByRole('heading', { name: 'Dashboard' }).click() // any click
  await expect(soundHint).toHaveCount(0)
  await expect(popupHint).toBeVisible() // the browser setting is not ours to change
  await qa.fire('blocker')
  await expect.poll(() => recorded(page)).toEqual([{ via: 'sound', freq: TONE_FREQ.answer }])
})

test('a hint only shows while its switch is on', async ({ page, qa }) => {
  await open(page, qa, { audio: 'suspended', permission: 'denied' })
  await page.locator('[data-alerts-trigger]').click() // this click also unlocks the sound
  await page.locator('[data-alert-switch="popup"]').click()
  await expect(page.locator('[data-alert-hint="popup"]')).toHaveCount(0)
  await page.locator('[data-alert-switch="popup"]').click()
  await expect(page.locator('[data-alert-hint="popup"]')).toBeVisible()
})

test('with permission still undecided, the first click asks for it, once', async ({ page, qa }) => {
  await open(page, qa, { permission: 'default' })
  const requests = () => page.evaluate(() => (window as any).__notificationRequests as number)
  expect(await requests()).toBe(0) // nothing is asked on load: browsers want a gesture
  await qa.fire('approval', { text: 'Design: before any click' })
  await expect(page.locator('[data-toast="approval"]')).toBeVisible()
  expect(await recorded(page)).toEqual([{ via: 'sound', freq: TONE_FREQ['sign-off'] }]) // sound only: no permission yet
  await page.getByRole('heading', { name: 'Dashboard' }).click()
  await expect.poll(requests).toBe(1)
  await page.getByRole('heading', { name: 'Dashboard' }).click()
  await page.keyboard.press('Shift')
  expect(await requests()).toBe(1)
  const { approval } = await qa.fire('approval', { text: 'Design: after the click' })
  await expect.poll(async () => (await recorded(page)).filter((e) => e.via === 'popup')).toEqual([{ via: 'popup', key: `approval:${approval}`, title: 'Approval needed · F-1' }])
})

test('clicking a pop-up focuses the app and opens the item', async ({ page, qa }) => {
  await open(page, qa)
  const click = () => page.evaluate(() => (window as any).__notifications.at(-1).onclick())
  await qa.fire('approval', { text: 'Design: click me' })
  await expect.poll(() => recorded(page)).toHaveLength(2)
  await click()
  await expect(page).toHaveURL(/[?&]c=feature-1&m=\d+/)
  await expect(page.getByRole('heading', { name: 'Export orders to CSV' })).toBeVisible()

  await qa.open('/p/demo?c=general') // elsewhere again (a reload, so the stub starts empty); a blocking question opens its role's DM
  await qa.fire('question')
  await expect.poll(() => recorded(page)).toHaveLength(2)
  await click()
  await expect(page).toHaveURL(/[?&]c=dm-lead(&|$)/)
})
