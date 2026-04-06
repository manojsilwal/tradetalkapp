// @ts-check
/** @type {import('@playwright/test').PlaywrightTestConfig} */
module.exports = {
  testDir: './e2e',
  /** Backtest + slow API paths can exceed 2m; align with client BACKTEST_POST_TIMEOUT_MS (+ buffer). */
  timeout: 360000,
  retries: 1,
  /** Local Vite + FastAPI struggle with many parallel browsers; use 1 worker unless PW_WORKERS is set. */
  workers: process.env.PW_WORKERS ? parseInt(process.env.PW_WORKERS, 10) : 1,
  fullyParallel: false,
  use: {
    /** Local Vite dev server; override with FRONTEND_URL for prod smoke. */
    baseURL: process.env.FRONTEND_URL || 'http://localhost:5173',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium' }],
};
