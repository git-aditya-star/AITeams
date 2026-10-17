import { defineConfig, devices } from '@playwright/test'
import { role, rolePort } from './e2e/ports.mjs'

// One sandbox daemon (scripts/qa_sandbox.py) per run, serving the working-tree build; `npm run e2e` builds first.
// F-11: a role runs on its own port with its own output dirs (e2e/ports.mjs); `--output` and AITEAMS_QA_PORT override.
const port = rolePort(8790)
const who = role()

export default defineConfig({
  testDir: './e2e',
  // Tests share the sandbox's state; the qa fixture re-seeds it before each test, so run them one at a time.
  workers: 1,
  outputDir: who ? `test-results/${who}` : 'test-results',
  reporter: [['list'], ['html', { open: 'never', outputFolder: who ? `playwright-report/${who}` : 'playwright-report' }]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1400, height: 900 } } }],
  webServer: {
    command: `uv run --no-sync python -B scripts/qa_sandbox.py --port ${port}`,
    cwd: '..',
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    // SIGTERM, not Playwright's default kill, so the sandbox's `finally` removes its temp dir.
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 },
    timeout: 60_000,
  },
})
