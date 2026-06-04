// @ts-check
const { test, expect } = require('@playwright/test')

const API = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

test.describe('Daily Brief & Screener', () => {
  test('GET /daily-brief returns movers payload', async ({ request }) => {
    const res = await request.get(`${API}/daily-brief`)
    expect(res.ok()).toBeTruthy()
    const body = await res.json()
    expect(body.trade_date).toBeTruthy()
    expect(Array.isArray(body.losers)).toBe(true)
    expect(Array.isArray(body.gainers)).toBe(true)
    expect(body.losers.length + body.gainers.length).toBeGreaterThan(0)
    const row = body.losers[0] || body.gainers[0]
    expect(row.symbol).toBeTruthy()
    expect(row.verdict).toBeTruthy()
  })

  test('GET /daily-brief/screener returns filtered actionable signals', async ({ request }) => {
    const res = await request.get(`${API}/daily-brief/screener`)
    expect(res.ok()).toBeTruthy()
    const body = await res.json()
    expect(body.trade_date).toBeTruthy()
    expect(Array.isArray(body.rows)).toBe(true)
    // Check that MSFT or any Hold or non-actionable rows are excluded.
    // Every row verdict must be Strong Buy, Buy, or Sell.
    for (const row of body.rows) {
      expect(["Strong Buy", "Buy", "Sell"]).toContain(row.verdict)
    }
  })

  test('daily brief page loads and displays Movers or Screener tabs', async ({ page }) => {
    await page.goto('/daily-brief')
    await expect(page.getByRole('heading', { name: 'Daily Brief' })).toBeVisible({ timeout: 60_000 })
    
    // Check if either tabs are visible (if screener data exists) or losers list is visible (heuristic fallback)
    const losersText = page.getByText(/Top 20 losers/i)
    const growthTab = page.getByRole('button', { name: 'Growth Buy/Sells' })
    
    await expect(losersText.or(growthTab)).toBeVisible({ timeout: 90_000 })
  })
})

