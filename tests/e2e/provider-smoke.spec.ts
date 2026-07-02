import { expect, test } from '@playwright/test';

/**
 * Live probes against `/health/smoke/*` (requires backend env ALLOW_PROVIDER_SMOKE=1).
 *
 * Backend must have:
 * - OPENROUTER_API_KEY for chat probe
 * - GEMINI_API_KEY or GOOGLE_API_KEY for embedding probe
 *
 * Optional: PROVIDER_SMOKE_SECRET on API + E2E_PROVIDER_SMOKE_SECRET in test env.
 */
const API_BASE = (process.env.E2E_API_BASE_URL || '').replace(/\/$/, '');

function smokeHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const sec = (process.env.E2E_PROVIDER_SMOKE_SECRET || '').trim();
  if (sec) h['X-Provider-Smoke-Secret'] = sec;
  return h;
}

test.describe('Provider smoke API', () => {
  test.beforeAll(() => {
    test.skip(!API_BASE, 'Set E2E_API_BASE_URL (e.g. http://127.0.0.1:8000)');
  });

  test('status endpoint is reachable when smoke routes enabled', async ({ request }) => {
    const res = await request.get(`${API_BASE}/health/smoke/status`, { headers: smokeHeaders() });
    if (res.status() === 404) {
      test.skip(
        true,
        'Smoke routes disabled — set ALLOW_PROVIDER_SMOKE=1 on the API and restart.',
      );
    }
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.allow_provider_smoke).toBe(true);
    expect(body).toHaveProperty('llm_http_provider');
    expect(body).toHaveProperty('embedding_model_resolved');
  });

  test('OpenRouter chat completion', async ({ request }) => {
    const res = await request.post(`${API_BASE}/health/smoke/openrouter/chat`, {
      headers: smokeHeaders(),
      data: JSON.stringify({ prompt: 'Say exactly: OK' }),
    });
    if (res.status() === 404) {
      test.skip(true, 'ALLOW_PROVIDER_SMOKE=1 not enabled on API');
    }
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    test.skip(!!body.skipped, body.reason || 'OpenRouter probe skipped');
    expect(body.ok).toBe(true);
    expect(String(body.model || '').length).toBeGreaterThan(2);
    expect(String(body.reply_preview || '').length).toBeGreaterThan(0);
  });

  test('Google embedding (Gemini API / GEMINI_EMBEDDING_MODEL)', async ({ request }) => {
    const res = await request.post(`${API_BASE}/health/smoke/google/embedding`, {
      headers: smokeHeaders(),
      data: JSON.stringify({
        text: 'tradetalk embedding smoke',
        task_type: 'RETRIEVAL_DOCUMENT',
      }),
    });
    if (res.status() === 404) {
      test.skip(true, 'ALLOW_PROVIDER_SMOKE=1 not enabled on API');
    }
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    test.skip(!!body.skipped, body.reason || 'Google embedding skipped');
    expect(body.ok).toBe(true);
    expect(body.dimensions).toBeGreaterThan(8);
    expect(body.model).toBeTruthy();
  });

  test('options flow reachability (AAPL)', async ({ request }) => {
    const res = await request.get(`${API_BASE}/health/smoke/options/AAPL`, {
      headers: smokeHeaders(),
    });
    if (res.status() === 404) {
      test.skip(true, 'ALLOW_PROVIDER_SMOKE=1 not enabled on API');
    }
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    test.skip(!!body.skipped, body.reason || 'Options probe skipped');
    expect(body.ok).toBe(true);
    expect(body.source).toBeTruthy();
  });
});
