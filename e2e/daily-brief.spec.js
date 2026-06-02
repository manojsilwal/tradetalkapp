// @ts-check
const { test, expect } = require('@playwright/test')

const API = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

test.describe('Daily Brief', () => {
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

  test('daily brief page loads', async ({ page }) => {
    await page.goto('/daily-brief')
    await expect(page.getByRole('heading', { name: 'Daily Brief' })).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText(/Top 20 losers/i)).toBeVisible({ timeout: 90_000 })
  })
})
