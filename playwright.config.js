// @ts-check
/** @type {import('@playwright/test').PlaywrightTestConfig} */
module.exports = {
  testDir: './e2e',
  timeout: 120000,
  retries: 1,
  use: {
    baseURL: process.env.FRONTEND_URL || 'https://frontend-manojsilwals-projects.vercel.app',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium' }],
};
