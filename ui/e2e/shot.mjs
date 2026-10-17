// Screenshot app routes against a fresh QA sandbox (scripts/qa_sandbox.py, seeded project id `demo`).
//   npm run shot -- <route> [<route>...] [--out <png>] [--width 1400] [--height 900] [--port 8798]
// Serves the working-tree build: run `npm run build` first. PNGs default to test-results/shots/<slug>.png.
import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const UI = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const ROOT = resolve(UI, '..')
const TOKEN = 'qa-sandbox-token'

const opts = { routes: [], out: null, width: 1400, height: 900, port: Number(process.env.AITEAMS_QA_PORT) || 8798 }
const argv = process.argv.slice(2)
for (let i = 0; i < argv.length; i++) {
  const a = argv[i]
  if (a === '--out') opts.out = argv[++i]
  else if (a === '--width') opts.width = Number(argv[++i])
  else if (a === '--height') opts.height = Number(argv[++i])
  else if (a === '--port') opts.port = Number(argv[++i])
  else opts.routes.push(a)
}
if (!opts.routes.length || (opts.out && opts.routes.length > 1)) {
  console.error('usage: npm run shot -- <route> [<route>...] [--out <png> (one route only)] [--width 1400] [--height 900] [--port N]')
  process.exit(2)
}

const slug = (route) => route.replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'home'
const base = `http://127.0.0.1:${opts.port}`

async function waitHealthy(proc) {
  for (let i = 0; i < 200; i++) {
    if (proc.exitCode !== null) throw new Error(`sandbox exited with ${proc.exitCode}`)
    try {
      if ((await fetch(`${base}/api/health`)).ok) return
    } catch {}
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('sandbox did not come up')
}

const sandbox = spawn('uv', ['run', '--no-sync', 'python', '-B', 'scripts/qa_sandbox.py', '--port', String(opts.port), '--token', TOKEN],
  { cwd: ROOT, stdio: ['ignore', 'inherit', 'inherit'] })
let browser
try {
  await waitHealthy(sandbox)
  browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: opts.width, height: opts.height } })
  await ctx.addCookies([{ name: 'aiteams_token', value: TOKEN, url: base }])
  const page = await ctx.newPage()
  for (const route of opts.routes) {
    const out = resolve(opts.out || resolve(UI, 'test-results', 'shots', `${slug(route)}.png`))
    mkdirSync(dirname(out), { recursive: true })
    await page.goto(base + route, { waitUntil: 'networkidle' })
    await page.screenshot({ path: out })
    console.log(`shot ${route} -> ${relative(process.cwd(), out) || out}`)
  }
} finally {
  await browser?.close()
  sandbox.kill('SIGTERM')
}
