import type { Page } from '@playwright/test'
import type { Question } from '../src/api'
import { expect, test, TOKEN } from './fixtures'

// F-10: non-blocking question cards (ask_master). The sandbox `card` kind posts the default 2-question interview
// card (Format: single; Columns: multi-select) into F-1's conversation through the real ask_master path.

// Fire a card and return its id plus the route of the conversation it was posted in.
const fireCard = async (qa: { fire: (k: string, b?: Record<string, unknown>) => Promise<any> }, body: Record<string, unknown>) => {
  const f = await qa.fire('card', body)
  return { id: f.question as number, conv: `/p/demo?c=${f.conversation}` }
}

const card = (page: Page, id: number, scope = page.locator('main')) => scope.locator(`[data-question-card="${id}"]`)

test('a card shows in its conversation and the Inbox; answering by option and multi-select closes it in both', async ({ page, qa }) => {
  const { id, conv } = await fireCard(qa, { role: 'product' })
  await qa.open(conv)

  const inConv = card(page, id)
  await expect(inConv).toHaveAttribute('data-state', 'pending')
  await expect(inConv.locator('[data-card-purpose]')).toHaveText('Interview')
  await expect(inConv).toContainText('My take: CSV first, XLSX later.')
  await page.screenshot({ path: process.env.SHOT_OUT || 'test-results/shots/question-card.png' })

  await inConv.getByRole('button', { name: /^CSV \(Recommended\)/ }).click()
  await inConv.getByRole('button', { name: /^Date/ }).click()
  await inConv.getByRole('button', { name: /^Customer/ }).click()
  await inConv.getByRole('button', { name: 'Submit' }).click()

  // Closed in place, each question → answer, and the Master's Decision spells the answers out for Product.
  await expect(inConv).toHaveAttribute('data-state', 'answered')
  await expect(inConv).toContainText('CSV (Recommended)')
  await expect(inConv).toContainText('Date, Customer')
  await expect(page.locator('main')).toContainText(`Answers to card #${id}`)

  await page.goto('/p/demo?c=inbox')
  await expect(card(page, id)).toHaveCount(0)
})

test('Other… answers with free text', async ({ page, qa }) => {
  const { id, conv } = await fireCard(qa, { role: 'lead', purpose: 'concern' })
  await qa.open(conv)
  const c = card(page, id)
  await expect(c.locator('[data-card-purpose]')).toHaveText('Concern')

  const others = c.getByPlaceholder('Other…')
  await others.nth(0).fill('Parquet, for the data team')
  await others.nth(1).fill('Only the totals')
  await c.getByRole('button', { name: 'Submit' }).click()
  await expect(c).toHaveAttribute('data-state', 'answered')
  await expect(c).toContainText('Parquet, for the data team')
  await expect(page.locator('main')).toContainText('(Other) Parquet, for the data team')
})

test('the Inbox lists a pending card once; answering there removes it and the conversation shows it answered', async ({ page, qa }) => {
  const { id, conv } = await fireCard(qa, { role: 'product' })
  await qa.open('/p/demo?c=inbox')
  const inInbox = card(page, id)
  await expect(inInbox).toHaveCount(1)
  // A card is not a blocking question: no top banner for it.
  await expect(page.getByText('Product is asking:')).toHaveCount(0)

  await inInbox.getByRole('button', { name: /^XLSX/ }).click()
  await inInbox.getByRole('button', { name: /^Amount/ }).click()
  await inInbox.getByRole('button', { name: 'Submit' }).click()
  await expect(inInbox).toHaveCount(0)

  await page.goto(conv)
  await expect(card(page, id)).toHaveAttribute('data-state', 'answered')
})

test('a withdrawn card shows closed', async ({ page, qa }) => {
  const { id, conv } = await fireCard(qa, { role: 'product' })
  await qa.open(conv)
  await expect(card(page, id)).toHaveAttribute('data-state', 'pending')
  await qa.fire('card_withdraw', { question: id })
  await expect(card(page, id)).toHaveAttribute('data-state', 'withdrawn')
  await expect(card(page, id)).toContainText('Withdrawn by Product')
})

test('an answer from elsewhere closes the card live in the open conversation and in the open Inbox', async ({ page, qa, request }) => {
  // Two cards: answer one while its conversation is on screen, the other while the Inbox is on screen.
  const a = await fireCard(qa, { role: 'product', purpose: 'ask' })
  const b = await fireCard(qa, { role: 'product' })
  // Answer the way the Master's click does, but from outside this page, with the first option of every question.
  const answer = async (id: number) => {
    const q = (await qa.api<Question[]>('/questions?state=pending')).find((x) => x.id === id)!
    const answers = Object.fromEntries(q.payload.questions.map((it) => [it.question, it.options[0].label]))
    return request.post(`/api/p/demo/questions/${id}/answer`, { headers: { Authorization: `Bearer ${TOKEN}` }, data: { answers } })
  }

  await qa.open(a.conv)
  await expect(card(page, a.id)).toHaveAttribute('data-state', 'pending')
  await expect(card(page, a.id).locator('[data-card-purpose]')).toHaveCount(0) // plain asks get no badge
  expect((await answer(a.id)).ok()).toBe(true)
  await expect(card(page, a.id)).toHaveAttribute('data-state', 'answered')

  await page.locator('aside').getByText('Inbox', { exact: true }).click() // client-side, no reload
  await expect(card(page, b.id)).toHaveCount(1)
  expect((await answer(b.id)).ok()).toBe(true)
  await expect(card(page, b.id)).toHaveCount(0)
})
