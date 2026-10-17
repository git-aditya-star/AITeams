import { defineConfig, devices } from '@playwright/test'

// One sandbox daemon (scripts/qa_sandbox.py) per run, serving the working-tree build; `npm run e2e` builds first.
const port = Number(process.env.AITEAMS_QA_PORT) || 8790

export default defineConfig({
  testDir: './e2e',
  // Tests share the sandbox's state; the qa fixture re-seeds it before each test, so run them one at a time.
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
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
