import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { Page } from '@playwright/test'
import { PID, TOKEN, expect, test } from './fixtures'

// F-50 QA: what file-previews.spec.ts leaves out. A path the daemon refuses (a real file outside the project, a `..` path, a `.git`
// path) is a "not found" chip in the UI and a 400 from the route, never a thumbnail; a plain message in a feature conversation
// previews every form, and a DM previews full paths only.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const D = '.aiteams/features/F-1'
const auth = { Authorization: `Bearer ${TOKEN}` }
const names = (scope: ReturnType<Page['locator']>) =>
  scope.locator('[data-mock-card]').evaluateAll((els) => els.map((e) => e.getAttribute('data-mock-card')))
const chips = (scope: ReturnType<Page['locator']>) =>
  scope.locator('[data-mock-missing]').evaluateAll((els) => els.map((e) => e.getAttribute('data-mock-missing')))

// a real png outside the project and its worktrees folder: it exists, so only the root check can refuse it
const outside = join(mkdtempSync(join(tmpdir(), 'f50-outside-')), 'outside.png')
writeFileSync(outside, Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex'))

test('the route refuses a file outside the project, a .. path, a .git path, another type and no token', async ({ request, qa }) => {
  const { tree } = await qa.fire('previews')
  const get = (path: string, headers: Record<string, string> = auth) =>
    request.get(`/api/p/${PID}/preview?path=${encodeURIComponent(path)}&feature=1`, { headers })
  expect((await get(outside)).status()).toBe(400)
  expect((await get(`../${D}/shot.png`)).status()).toBe(400)
  expect((await get(`${tree}/../f1/ui/test-results/shots/tree-shot.png`)).status()).toBe(400)
  expect((await get('.git/shot.png')).status()).toBe(400)
  expect((await get(`${D}/prd.md`)).status()).toBe(400)
  expect((await get('ui/test-results/shots/tree-shot.png', {})).status()).toBe(401)
  // the same file by an accepted form is served, with the mock's sandbox headers
  const ok = await get('ui/test-results/shots/tree-shot.png')
  expect(ok.status()).toBe(200)
  expect(ok.headers()['content-security-policy']).toContain('sandbox')
  // resolve answers each refused ref with kind null, never an error
  const r = await request.post(`/api/p/${PID}/preview/resolve`, {
    headers: auth,
    data: { feature: 1, refs: [outside, `../${D}/shot.png`, '.git/shot.png', 'shot.png'] },
  })
  expect(r.status()).toBe(200)
  expect((await r.json()).map((x: { kind: string | null }) => x.kind)).toEqual([null, null, null, 'image'])
})

test('a message naming refused paths shows chips for them and thumbnails only for the files it may show', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  await qa.fire('message', {
    conversation: 'feature-1',
    role: 'qa',
    text: `Refused: \`${outside}\`, \`../${D}/shot.png\` and \`.git/logo.png\`. Allowed: \`${D}/shot.png\`.`,
  })
  const msg = page.locator('[id^="m-"]', { hasText: 'Refused:' })
  await msg.scrollIntoViewIfNeeded()
  await expect.poll(() => names(msg)).toEqual(['shot.png'])
  await expect.poll(async () => (await chips(msg)).sort()).toEqual(['logo.png', 'outside.png', 'shot.png'])
  await page.screenshot({ path: `${SHOTS}/file-previews-refused.png`, animations: 'disabled' })
})

test('a plain message in a feature conversation previews every form; a DM previews full paths only', async ({ page, qa }) => {
  const { absolute } = await qa.fire('previews')
  await qa.open('/p/demo?c=feature-1')
  await qa.fire('message', {
    conversation: 'feature-1',
    role: 'qa',
    text: `Feature msg: ${D}/shot.png, mock-export.html, ui/test-results/shots/tree-shot.png, qa/shots/project-shot.png`,
  })
  const msg = page.locator('[id^="m-"]', { hasText: 'Feature msg:' })
  await msg.scrollIntoViewIfNeeded()
  await expect.poll(async () => (await names(msg)).sort()).toEqual(['mock-export.html', 'project-shot.png', 'shot.png', 'tree-shot.png'])
  await expect(msg.locator('[data-mock-missing]')).toHaveCount(0)
  await msg.locator('[data-mock-card="project-shot.png"]').click()
  await expect(page.locator('[data-mock-dialog="project-shot.png"] [data-mock-image]')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.screenshot({ path: `${SHOTS}/file-previews-feature-message.png`, animations: 'disabled' })

  await qa.open('/p/demo?c=dm-qa')
  await qa.fire('message', { conversation: 'dm-qa', role: 'qa', text: `DM msg: shot.png, ${D}/mock-export.html, ${absolute}` })
  const dm = page.locator('[id^="m-"]', { hasText: 'DM msg:' })
  await expect.poll(async () => (await names(dm)).sort()).toEqual(['mock-export.html', 'tree-shot.png'])
  await expect(dm.locator('[data-mock-missing]')).toHaveCount(0) // the bare name is a word here, not a reference
  await page.screenshot({ path: `${SHOTS}/file-previews-dm.png`, animations: 'disabled' })
})
