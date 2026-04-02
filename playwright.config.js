// @ts-check
/** @type {import('@playwright/test').PlaywrightTestConfig} */
module.exports = {
  testDir: './e2e',
  /** Backtest + slow API paths can exceed 2m; align with client BACKTEST_POST_TIMEOUT_MS (+ buffer). */
  timeout: 360000,
  retries: 1,
  use: {
    baseURL: process.env.FRONTEND_URL || 'https://frontend-manojsilwals-projects.vercel.app',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium' }],
};
