// @ts-check
const { test } = require('@playwright/test')

test('trace network requests', async ({ page }) => {
  page.on('console', msg => console.log('BROWSER LOG:', msg.text()))
  page.on('request', request => {
    console.log('REQ:', request.method(), request.url())
  })
  page.on('response', response => {
    console.log('RES:', response.status(), response.url())
    if (response.url().includes('daily-brief')) {
      response.text().then(text => console.log('DAILY BRIEF BODY:', text.slice(0, 500))).catch(e => {})
    }
  })

  await page.goto('https://frontend-manojsilwals-projects.vercel.app/daily-brief')
  await page.waitForTimeout(5000)
})
