import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-32: a message that @mentions the Master gets a soft blue tint and a 2px blue bar, so it stands out in the chat. Not @all,
// not the Master's own messages, not System lines. The amber jump highlight wins while it shows. Not in the Inbox.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'

type Fire = { fire: (k: string, b?: Record<string, unknown>) => Promise<Record<string, unknown>> }
const post = async (qa: Fire, role: string, text: string, mentions: string[]) =>
  (await qa.fire('message', { conversation: 'feature-1', role, text, mentions })).message as number
const fireId = async (qa: Fire, kind: string, role: string) => (await qa.fire(kind, { conversation: 'feature-1', role })).message as number

// What `border-blue-400` / `bg-blue-50/60` / `bg-amber-50` compute to in this build, measured on a probe element.
async function colours(page: Page) {
  return page.evaluate(() => {
    const probe = (cls: string, prop: string) => {
      const e = document.createElement('div')
      e.className = cls
      e.style.borderLeftStyle = 'solid'
      document.body.appendChild(e)
      const v = getComputedStyle(e).getPropertyValue(prop)
      e.remove()
      return v
    }
    return {
      bar: probe('border-l-2 border-blue-400', 'border-left-color'),
      tint: probe('bg-blue-50/60', 'background-color'),
      amber: probe('bg-amber-50', 'background-color'),
    }
  })
}

// Where the avatar and the text column start: the same x on a tinted row as on a plain one.
async function xs(row: Locator) {
  return row.evaluate((r) => {
    const x = (e: Element | null) => Math.round((e?.getBoundingClientRect().x ?? -1) * 10) / 10
    return { avatar: x(r.children[0]), text: x(r.children[1]) }
  })
}

test('only a message that mentions master (and is not from master) is tinted, with a 2px blue bar', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  // (three role messages in a row would fold into "3 replies", so the master's own message sits between them)
  const tagged = await post(qa, 'backend', 'Tagged: @master a quick check on the export', ['master'])
  const own = await post(qa, 'master', 'My own: @master note to self', ['master'])
  const all = await post(qa, 'backend', 'Everyone: @all the export is live', ['all'])
  const none = await post(qa, 'backend', 'No mentions in this one', [])
  const row = (id: number) => page.locator(`#m-${id}`)
  await expect(row(none)).toBeVisible()

  await expect(page.locator('[data-for-master]')).toHaveCount(2) // the seeded approval card (#7) and the tagged one
  await expect(row(tagged)).toHaveAttribute('data-for-master', '')
  for (const id of [all, none, own]) await expect(row(id)).not.toHaveAttribute('data-for-master', '')

  const c = await colours(page)
  expect(c.tint).not.toBe(c.amber)
  await expect(row(tagged)).toHaveCSS('border-left-width', '2px')
  await expect(row(tagged)).toHaveCSS('border-left-color', c.bar)
  await expect(row(tagged)).toHaveCSS('background-color', c.tint)
  for (const id of [all, none, own]) {
    await expect(row(id)).toHaveCSS('border-left-width', '0px')
    await expect(row(id)).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
  }

  // hovering keeps the tint; hovering a plain row still greys it
  await row(tagged).hover()
  await expect(row(tagged)).toHaveCSS('background-color', c.tint)
  await row(none).hover()
  await expect(row(none)).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')

  await row(tagged).scrollIntoViewIfNeeded()
  await page.mouse.move(0, 0) // no hover grey on the plain rows in the shot
  await page.screenshot({ path: `${SHOTS}/master-highlight.png`, animations: 'disabled' })
})

test('a card that reaches the master is tinted too: the seeded approval, and a Question card', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const approval = (await qa.api<{ id: number; ref: string | null }[]>('/messages?conversation=feature-1&limit=200')).find((m) => m.ref?.startsWith('approval:'))!
  await expect(page.locator(`#m-${approval.id}`)).toHaveAttribute('data-for-master', '')
  const q = await fireId(qa, 'question_message', 'backend')
  await expect(page.locator(`#m-${q}`)).toHaveAttribute('data-for-master', '')
  await expect(page.locator(`#m-${q}`)).toHaveCSS('border-left-width', '2px')
})

test('the avatar and the text sit at the same x on a tinted row as on a plain one', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const tagged = await post(qa, 'backend', 'Tagged: @master look', ['master'])
  const plain = await post(qa, 'backend', 'Plain one', [])
  await expect(page.locator(`#m-${plain}`)).toBeVisible()
  const a = await xs(page.locator(`#m-${tagged}`))
  const b = await xs(page.locator(`#m-${plain}`))
  expect(a.avatar).toBeGreaterThan(0)
  expect(a).toEqual(b)
})

test('the jump highlight wins: amber and no bar while it shows, the blue is back once the jump is gone', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const tagged = await post(qa, 'backend', 'Tagged: @master jump here', ['master'])
  const plain = await post(qa, 'backend', 'A plain row to measure against', [])
  const c = await colours(page)

  await page.goto(`/p/demo?c=feature-1&m=${tagged}`)
  const row = page.locator(`#m-${tagged}`)
  await expect(row).toBeVisible()
  await expect(row).toHaveCSS('background-color', c.amber)
  await expect(row).toHaveCSS('border-left-width', '0px')
  await expect(page.locator(`#m-${plain}`)).toBeVisible()
  expect(await xs(row)).toEqual(await xs(page.locator(`#m-${plain}`))) // amber rows keep the same alignment too

  await page.goto('/p/demo?c=feature-1') // no ?m=: nothing is highlighted any more
  await expect(page.locator(`#m-${tagged}`)).toHaveCSS('background-color', c.tint)
  await expect(page.locator(`#m-${tagged}`)).toHaveCSS('border-left-width', '2px')
})

test('the Inbox does not tint its message rows: every row there is for the master', async ({ page, qa }) => {
  const id = await fireId(qa, 'blocker', 'backend')
  await qa.open('/p/demo?c=inbox')
  const row = page.locator(`#m-${id}`)
  await expect(row).toBeVisible()
  await expect(row).not.toHaveAttribute('data-for-master', '')
  await expect(row).toHaveCSS('border-left-width', '0px')
  await expect(row).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
  // while the same message in its conversation is tinted
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator(`#m-${id}`)).toHaveAttribute('data-for-master', '')
})

test('a message that tags master is never folded into a run of replies', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  // 2 plain role messages, the tagged one, 2 more: 5 role messages in a row, which would fold into "5 replies" (a run folds
  // unless it is the last thing in the chat, so the master's own message ends it). The tagged one must break the run.
  const before = [await post(qa, 'backend', 'Plain before 1', []), await post(qa, 'qa', 'Plain before 2', [])]
  const tagged = await post(qa, 'lead', 'Tagged inside the run: @master please look', ['master'])
  const after = [await post(qa, 'backend', 'Plain after 1', []), await post(qa, 'qa', 'Plain after 2', [])]
  await post(qa, 'master', 'Noted.', [])

  const row = (id: number) => page.locator(`#m-${id}`)
  await expect(row(tagged)).toBeVisible()
  await expect(row(tagged)).toHaveAttribute('data-for-master', '')
  await expect(row(tagged)).toHaveCSS('border-left-width', '2px')
  for (const id of [...before, ...after]) await expect(row(id)).toBeVisible() // two in a row are not a run either
  await expect(page.getByText('5 replies')).toHaveCount(0) // nothing folded the five together
  await expect(page.getByText('3 replies')).toBeVisible() // the seeded fold of plain replies (#2-#4) is still a fold
  await expect(row(3)).toHaveCount(0)
  await row(tagged).scrollIntoViewIfNeeded()
  await page.mouse.move(0, 0)
  await page.screenshot({ path: `${SHOTS}/master-highlight-fold.png`, animations: 'disabled' })
})

test('three plain role messages in a row still fold, a tagged one beside them does not join the fold', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  const run = [await post(qa, 'backend', 'Fold me 1', []), await post(qa, 'qa', 'Fold me 2', []), await post(qa, 'backend', 'Fold me 3', [])]
  const tagged = await post(qa, 'lead', 'Tagged right after: @master', ['master'])
  await post(qa, 'master', 'Noted.', [])
  await expect(page.getByText('3 replies')).toHaveCount(2) // the seeded fold and this one
  await expect(page.locator(`#m-${run[1]}`)).toHaveCount(0) // folded away
  await expect(page.locator(`#m-${tagged}`)).toHaveAttribute('data-for-master', '') // but the tagged one is out in the open
})
