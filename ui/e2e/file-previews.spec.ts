import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-50: a file a message or a card names (an html page or an image) shows as a thumbnail at the end of it; a click opens the full
// view, as the chat previews always did (mocks.spec.ts covers those and the sandbox). The sandbox's F-1 docs folder holds
// mock-export.html, shot.png and flow.svg. Cards are fired through the real paths with texts that name them.

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
const SANDBOX = 'allow-scripts allow-forms allow-popups allow-modals'
const D = '.aiteams/features/F-1'

const thumbs = (scope: ReturnType<Page['locator']>) => scope.locator('[data-mock-card]')
const names = (scope: ReturnType<Page['locator']>) => thumbs(scope).evaluateAll((els) => els.map((e) => e.getAttribute('data-mock-card')))

test('an approval card shows its doc (a bare name in its feature) and the files its brief names, once', async ({ page, qa }) => {
  const { approval: id } = await qa.fire('approval', {
    text: 'Design: Show the mock',
    brief: { doc: 'mock-export.html', what: `Compare it with ${D}/shot.png.`, tradeoffs: ['Looks like flow.svg'] },
  })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect(thumbs(card)).toHaveCount(3)
  await expect.poll(async () => (await names(card)).sort()).toEqual(['flow.svg', 'mock-export.html', 'shot.png'])
  // the message that carries the card does not read its own text a second time
  await expect(thumbs(page.locator('[id^="m-"]', { has: card }))).toHaveCount(3)
  const frame = card.locator('[data-mock-card="mock-export.html"] iframe')
  await expect(frame).toHaveAttribute('sandbox', SANDBOX)
  await expect(page.frameLocator(`[data-card="approval-${id}"] [data-mock-card="mock-export.html"] iframe`).getByRole('heading', { name: 'Orders' })).toBeAttached()
  await page.screenshot({ path: `${SHOTS}/file-previews-approval.png`, animations: 'disabled' })

  await card.locator('[data-mock-card="shot.png"]').click() // a click opens the full view
  const dialog = page.locator('[data-mock-dialog="shot.png"]')
  await expect(dialog.locator('[data-mock-image]')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

test('a question card shows the files its intro, questions and options name', async ({ page, qa }) => {
  const { question: id } = await qa.fire('card', {
    intro: `Look at ${D}/flow.svg first.`,
    questions: [{ question: 'Which one?', header: 'Pick', options: [{ label: 'A', description: 'the layout in shot.png' }, { label: 'B', description: 'nothing to see' }] }],
  })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-question-card="${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect.poll(async () => (await names(card)).sort()).toEqual(['flow.svg', 'shot.png'])
  await expect(thumbs(page.locator('[id^="m-"]', { has: card }))).toHaveCount(2)
  await page.screenshot({ path: `${SHOTS}/file-previews-question.png`, animations: 'disabled' })
  // answering closes the card; its intro still shows its file
  await card.getByRole('button', { name: /^A/ }).click()
  await expect(card).toHaveAttribute('data-state', 'answered')
  await expect.poll(() => names(card)).toEqual(['flow.svg'])
})

test('a commit card shows the files its message names', async ({ page, qa }) => {
  const { commit: id } = await qa.fire('commit_request', { text: `feat(ui): the export button\n\nThe layout is in ${D}/shot.png.` })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="commit-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect.poll(() => names(card)).toEqual(['shot.png'])
  await expect(thumbs(page.locator('[id^="m-"]', { has: card }))).toHaveCount(1)
})

test('the Inbox shows the previews of its approval, question and commit cards, and of a message, each once', async ({ page, qa }) => {
  // not a Design card: a pending one would hold the commit request back
  const { approval } = await qa.fire('approval', { text: 'Scope lock: Show the mock', brief: { doc: 'mock-export.html' } })
  const { question } = await qa.fire('card', { intro: `See ${D}/flow.svg.` })
  const { commit } = await qa.fire('commit_request', { text: `feat: x\n\n${D}/shot.png` })
  await qa.fire('message', { conversation: 'feature-1', role: 'lead', text: `@master Inbox note ${D}/shot.png`, type: 'Question', mentions: ['master'] })
  await qa.open('/p/demo?c=inbox')
  await expect.poll(() => names(page.locator(`[data-card="approval-${approval}"]`))).toEqual(['mock-export.html'])
  await expect.poll(() => names(page.locator(`[data-question-card="${question}"]`))).toEqual(['flow.svg'])
  await expect.poll(() => names(page.locator(`[data-card="commit-${commit}"]`))).toEqual(['shot.png'])
  const msg = page.locator('[id^="m-"]', { hasText: 'Inbox note' })
  await expect.poll(() => names(msg)).toEqual(['shot.png'])
  await page.screenshot({ path: `${SHOTS}/file-previews-inbox.png`, animations: 'disabled' })
})

test('a commit group shows the previews of its members\' messages', async ({ page, qa }) => {
  await qa.fire('commit_group', { text: `group backend half: the layout is in ${D}/shot.png` })
  await qa.open('/p/demo?c=inbox')
  const group = page.locator('[data-card^="commit-group-"]')
  await expect(group).toBeVisible()
  await expect.poll(() => names(group)).toEqual(['shot.png'])
})

test('a file that is not there shows a "not found" chip, and a bare name with no feature shows nothing', async ({ page, qa }) => {
  await qa.fire('card', { intro: 'See `nope.png` and ui/test-results/shots/none.png.' })
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator('[data-mock-missing="nope.png"]')).toHaveText('not found: nope.png')
  await expect(page.locator('[data-mock-missing="none.png"]')).toHaveText('not found: none.png')
  // a channel is not a feature: its bare names are words, and its full paths still preview
  await qa.open('/p/demo?c=general')
  await qa.fire('message', { conversation: 'general', role: 'lead', text: `shot.png and ${D}/flow.svg` })
  const msg = page.locator('[id^="m-"]', { hasText: 'shot.png and' })
  await expect(thumbs(msg)).toHaveCount(1)
  await expect(msg.locator('[data-mock-card="flow.svg"]')).toBeVisible()
  await expect(msg.locator('[data-mock-missing]')).toHaveCount(0)
})

// The sandbox's `previews` fire (F-1 by default) gives F-1 a worktree holding ui/test-results/shots/tree-shot.png (200x120), puts
// qa/shots/project-shot.png (120x80) in the project folder, and files an approval, a question card and a commit request that name
// files in every form: a docs path, a bare name, a path relative to the tree and to the project, an absolute path, a missing one.
const wide = (scope: ReturnType<Page['locator']>, name: string) => scope.locator(`[data-mock-card="${name}"] img`).evaluate((el: HTMLImageElement) => el.naturalWidth)

test('the files a role saved in its worktree and in the project folder preview, by relative and absolute path', async ({ page, qa }) => {
  const { approval } = await qa.fire('previews')
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${approval}"]`)
  await card.scrollIntoViewIfNeeded()
  // docs path (shot.png), the doc as a bare name (mock-export.html), a path in the feature's tree, a path in the project folder
  await expect.poll(async () => (await names(card)).sort()).toEqual(['mock-export.html', 'project-shot.png', 'shot.png', 'tree-shot.png'])
  await expect.poll(() => wide(card, 'tree-shot.png')).toBe(200) // the tree's file, not another one
  await expect.poll(() => wide(card, 'project-shot.png')).toBe(120)
  await expect(card.locator('[data-mock-missing]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/file-previews-worktree.png`, animations: 'disabled' })
  await card.locator('[data-mock-card="tree-shot.png"]').click()
  const dialog = page.locator('[data-mock-dialog="tree-shot.png"]')
  await expect(dialog.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute('href', /^\/api\/p\/demo\/preview\?path=ui%2Ftest-results%2Fshots%2Ftree-shot\.png&feature=1$/)
  await expect.poll(() => dialog.locator('[data-mock-image]').evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(200)
})

test('a question card shows the first four files, a chip for one that is missing, and links for the rest', async ({ page, qa }) => {
  const { question } = await qa.fire('previews')
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-question-card="${question}"]`)
  await card.scrollIntoViewIfNeeded()
  // the intro's docs path and its missing file, the question's project path (its bare name is the intro's file again) and the first
  // option's tree path; the second option's absolute path is the fifth reference
  await expect(card.locator('[data-mock-previews] > *')).toHaveCount(4)
  await expect.poll(async () => (await names(card)).sort()).toEqual(['mock-export.html', 'project-shot.png', 'tree-shot.png'])
  await expect(card.locator('[data-mock-missing="missing.png"]')).toHaveText('not found: missing.png')
  const more = card.locator('[data-file-extras]')
  await expect(more.locator('a')).toHaveText(['tree-shot.png'])
  await expect(more.locator('a')).toHaveAttribute('target', '_blank')
  await expect(more.locator('a')).toHaveAttribute('href', /^\/api\/p\/demo\/preview\?path=%2F.*tree-shot\.png&feature=1$/)
  await page.screenshot({ path: `${SHOTS}/file-previews-question-more.png`, animations: 'disabled' })
})

test('a commit card shows an absolute path, a bare name and a project path', async ({ page, qa }) => {
  const { commit } = await qa.fire('previews')
  await qa.open('/p/demo?c=inbox')
  const card = page.locator(`[data-card="commit-${commit}"]`)
  await expect.poll(async () => (await names(card)).sort()).toEqual(['mock-export.html', 'project-shot.png', 'tree-shot.png'])
  await expect.poll(() => wide(card, 'tree-shot.png')).toBe(200)
})
