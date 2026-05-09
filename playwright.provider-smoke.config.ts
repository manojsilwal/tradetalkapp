/**
 * API-only provider smoke (NVIDIA DeepSeek + Google embeddings).
 * No frontend globalSetup — run against a live FastAPI instance with secrets in env.
 *
 * Example:
 *   ALLOW_PROVIDER_SMOKE=1 uvicorn backend.main:app --host 127.0.0.1 --port 8000
 *   E2E_API_BASE_URL=http://127.0.0.1:8000 npx playwright test --config=playwright.provider-smoke.config.ts
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'provider-smoke.spec.ts',
  fullyParallel: false,
  timeout: 720_000,
  expect: { timeout: 60_000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    trace: 'on-first-retry',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
