// @ts-check
/** Your Morning v0 smoke — requires local API :8000 + Vite :5173 */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Your Morning smoke', () => {
  test('dashboard shows Your Morning section', async ({ page }) => {
    await page.goto(FRONTEND);
    await dismissOnboarding(page);
    await expect(page.getByText('Your Morning', { exact: false }).first()).toBeVisible({ timeout: 20000 });
  });

  test('morning brief API reachable from browser context', async ({ request }) => {
    const res = await request.get('http://127.0.0.1:8000/portfolio/morning-brief');
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty('headline');
    expect(body).toHaveProperty('cards');
    expect(Array.isArray(body.cards)).toBeTruthy();
    expect(body).toHaveProperty('market_session');
    expect(body).toHaveProperty('continuity_moments');
    expect(Array.isArray(body.continuity_moments)).toBeTruthy();
    if (body.has_portfolio && body.cards?.length > 0) {
      expect(body.cards[0]).toHaveProperty('chip');
      expect(body.cards[0]).toHaveProperty('direction');
      const hasMacro = body.cards.some((c) => c.type === 'macro_sector_watch');
      if (hasMacro) {
        const sectorDup = (body.watch_next || []).filter((w) => w.type === 'sector_exposure');
        expect(sectorDup.length).toBe(0);
      }
    }
  });

  test('track record and timeline APIs respond', async ({ request }) => {
    const tr = await request.get('http://127.0.0.1:8000/portfolio/track-record');
    expect(tr.ok()).toBeTruthy();
    const trBody = await tr.json();
    expect(trBody).toHaveProperty('headline');
    expect(trBody).toHaveProperty('graded_count');

    const tl = await request.get('http://127.0.0.1:8000/portfolio/timeline?limit=5');
    expect(tl.ok()).toBeTruthy();
    const tlBody = await tl.json();
    expect(tlBody).toHaveProperty('items');
    expect(Array.isArray(tlBody.items)).toBeTruthy();
  });
});
