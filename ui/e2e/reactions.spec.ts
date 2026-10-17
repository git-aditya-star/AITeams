import { expect, test } from './fixtures'

const SEEDED = 'F-1 (CSV export) is in build' // the seeded manager message in #general

// F-6: roles react through MCP `react`; the Master sees read-only chips. The sandbox's `reaction` kind toggles
// one role's emoji on a message through the same db + hub calls as POST /internal/reactions.
test('reaction chips show live with roles on hover, survive a reload, and have no add control', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  await expect(page.getByText(SEEDED)).toBeVisible() // the page is loaded, so live frames land on it
  const posted = await qa.fire('message', { conversation: 'general', role: 'lead', text: 'Reaction target' })
  const id = posted.message as number
  const row = page.locator(`#m-${id}`)
  await expect(row).toContainText('Reaction target')
  await expect(row.locator('[data-reactions]')).toHaveCount(0)

  await qa.fire('reaction', { message_id: id, emoji: '👍', role: 'qa' })
  await qa.fire('reaction', { message_id: id, emoji: '👍', role: 'backend' })
  await qa.fire('reaction', { message_id: id, emoji: '🎉', role: 'frontend' })
  const thumbs = row.locator('[data-reaction="👍"]')
  await expect(thumbs).toHaveText('👍2')
  await expect(row.locator('[data-reaction="🎉"]')).toHaveText('🎉1')

  await thumbs.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Qa, Backend')

  // Read-only: the chips are not buttons and the row offers nothing to add a reaction.
  await expect(row.locator('[data-reactions] button')).toHaveCount(0)
  await expect(row.getByRole('button', { name: /react/i })).toHaveCount(0)

  await page.reload()
  await expect(page.locator(`#m-${id} [data-reaction="👍"]`)).toHaveText('👍2')
  await page.locator(`#m-${id}`).screenshot({ path: process.env.SHOT_OUT || 'test-results/shots/reactions.png' })

  // Toggling the same emoji again removes that role.
  await qa.fire('reaction', { message_id: id, emoji: '🎉', role: 'frontend' })
  await expect(page.locator(`#m-${id} [data-reaction="🎉"]`)).toHaveCount(0)
})

// M2c race: frames that arrive while the conversation's page is in flight must survive the page landing.
test('a message and a reaction that arrive during the page load are kept', async ({ page, qa }) => {
  await qa.open('/p/demo?c=general')
  await expect(page.getByText(SEEDED)).toBeVisible()
  const a = (await qa.fire('message', { conversation: 'general', role: 'lead', text: 'Older target' })).message as number
  await expect(page.locator(`#m-${a}`)).toBeVisible()

  // Hold the next #general page after the server has read it, so it predates the frames below.
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  let held!: () => void
  const fetched = new Promise<void>((r) => (held = r))
  await page.route(/\/api\/p\/demo\/messages\?.*conversation=general/, async (route) => {
    const resp = await route.fetch()
    held()
    await gate
    await route.fulfill({ response: resp })
  })
  await page.locator('aside').getByRole('button', { name: '# standup' }).click()
  await page.locator('aside').getByRole('button', { name: '# general' }).click()
  await fetched

  await qa.fire('reaction', { message_id: a, emoji: '👍', role: 'qa' })
  const b = (await qa.fire('message', { conversation: 'general', role: 'backend', text: 'Arrived mid-load' })).message as number
  await expect(page.locator(`#m-${b}`)).toBeVisible() // live frame applied while the page is held
  release()

  await expect(page.getByText(SEEDED)).toBeVisible() // the page has landed
  await expect(page.locator(`#m-${b}`)).toContainText('Arrived mid-load')
  await expect(page.locator(`#m-${a} [data-reaction="👍"]`)).toHaveText('👍1')
})
