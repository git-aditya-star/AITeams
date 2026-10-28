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

// ---- F-39 part 3: the PR card and the header's PR chip --------------------------------------------------------------------
// A `PR:` approval carries the Lead's pull request in `payload` ({source, destination, title, body, commits}). The Master edits the
// destination, title and description; Approve sends the edited three and the daemon pushes the branch and runs `gh pr create`. As in
// part 1 the first cases pin the UI with page.route (the rows, /branches/pr-preview and the decide POST are answered here); the last
// two run the real daemon through the sandbox's `pr_card`.

const PR_URL = 'https://github.com/acme/demo/pull/57'
const COMMITS = [
  { sha: 'a1b2c3d', subject: 'feat: export button' },
  { sha: 'b2c3d4e', subject: 'feat: csv writer' },
  { sha: 'c3d4e5f', subject: 'test: csv export' },
]
const prPayload = (over: Record<string, unknown> = {}) => ({
  source: 'feat/f1-export-csv',
  destination: 'main',
  title: 'Export orders to CSV',
  body: 'Adds the CSV export behind the new button.\n\nTested in the sandbox.',
  commits: COMMITS,
  ...over,
})
const NOTHING = 'feat/f1-export-csv has no commits that develop lacks, so there is nothing to open a pull request for'

type PRCalls = { previews: URLSearchParams[]; decides: Record<string, any>[] }

// A pending `PR:` card on F-1 whose approval row carries `payload`. /branches/pr-preview answers like the daemon: three commits for
// every destination but `develop`, which has nothing to propose (an `errors` answer).
async function prCard(page: Page, qa: { fire: (k: string, b?: Record<string, unknown>) => Promise<any>; open: (route: string) => Promise<void> }, payload: unknown, state = 'pending') {
  const id = (await qa.fire('approval', { text: 'PR: Export orders to CSV', role: 'lead' })).approval as number
  const calls: PRCalls = { previews: [], decides: [] }
  await page.route(/\/api\/p\/demo\/messages\?.*conversation=feature-1/, async (route) => {
    const res = await route.fetch()
    const list = await res.json()
    for (const m of list) if (m.approval?.id === id) m.approval = { ...m.approval, payload, state }
    await route.fulfill({ response: res, json: list })
  })
  await page.route(/\/api\/p\/demo\/branches\/pr-preview\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    calls.previews.push(q)
    const dest = q.get('destination') || 'main'
    const empty = dest === 'develop'
    return route.fulfill({
      json: {
        source: 'feat/f1-export-csv',
        destination: dest,
        branches: [{ name: 'main', remote: true }, { name: 'develop', remote: true }, { name: 'release/1.2', remote: true }],
        commits: empty ? [] : COMMITS,
        errors: empty ? [NOTHING] : [],
        pr_url: null,
        pr_state: null,
      },
    })
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

const destination = (card: ReturnType<Page['locator']>) => card.getByRole('combobox', { name: 'Destination' })

test('a PR card shows the branch going to a destination, the title, the description, the commits and what AITeams will do', async ({ page, qa }) => {
  const { card, calls } = await prCard(page, qa, prPayload())
  await expect(card.locator('[data-pr-source]')).toHaveText('feat/f1-export-csv')
  await expect(destination(card)).toHaveText('main')
  await expect(card.getByLabel('Pull request title')).toHaveValue('Export orders to CSV')
  await expect(card.getByLabel('Pull request description')).toHaveValue('Adds the CSV export behind the new button.\n\nTested in the sandbox.')
  await expect(card.locator('[data-pr-commits]')).toContainText('3 commits')
  await expect(card.locator('[data-pr-commits] li')).toHaveText(['a1b2c3dfeat: export button', 'b2c3d4efeat: csv writer', 'c3d4e5ftest: csv export'])
  await expect(card.locator('[data-pr-note]')).toHaveText('AITeams pushes feat/f1-export-csv to origin and opens the pull request on GitHub. You merge it there.')
  await expect(card.locator('[data-pr-error]')).toHaveCount(0)
  await expect(card.locator('[data-action="approve"]')).toBeEnabled()
  // The card asks the daemon for fresh commits when it opens, for this feature and its destination.
  await expect.poll(() => calls.previews.length).toBeGreaterThan(0)
  expect([calls.previews[0].get('feature'), calls.previews[0].get('destination')]).toEqual(['1', 'main'])
  await page.screenshot({ path: `${SHOTS}/pr-card-pending.png`, animations: 'disabled' })
})

test('the destination list is what pr-preview returned; a destination with nothing to propose blocks Approve and picking another clears it', async ({ page, qa }) => {
  const { card, calls } = await prCard(page, qa, prPayload())
  const approve = card.locator('[data-action="approve"]')
  await destination(card).click()
  for (const b of ['main', 'develop', 'release/1.2']) await expect(page.getByRole('option', { name: b, exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: 'feat/f1-export-csv' })).toHaveCount(0) // never the branch itself
  await page.getByRole('option', { name: 'develop', exact: true }).click()
  await expect(card.locator('[data-pr-error]')).toHaveText(NOTHING)
  await expect(card.locator('[data-pr-commits]')).toHaveCount(0)
  await expect(approve).toBeDisabled()
  expect(calls.previews.at(-1)!.get('destination')).toBe('develop')
  await page.screenshot({ path: `${SHOTS}/pr-card-nothing.png`, animations: 'disabled' })

  await destination(card).click()
  await page.getByRole('option', { name: 'release/1.2', exact: true }).click()
  await expect(card.locator('[data-pr-error]')).toHaveCount(0)
  await expect(card.locator('[data-pr-commits] li')).toHaveCount(3) // the commits follow the destination
  await expect(approve).toBeEnabled()

  await card.getByLabel('Pull request title').fill('  ') // a pull request needs a title
  await expect(approve).toBeDisabled()
})

test('Approve sends the edited destination, title and description; Reject and Request changes send none', async ({ page, qa }) => {
  const a = await prCard(page, qa, prPayload())
  await a.card.getByLabel('Pull request title').fill('Export orders as CSV')
  await a.card.getByLabel('Pull request description').fill('Edited by the Master.')
  await destination(a.card).click()
  await page.getByRole('option', { name: 'release/1.2', exact: true }).click()
  await a.card.locator('[data-action="approve"]').click()
  await expect.poll(() => a.calls.decides.length).toBe(1)
  expect(a.calls.decides[0]).toEqual({ decision: 'approve', payload: { destination: 'release/1.2', title: 'Export orders as CSV', body: 'Edited by the Master.' } })
  await expect(a.card.locator('[data-action]')).toHaveCount(0) // decided: nothing left to edit
  await expect(a.card.getByLabel('Pull request title')).toHaveCount(0)

  const b = await prCard(page, qa, prPayload())
  await b.card.getByLabel('Pull request title').fill('Something else')
  await b.card.locator('[data-action="changes"]').click()
  await expect.poll(() => b.calls.decides.length).toBe(1)
  expect(b.calls.decides[0]).toEqual({ decision: 'changes' })
  const c = await prCard(page, qa, prPayload())
  await c.card.locator('[data-action="reject"]').click()
  await expect.poll(() => c.calls.decides.length).toBe(1)
  expect(c.calls.decides[0]).toEqual({ decision: 'reject' })
})

// A failed push or `gh` call is a 409 with the sentence in `detail`; the card stays pending so the Master can fix it and retry.
test('a refused Approve keeps the PR card open and shows the reason', async ({ page, qa }) => {
  const { id, card } = await prCard(page, qa, prPayload())
  await page.route(new RegExp(`/api/p/demo/approvals/${id}/decide$`), (route) =>
    route.fulfill({ status: 409, json: { detail: "`gh` isn't logged in; run `gh auth login` and retry" } }),
  )
  await card.locator('[data-action="approve"]').click()
  await expect(card).toContainText("`gh` isn't logged in; run `gh auth login` and retry")
  await expect(card).not.toContainText('-> 409')
  await expect(card.locator('[data-action="approve"]')).toBeEnabled()
  await expect(card.getByLabel('Pull request title')).toHaveValue('Export orders to CSV') // the edits are still there
})

test('a decided PR card names the branches and links to the pull request; a payload sent as text still renders', async ({ page, qa }) => {
  const { card } = await prCard(page, qa, prPayload({ pr_url: PR_URL, pr_state: 'open' }), 'approved')
  const body = card.locator('[data-pr-card]')
  await expect(body).toHaveAttribute('data-pr-decided', 'true')
  await expect(body).toContainText('feat/f1-export-csv')
  await expect(body).toContainText('main')
  await expect(body).toContainText('Export orders to CSV')
  const link = card.locator('[data-pr-link]')
  await expect(link).toHaveText('Pull request #57')
  await expect(link).toHaveAttribute('href', PR_URL)
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  await expect(card.getByLabel('Pull request title')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/pr-card-decided.png`, animations: 'disabled' })

  const t = await prCard(page, qa, JSON.stringify(prPayload()) as any)
  await expect(t.card.getByLabel('Pull request title')).toHaveValue('Export orders to CSV')
  const rejected = await prCard(page, qa, prPayload(), 'rejected')
  await expect(rejected.card.locator('[data-pr-link]')).toHaveCount(0) // no pull request was opened
})

// The header chip, from the payload alone: F-1's row in the /features answers is rewritten.
const OPEN = { state: 'approved', gated: true, mode: 'new', branch: 'feat/f1-export-csv', base: 'origin/main', tree: TREE, pr_url: PR_URL, pr_state: 'open', pr_destination: 'main', tree_state: 'live' }
async function rewriteF1Branch(page: Page, get: () => any) {
  await page.route(/\/api\/p\/demo\/features(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const r = await route.fetch()
    const list = await r.json()
    await route.fulfill({ response: r, json: list.map((f: any) => (f.key === 'F-1' ? { ...f, branch: get() } : f)) })
  })
}

test('the header chip links to the pull request with its state, and says what became of the worktree', async ({ page, qa }) => {
  let branch: any
  await rewriteF1Branch(page, () => branch)
  const chip = page.locator('main [data-pr-chip]')
  const cases: [string, any, string | null, string, string][] = [
    ['open', OPEN, 'open', 'PR #57 · open', 'feat/f1-export-csv → main'],
    ['merged, worktree removed', { ...OPEN, pr_state: 'merged', tree_state: 'removed' }, 'merged', 'PR #57 · merged', 'worktree removed'],
    ['merged, worktree kept', { ...OPEN, pr_state: 'merged', tree_state: 'kept' }, 'merged', 'PR #57 · merged', "worktree kept: it has changes git won't delete"],
    ['closed', { ...OPEN, pr_state: 'closed' }, 'closed', 'PR #57 · closed', 'feat/f1-export-csv → main'],
    ['no pull request yet', { ...OPEN, pr_url: null, pr_state: null, pr_destination: null }, null, '', ''],
  ]
  for (const [label, b, state, text, tip] of cases) {
    branch = b
    await qa.open('/p/demo?c=feature-1')
    await expect(page.locator('main [data-branch-chip]'), label).toHaveText('feat/f1-export-csv') // the branch chip is still there
    if (state === null) {
      await expect(chip, label).toHaveCount(0)
      await expect(page.locator('main [data-pr-refresh]'), label).toHaveCount(0)
      continue
    }
    await expect(chip, label).toHaveText(text)
    await expect(chip, label).toHaveAttribute('data-pr-chip', state)
    await expect(chip, label).toHaveAttribute('href', PR_URL)
    await expect(chip, label).toHaveAttribute('target', '_blank')
    await expect(page.locator('main [data-pr-refresh]'), label).toHaveCount(state === 'open' ? 1 : 0) // only an open one can change
    await chip.hover()
    await expect(page.getByRole('tooltip').first(), label).toContainText(tip)
    await page.mouse.move(0, 0)
    if (state === 'open') await page.screenshot({ path: `${SHOTS}/pr-header-chip.png`, animations: 'disabled' })
  }
})

test('the refresh button asks the daemon and the chip follows its answer; a refused refresh says why', async ({ page, qa }) => {
  let branch: any = OPEN
  let refuse = true
  const calls: string[] = []
  await rewriteF1Branch(page, () => branch)
  const f1 = ((await qa.api<any[]>('/features')) as any[]).find((f) => f.key === 'F-1')
  await page.route(new RegExp(`/api/p/demo/features/${f1.id}/pr/refresh$`), async (route) => {
    calls.push(route.request().method())
    if (refuse) return route.fulfill({ status: 409, json: { detail: "`gh` isn't logged in; run `gh auth login` and retry" } })
    branch = { ...OPEN, pr_state: 'merged', tree_state: 'removed' }
    await route.fulfill({ json: { ...f1, branch } })
  })
  await qa.open('/p/demo?c=feature-1')
  const chip = page.locator('main [data-pr-chip]')
  const refresh = page.locator('main [data-pr-refresh]')
  await expect(chip).toHaveText('PR #57 · open')

  await refresh.click()
  await expect(page.getByText("`gh` isn't logged in; run `gh auth login` and retry")).toBeVisible()
  await expect(chip).toHaveText('PR #57 · open') // nothing changed
  await expect(refresh).toBeEnabled()

  refuse = false
  await refresh.click()
  await expect(chip).toHaveText('PR #57 · merged')
  await expect(refresh).toHaveCount(0)
  expect(calls).toEqual(['POST', 'POST'])
})

// Part 2: the real daemon. `pr_card {feature, role, destination, title, body, commits}` files a PR card through the real request_pr: a
// feature with no confirmed branch first gets a real worktree with commits on it. The sandbox repo has no origin, so Approve fails
// the way it does for a Master who has not added one. `pr_card {feature, pr_state, pr_url}` files no card: the header shows that PR.
test('a fired PR card lists the real commits, and Approve is refused with the daemon\'s own sentence', async ({ page, qa }) => {
  const { approval: id, payload } = (await qa.fire('pr_card', { feature: 'F-2', commits: 2 })) as any
  expect(payload.commits).toHaveLength(2)
  expect(payload.source).not.toBe(payload.destination)
  await qa.open('/p/demo?c=feature-2')
  const card = page.locator(`[data-card="approval-${id}"]`)
  await card.scrollIntoViewIfNeeded()
  await expect(card.locator('[data-pr-source]')).toHaveText(payload.source)
  await expect(destination(card)).toHaveText(payload.destination)
  await expect(card.getByLabel('Pull request title')).toHaveValue(payload.title)
  await expect(card.locator('[data-pr-commits] li')).toHaveCount(2) // from the real pr-preview
  await expect(card.locator('[data-pr-commits]')).toContainText('docs: note 1 for F-2')
  await expect(card.locator('[data-pr-error]')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/pr-card-real.png`, animations: 'disabled' })

  await card.getByLabel('Pull request title').fill('Export orders as CSV')
  await card.locator('[data-action="approve"]').click()
  await expect(card).toContainText('this repo has no `origin` remote')
  await expect(card.locator('[data-action="approve"]')).toBeEnabled() // still pending: add an origin and retry
  await expect(card.getByLabel('Pull request title')).toHaveValue('Export orders as CSV')
})

test('a fired pull request shows in the header, and a merge arrives live', async ({ page, qa }) => {
  await qa.fire('pr_card', { feature: 'F-3', pr_state: 'open', pr_url: 'https://github.com/acme/demo/pull/7' })
  await qa.open('/p/demo?c=feature-3')
  const chip = page.locator('main [data-pr-chip]')
  await expect(chip).toHaveText('PR #7 · open')
  await expect(chip).toHaveAttribute('href', 'https://github.com/acme/demo/pull/7')
  await expect(page.locator('main [data-pr-refresh]')).toHaveCount(1)

  await qa.fire('pr_card', { feature: 'F-3', pr_state: 'merged', pr_url: 'https://github.com/acme/demo/pull/7' })
  await expect(chip).toHaveText('PR #7 · merged') // the feature frame, live
  await expect(page.locator('main [data-pr-refresh]')).toHaveCount(0)
})
