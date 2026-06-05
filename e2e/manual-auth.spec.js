// @ts-check
const { test, expect } = require('@playwright/test');

test.describe('Manual Credentials Authentication', () => {
  test('user can sign up, sign out, sign in with correct credentials, and fail with incorrect credentials', async ({ page }) => {
    // Generate a unique email to prevent collisions between runs
    const email = `testuser_${Date.now()}@example.com`;
    const password = 'securepassword123';
    const name = 'Manual Tester';

    // 1. Visit paper portfolio page first to see if we are already logged in (due to silent dev login)
    await page.goto('/portfolio');

    // 2. Dismiss onboarding tour if present
    const skipBtn = page.getByRole('button', { name: 'Skip tour' });
    if (await skipBtn.isVisible().catch(() => false)) {
      await skipBtn.click();
    }

    // 3. Wait up to 5 seconds for the silent login to complete and show the profile sign-out button
    try {
      await page.waitForSelector('button[title*="click to sign out"]', { timeout: 5000 });
      // If visible, click it to clear the silent/dev session.
      await page.locator('button[title*="click to sign out"]').click();
      // Wait for user to be cleared and state to settle
      await page.waitForTimeout(1000);
    } catch (e) {
      console.log('Silent login did not complete or we are already logged out.');
    }

    // 4. Click the login button in the sidebar to navigate client-side to /login
    await page.locator('button[title*="Sign in to track"]').click();

    // 5. Confirm AuthGate is visible
    await expect(page.getByText('Unlock Your Account')).toBeVisible({ timeout: 15000 });

    // 6. Switch to Sign Up tab
    await page.getByRole('button', { name: 'Sign Up', exact: true }).click();

    // 7. Fill out and submit Sign Up form
    await page.getByPlaceholder('Your Name').fill(name);
    await page.getByPlaceholder('Email address').fill(email);
    await page.getByPlaceholder('Password (min 6 chars)').fill(password);
    await page.locator('form button[type="submit"]').click();

    // 8. Confirm successful registration and entry to portfolio
    await expect(page.locator('button[title*="Manual Tester"]')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('button[title*="Dev User"]')).toBeHidden(); // We are no longer dev user

    // 9. Click profile button to sign out (button shows user avatar or name abbreviation)
    await page.locator('button[title*="click to sign out"]').click();
    await page.waitForTimeout(1000);

    // 10. Click the login button in the sidebar again to go to /login client-side
    await page.locator('button[title*="Sign in to track"]').click();
    await expect(page.getByText('Unlock Your Account')).toBeVisible({ timeout: 15000 });

    // 11. Try logging in with INCORRECT password
    await page.getByPlaceholder('Email address').fill(email);
    await page.getByPlaceholder('Password (min 6 chars)').fill('wrongpassword');
    await page.locator('form button[type="submit"]').click();

    // 12. Confirm error message is displayed
    await expect(page.getByText('Invalid email or password')).toBeVisible({ timeout: 10000 });

    // 13. Log in with CORRECT password
    await page.getByPlaceholder('Password (min 6 chars)').fill(password);
    await page.locator('form button[type="submit"]').click();

    // 14. Confirm successful login
    await expect(page.locator('button[title*="Manual Tester"]')).toBeVisible({ timeout: 15000 });
  });
});
