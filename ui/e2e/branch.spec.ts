import { existsSync } from 'node:fs'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

// F-39 part 1: the Master confirms where a feature's code goes before any code is written. A `Branch:` approval carries the
// Lead's proposal (a new branch in its own worktree, the current branch, or another feature's worktree) in `payload`; the card
// lets the Master edit it, shows live warnings/errors for the choice and where the commits will land, and Approve sends the edited
// choice. The feature header says where the work goes: the branch, "current folder (<branch>)", or an amber "branch not confirmed".
//
// Part 1 pins the UI with page.route: the approval is created with the existing `approval` fire kind, the daemon's answers are
// rewritten to carry the payload, /branches and /branches/check are answered here (so nothing runs git), and the decide POST is
// answered here too (its body is the thing under test).

const SHOTS = process.env.SHOT_DIR || 'test-results/shots'
// The daemon's list of shared-looking branches (aiteams/branches.py SHARED_RE); the mock of /branches/check applies it.
const SHARED = /^(origin\/)?(main|master|trunk|dev|develop.*|development.*|release.*|staging|stage|production|prod|hotfix.*)(\/.*)?$/i

const TREE = '/Users/someone/AITeams-worktrees/AITeams/f1'
const proposal = (over: Record<string, unknown> = {}) => ({
  mode: 'new',
  branch: 'feat/f1-export-csv',
  base: 'origin/main',
  current: 'main',
  tree: TREE,
  worktrees: [
    { feature: 'F-37', branch: 'feat/f37-continue', tree: '/Users/someone/AITeams-worktrees/AITeams/f37' },
    { feature: 'F-38', branch: 'feat/f38-stalls', tree: '/Users/someone/AITeams-worktrees/AITeams/f38' },
  ],
  warnings: [],
  ...over,
})

type Calls = { checks: URLSearchParams[]; decides: Record<string, any>[] }

// A pending `Branch:` card on F-1 whose approval row carries `payload`, with the two read routes and the decide POST mocked.
async function branchCard(page: Page, qa: { fire: (k: string, b?: Record<string, unknown>) => Promise<any>; open: (route: string) => Promise<void> }, payload: Record<string, unknown>, state = 'pending') {
  const id = (await qa.fire('approval', { text: 'Branch: F-1', role: 'lead' })).approval as number
  const calls: Calls = { checks: [], decides: [] }
  await page.route(/\/api\/p\/demo\/messages\?.*conversation=feature-1/, async (route) => {
    const res = await route.fetch()
    const list = await res.json()
    for (const m of list) if (m.approval?.id === id) m.approval = { ...m.approval, payload, state }
    await route.fulfill({ response: res, json: list })
  })
  await page.route(/\/api\/p\/demo\/branches(\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        current: 'main',
        branches: [
          { name: 'origin/main', remote: true },
          { name: 'origin/develop', remote: true },
          { name: 'feat/f37-continue', remote: false },
          { name: 'main', remote: false },
        ],
      },
    }),
  )
  await page.route(/\/api\/p\/demo\/branches\/check\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    calls.checks.push(q)
    const branch = q.get('branch') ?? ''
    const errors = q.get('mode') === 'new' && branch === 'taken' ? ['A branch named taken already exists'] : []
    const warnings = SHARED.test(branch) ? [`Work would land directly on ${branch}, which looks like a shared branch`] : []
    return route.fulfill({ json: { warnings, errors } })
  })
  await page.route(new RegExp(`/api/p/demo/approvals/${id}/decide$`), async (route) => {
    const body = route.request().postDataJSON()
    calls.decides.push(body)
    await route.fulfill({ json: { id, state: body.decision === 'approve' ? 'approved' : body.decision === 'reject' ? 'rejected' : 'changes_requested' } })
  })
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect(card).toBeVisible()
  return { id, card, calls }
}

const radio = (card: ReturnType<Page['locator']>, name: string) => card.getByRole('radio', { name })
const lands = (card: ReturnType<Page['locator']>) => card.locator('[data-lands-on]')

test('a Branch card offers the three choices and says where the commits land', async ({ page, qa }) => {
  const { card } = await branchCard(page, qa, proposal())
  await expect(card.locator('[data-branch-card]')).toHaveAttribute('data-branch-mode', 'new')
  await expect(radio(card, 'New branch in its own worktree')).toBeChecked()
  await expect(radio(card, 'Current branch')).not.toBeChecked()
  await expect(radio(card, 'Existing worktree')).not.toBeChecked()
  await expect(card.getByLabel('Branch name')).toHaveValue('feat/f1-export-csv')
  await expect(card.getByRole('combobox', { name: 'Base' })).toHaveText('origin/main')
  await expect(lands(card)).toHaveText('Commits land on feat/f1-export-csv in ~/AITeams-worktrees/AITeams/f1') // the home dir is shown as ~
  await expect(card.locator('[data-branch-warning]')).toHaveCount(0)
  await expect(card.locator('[data-action="approve"]')).toBeEnabled()
  // The Base list is what /branches returned, and the Lead's pick is always in it.
  await card.getByRole('combobox', { name: 'Base' }).click()
  for (const b of ['origin/main', 'origin/develop', 'feat/f37-continue', 'main']) await expect(page.getByRole('option', { name: b, exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
})

test('editing the name into a shared-looking branch shows the warning live, and a taken name blocks Approve', async ({ page, qa }) => {
  const { card, calls } = await branchCard(page, qa, proposal())
  const name = card.getByLabel('Branch name')
  const approve = card.locator('[data-action="approve"]')

  await name.fill('release/1.2')
  await expect(card.locator('[data-branch-warning]')).toContainText('release/1.2')
  await expect(card.locator('[data-branch-warning]')).toContainText('shared branch')
  await expect(lands(card)).toContainText('release/1.2') // the line follows the edit
  await expect(approve).toBeEnabled() // a warning never refuses
  await page.screenshot({ path: `${SHOTS}/branch-card-warning.png`, animations: 'disabled' })
  const q = calls.checks.at(-1)!
  expect([q.get('mode'), q.get('branch'), q.get('base')]).toEqual(['new', 'release/1.2', 'origin/main'])

  await name.fill('feat/f1-ok')
  await expect(card.locator('[data-branch-warning]')).toHaveCount(0)

  await name.fill('taken')
  await expect(card.locator('[data-branch-error]')).toHaveText('A branch named taken already exists')
  await expect(approve).toBeDisabled()
  await name.fill('feat/f1-free')
  await expect(card.locator('[data-branch-error]')).toHaveCount(0)
  await expect(approve).toBeEnabled()

  await name.fill('') // nothing to create
  await expect(approve).toBeDisabled()
})

test('the current branch and an existing worktree change where the commits land', async ({ page, qa }) => {
  const { card, calls } = await branchCard(page, qa, proposal())
  await radio(card, 'Current branch').click()
  await expect(card.getByLabel('Branch name')).toHaveCount(0) // the new-branch fields go with the mode
  await expect(lands(card)).toHaveText('Commits land on main in the main folder')
  await expect(card.locator('[data-branch-warning]')).toContainText('main') // the main folder is on a shared-looking branch
  expect(calls.checks.at(-1)!.get('mode')).toBe('current')

  await radio(card, 'Existing worktree').click()
  await expect(lands(card)).toHaveText('Commits land on feat/f37-continue in ~/AITeams-worktrees/AITeams/f37')
  await expect(card.locator('[data-branch-warning]')).toHaveCount(0)
  await card.getByRole('combobox', { name: 'Worktree' }).click()
  await page.getByRole('option', { name: 'F-38 · feat/f38-stalls' }).click()
  await expect(lands(card)).toHaveText('Commits land on feat/f38-stalls in ~/AITeams-worktrees/AITeams/f38')
  await expect.poll(() => calls.checks.at(-1)?.get('shared_from')).toBe('F-38')
})

test('with no other worktree the Existing worktree choice is disabled', async ({ page, qa }) => {
  const { card } = await branchCard(page, qa, proposal({ worktrees: [] }))
  await expect(radio(card, 'Existing worktree')).toBeDisabled()
  await expect(card).toContainText('No other feature has a worktree yet')
})

test('Approve sends the edited choice; Reject and Request changes send none', async ({ page, qa }) => {
  const a = await branchCard(page, qa, proposal())
  await a.card.getByLabel('Branch name').fill('feat/f1-edited')
  await a.card.getByRole('combobox', { name: 'Base' }).click()
  await page.getByRole('option', { name: 'origin/develop' }).click()
  await a.card.locator('[data-action="approve"]').click()
  await expect.poll(() => a.calls.decides.length).toBe(1)
  expect(a.calls.decides[0]).toEqual({ decision: 'approve', payload: { mode: 'new', branch: 'feat/f1-edited', base: 'origin/develop' } })
  // Decided: the card says what was approved, with no choices left to edit.
  await expect(a.card.locator('[data-action]')).toHaveCount(0)
  await expect(a.card.getByRole('radio')).toHaveCount(0)
  await expect(a.card.locator('[data-lands-on]')).toContainText('feat/f1-export-csv') // the stored proposal; the daemon rewrites it on approve
})

test('Approve on the current branch and on a worktree sends just that choice', async ({ page, qa }) => {
  const a = await branchCard(page, qa, proposal())
  await radio(a.card, 'Current branch').click()
  await a.card.locator('[data-action="approve"]').click()
  await expect.poll(() => a.calls.decides.length).toBe(1)
  expect(a.calls.decides[0]).toEqual({ decision: 'approve', payload: { mode: 'current' } })
})

test('Approve on an existing worktree sends the feature it shares', async ({ page, qa }) => {
  const a = await branchCard(page, qa, proposal())
  await radio(a.card, 'Existing worktree').click()
  await a.card.getByRole('combobox', { name: 'Worktree' }).click()
  await page.getByRole('option', { name: 'F-38 · feat/f38-stalls' }).click()
  await a.card.locator('[data-action="approve"]').click()
  await expect.poll(() => a.calls.decides.length).toBe(1)
  expect(a.calls.decides[0]).toEqual({ decision: 'approve', payload: { mode: 'shared', shared_from: 'F-38' } })
})

test('Reject and Request changes carry no payload', async ({ page, qa }) => {
  const a = await branchCard(page, qa, proposal())
  await a.card.getByLabel('Branch name').fill('feat/f1-edited')
  await a.card.locator('[data-action="changes"]').click()
  await expect.poll(() => a.calls.decides.length).toBe(1)
  expect(a.calls.decides[0]).toEqual({ decision: 'changes' })
  const b = await branchCard(page, qa, proposal())
  await b.card.locator('[data-action="reject"]').click()
  await expect.poll(() => b.calls.decides.length).toBe(1)
  expect(b.calls.decides[0]).toEqual({ decision: 'reject' })
})

test('a decided Branch card says what was approved', async ({ page, qa }) => {
  const { card } = await branchCard(page, qa, proposal({ mode: 'new', base: 'origin/main' }), 'approved')
  const body = card.locator('[data-branch-card]')
  await expect(body).toContainText('New branch in its own worktree from origin/main')
  await expect(lands(card)).toHaveText('Commits land on feat/f1-export-csv in ~/AITeams-worktrees/AITeams/f1')
  await expect(card.getByRole('radio')).toHaveCount(0)
  await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0)
})

test('a payload sent as text still renders the card', async ({ page, qa }) => {
  const { card } = await branchCard(page, qa, JSON.stringify(proposal()) as any)
  await expect(card.getByLabel('Branch name')).toHaveValue('feat/f1-export-csv')
})

// The header chip, from the payload alone: F-1's row in the /features answers is rewritten.
test("the feature header says where the work goes: a branch, the current folder, or 'branch not confirmed'", async ({ page, qa }) => {
  let branch: any
  await page.route(/\/api\/p\/demo\/features(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: list.map((f: any) => (f.key === 'F-1' ? { ...f, branch } : f)) })
  })
  const chip = page.locator('main [data-branch-chip]')
  const cases: [string, any, string | null, string, string][] = [
    ['a new branch', { state: 'approved', gated: true, mode: 'new', branch: 'feat/f1-export-csv', base: 'origin/main', tree: TREE }, 'new', 'feat/f1-export-csv', '~/AITeams-worktrees/AITeams/f1 · from origin/main'],
    ['a shared worktree', { state: 'approved', gated: true, mode: 'shared', branch: 'feat/f37-continue', tree: '/Users/someone/AITeams-worktrees/AITeams/f37', shared_from: 37 }, 'shared', 'feat/f37-continue', 'shared worktree'],
    ['the current folder', { state: 'approved', gated: true, mode: 'current', branch: 'main', tree: null }, 'current', 'current folder (main)', 'Works in the main folder'],
    ['a card waiting for the Master', { state: 'pending', gated: true, approval_id: 5 }, 'unconfirmed', 'branch not confirmed', 'Branch approval #5 is waiting for you'],
    ['a gated feature with no card yet', { state: 'none', gated: true, approval_id: null }, 'unconfirmed', 'branch not confirmed', "No 'Branch:' card requested yet"],
    ['a rejected card', { state: 'rejected', gated: true, approval_id: 5 }, 'unconfirmed', 'branch not confirmed', 'The Branch card was rejected'],
    ['a feature that predates the gate', { state: 'none', gated: false, approval_id: null }, null, '', ''],
  ]
  for (const [label, b, form, text, tip] of cases) {
    branch = b
    await qa.open('/p/demo?c=feature-1')
    if (form === null) {
      await expect(page.locator('main').getByText('F-1', { exact: true }).first()).toBeVisible()
      await expect(chip, label).toHaveCount(0)
      continue
    }
    await expect(chip, label).toHaveText(text)
    await expect(chip, label).toHaveAttribute('data-branch-chip', form)
    if (form === 'unconfirmed') await expect(chip).toHaveClass(/amber/)
    await chip.hover()
    await expect(page.getByRole('tooltip').first(), label).toContainText(tip)
    await page.mouse.move(0, 0)
    if (form === 'new') await page.screenshot({ path: `${SHOTS}/branch-header-chip.png`, animations: 'disabled' })
  }
})

test('no chip on a seeded feature: F-1 predates the gate', async ({ page, qa }) => {
  await qa.open('/p/demo?c=feature-1')
  await expect(page.locator('main').getByText('F-1', { exact: true }).first()).toBeVisible()
  await expect(page.locator('main [data-branch-chip]')).toHaveCount(0)
})

// A refused approval keeps the card open and says why (the daemon's 409 `detail`: git's own message), not the raw request line.
test('a refused Approve keeps the card open and shows the reason', async ({ page, qa }) => {
  const { id, card } = await branchCard(page, qa, proposal())
  await page.route(new RegExp(`/api/p/demo/approvals/${id}/decide$`), (route) =>
    route.fulfill({ status: 409, json: { detail: `git worktree add failed: '${TREE}' already exists and is not empty` } }),
  )
  await card.locator('[data-action="approve"]').click()
  await expect(card).toContainText('already exists and is not empty')
  await expect(card).not.toContainText('-> 409')
  await expect(card.locator('[data-action="approve"]')).toBeEnabled() // still pending: fix it and retry
  await expect(card.getByLabel('Branch name')).toBeVisible()
})

// Part 2: the real daemon. `branch_card {feature, role, mode, branch, base, shared_from}` files a Branch card through the real
// propose_branch; Approve really creates the worktree in the sandbox's temp dir (the base `HEAD` always resolves there).
const AUTH = { Authorization: 'Bearer qa-sandbox-token' }
const tilde = (p: string) => p.replace(/^\/(?:Users|home)\/[^/]+/, '~') // the card shows the home directory as ~

test('a fired Branch card: Approve creates the worktree, and the card and the header say where the work goes', async ({ page, qa }) => {
  const { approval: id, payload } = (await qa.fire('branch_card', { feature: 'F-1', mode: 'new', branch: 'feat/f1-csv', base: 'HEAD' })) as any
  expect(payload).toMatchObject({ mode: 'new', branch: 'feat/f1-csv', base: 'HEAD' })
  expect(payload.tree).toMatch(/AITeams-worktrees\/[^/]+\/f1$/)
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${id}"]`)
  const chip = page.locator('main [data-branch-chip]')
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByLabel('Branch name')).toHaveValue('feat/f1-csv')
  await expect(lands(card)).toHaveText(`Commits land on feat/f1-csv in ${tilde(payload.tree)}`)
  await expect(chip).toHaveAttribute('data-branch-chip', 'unconfirmed') // a card is pending: the feature is gated, not yet confirmed
  await page.screenshot({ path: `${SHOTS}/branch-card-pending.png`, animations: 'disabled' })

  await card.locator('[data-action="approve"]').click()
  await expect(card.locator('[data-action]')).toHaveCount(0)
  await expect(card.locator('[data-branch-card]')).toContainText('New branch in its own worktree from HEAD')
  await expect(lands(card)).toHaveText(`Commits land on feat/f1-csv in ${tilde(payload.tree)}`) // the approved choice, written back by the daemon
  await expect(chip).toHaveAttribute('data-branch-chip', 'new') // the feature frame, live
  await expect(chip).toHaveText('feat/f1-csv')
  expect(existsSync(payload.tree)).toBe(true) // a real git worktree
  const f1 = ((await qa.api<any[]>('/features')) as any[]).find((f) => f.key === 'F-1')
  expect(f1.branch).toMatchObject({ state: 'approved', gated: true, mode: 'new', branch: 'feat/f1-csv', base: 'HEAD', tree: payload.tree })
  await page.screenshot({ path: `${SHOTS}/branch-card-approved.png`, animations: 'disabled' })
})

test('the real checks: a shared-looking name warns, and a branch that already exists blocks Approve', async ({ page, qa }) => {
  const { approval: id } = (await qa.fire('branch_card', { feature: 'F-1', mode: 'new', branch: 'feat/f1-csv', base: 'HEAD' })) as any
  const { current } = await qa.api<{ current: string }>('/branches')
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  const name = card.getByLabel('Branch name')
  const approve = card.locator('[data-action="approve"]')

  await name.fill('release/1.2')
  await expect(card.locator('[data-branch-warning]')).toContainText('release/1.2')
  await expect(card.locator('[data-branch-warning]')).toContainText('shared branch')
  await expect(approve).toBeEnabled() // a warning never refuses
  await page.screenshot({ path: `${SHOTS}/branch-card-warning-real.png`, animations: 'disabled' })

  await name.fill(current)
  await expect(card.locator('[data-branch-error]')).toContainText('already exists')
  await expect(approve).toBeDisabled()
  await name.fill('feat/f1-free')
  await expect(card.locator('[data-branch-error]')).toHaveCount(0)
  await expect(card.locator('[data-branch-warning]')).toHaveCount(0)
  await expect(approve).toBeEnabled()
})

test('the current branch: the card names the checked-out branch, and once approved the header reads "current folder"', async ({ page, qa }) => {
  const { approval: id } = (await qa.fire('branch_card', { feature: 'F-1', mode: 'current' })) as any
  const { current } = await qa.api<{ current: string }>('/branches')
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('radio', { name: 'Current branch' })).toBeChecked()
  await expect(lands(card)).toHaveText(`Commits land on ${current} in the main folder`)
  await expect(card.locator('[data-branch-warning]')).toHaveCount(SHARED.test(current) ? 1 : 0) // main/master/dev... is shared-looking

  await card.locator('[data-action="approve"]').click()
  await expect(card.locator('[data-action]')).toHaveCount(0)
  await expect(card.locator('[data-branch-card]')).toContainText('Current branch')
  await expect(lands(card)).toHaveText(`Commits land on ${current} in the main folder`)
  await expect(page.locator('main [data-branch-chip]')).toHaveText(`current folder (${current})`)
  await expect(page.locator('main [data-branch-chip]')).toHaveAttribute('data-branch-chip', 'current')
})

test("a feature created after the gate, with no Branch card yet, reads 'branch not confirmed'", async ({ page, qa, request }) => {
  const r = await request.post('/api/p/demo/features', { headers: AUTH, data: { text: 'Export the audit log', starter: 'product' } })
  expect(r.ok(), await r.text()).toBe(true)
  const f = await r.json()
  expect(f.branch).toMatchObject({ state: 'none', gated: true })
  await qa.open(`/p/demo?c=${f.conversation_id}`)
  const chip = page.locator('main [data-branch-chip]')
  await expect(chip).toHaveText('branch not confirmed')
  await expect(chip).toHaveClass(/amber/)
  await chip.hover()
  await expect(page.getByRole('tooltip').first()).toContainText("No 'Branch:' card requested yet")
})

test('another feature can share an existing worktree: the card lists it, and the header says it is shared', async ({ page, qa, request }) => {
  // F-4 (Lead 2) gets the worktree. No other test approves a new branch on F-4: a tree outlives /qa/reset (it sits beside the
  // sandbox repo, not in it), so two tests making one feature's tree would collide.
  const first = (await qa.fire('branch_card', { feature: 'F-4', role: 'lead-2', mode: 'new', branch: 'feat/f4-schedule', base: 'HEAD' })) as any
  const ok = await request.post(`/api/p/demo/approvals/${first.approval}/decide`, { headers: AUTH, data: { decision: 'approve' } })
  expect(ok.ok(), await ok.text()).toBe(true)
  const { approval: id, payload } = (await qa.fire('branch_card', { feature: 'F-1', mode: 'shared', shared_from: 'F-4' })) as any
  expect(payload.shared_from).toBe('F-4')
  await qa.open('/p/demo?c=feature-1')
  const card = page.locator(`[data-card="approval-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('radio', { name: 'Existing worktree' })).toBeChecked()
  await expect(card.getByRole('combobox', { name: 'Worktree' })).toHaveText('F-4 · feat/f4-schedule')
  await expect(lands(card)).toHaveText(`Commits land on feat/f4-schedule in ${tilde(first.payload.tree)}`)

  await card.locator('[data-action="approve"]').click()
  await expect(card.locator('[data-action]')).toHaveCount(0)
  await expect(card.locator('[data-branch-card]')).toContainText('Existing worktree')
  const chip = page.locator('main [data-branch-chip]')
  await expect(chip).toHaveAttribute('data-branch-chip', 'shared')
  await expect(chip).toHaveText('feat/f4-schedule')
  await chip.hover()
  await expect(page.getByRole('tooltip').first()).toContainText('shared worktree')
})
