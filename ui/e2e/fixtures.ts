import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test'

export const TOKEN = 'qa-sandbox-token' // qa_sandbox.py's default
export const PID = 'demo' // the sandbox's fixed project id
const auth = { Authorization: `Bearer ${TOKEN}` }

export type Fired = { ok: boolean; kind: string; conversation: string | null; [k: string]: unknown }

class Sandbox {
  constructor(
    private request: APIRequestContext,
    private page: Page,
  ) {}

  // Go to a route and wait until the page's WebSocket delivers frames, so a later fire() reaches it.
  async open(route: string) {
    const ws = this.page.waitForEvent('websocket', (w) => w.url().includes(`/ws/p/${PID}`))
    await this.page.goto(route)
    const socket = await ws
    await expect(async () => {
      const frame = socket.waitForEvent('framereceived', { timeout: 1000 })
      await this.fire('message', { conversation: 'general', role: 'manager', text: 'ws probe' })
      await frame
    }).toPass({ timeout: 15_000 })
  }

  async reset() {
    expect((await this.request.post('/qa/reset', { headers: auth })).ok()).toBe(true)
  }

  async fire(kind: string, body: Record<string, unknown> = {}): Promise<Fired> {
    const r = await this.request.post('/qa/fire', { headers: auth, data: { kind, ...body } })
    expect(r.ok(), await r.text()).toBe(true)
    return r.json()
  }

  async api<T = any>(path: string): Promise<T> {
    return (await this.request.get(`/api/p/${PID}${path}`, { headers: auth })).json()
  }
}

// Every test starts signed in (the sandbox token as the session cookie) on a freshly seeded sandbox.
export const test = base.extend<{ qa: Sandbox }>({
  qa: [
    async ({ request, context, page, baseURL }, use) => {
      const qa = new Sandbox(request, page)
      await qa.reset()
      await context.addCookies([{ name: 'aiteams_token', value: TOKEN, url: baseURL! }])
      await use(qa)
    },
    { auto: true },
  ],
})

export { expect }
