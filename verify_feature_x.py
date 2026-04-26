from playwright.sync_api import Page, expect, sync_playwright

def test_unified_dashboard(page: Page):
  page.goto("http://localhost:5173")

  # Wait for tour overlay and skip it
  try:
    skip_btn = page.locator('button:has-text("Skip tour")')
    if skip_btn.is_visible(timeout=5000):
        skip_btn.click()
  except:
    pass

  page.wait_for_timeout(2000)

  input_field = page.locator('input[placeholder="e.g. AAPL"]')
  if input_field.is_visible():
      input_field.fill("TSLA")
      page.locator('button:has-text("Analyze")').click()

  page.wait_for_timeout(10000)
  page.screenshot(path="/app/test-results/debug-view3.png", full_page=True)

if __name__ == "__main__":
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    try:
      test_unified_dashboard(page)
    finally:
      browser.close()
