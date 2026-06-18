// @ts-check
const { test, expect } = require('@playwright/test');

test.describe('Google signup + email 2FA sign-in (dev mode)', () => {
  test('dev signup, set password, sign in with OTP, and reject wrong password', async ({ page }) => {
    const password = 'securepassword123';
    const devEmail = 'dev@tradetalk.local';

    await page.goto('/portfolio');

    const skipBtn = page.getByRole('button', { name: 'Skip tour' });
    if (await skipBtn.isVisible().catch(() => false)) {
      await skipBtn.click();
    }

    try {
      await page.waitForSelector('button[title*="click to sign out"]', { timeout: 5000 });
      await page.locator('button[title*="click to sign out"]').click();
      await page.waitForTimeout(1000);
    } catch {
      console.log('Already logged out or no silent login.');
    }

    await page.locator('button[title*="Sign in to track"]').click();
    await expect(page.getByText('Unlock Your Account')).toBeVisible({ timeout: 15000 });

    await page.getByRole('button', { name: 'Sign Up', exact: true }).click();

    const devSignupBtn = page.getByRole('button', { name: 'Dev Sign Up (Google bypass)' });
    if (await devSignupBtn.isVisible().catch(() => false)) {
      await devSignupBtn.click();
      const setPasswordVisible = await page.getByPlaceholder('Password (min 6 chars)').isVisible({ timeout: 5000 }).catch(() => false);
      if (setPasswordVisible) {
        await page.getByPlaceholder('Password (min 6 chars)').fill(password);
        await page.getByPlaceholder('Confirm password').fill(password);
        await page.getByRole('button', { name: 'Create Account' }).click();
        await expect(page.getByText('Account created. Sign in with your email and password.')).toBeVisible({ timeout: 15000 });
      }
    } else {
      console.log('Production Google signup UI — skipping dev signup step.');
    }

    await page.getByRole('button', { name: 'Sign In', exact: true }).click();
    await page.getByPlaceholder('Email address').fill(devEmail);
    await page.getByPlaceholder('Password (min 6 chars)').fill('wrongpassword');
    await page.locator('form button[type="submit"]').click();
    await expect(page.getByText('Invalid email or password')).toBeVisible({ timeout: 10000 });

    await page.getByPlaceholder('Password (min 6 chars)').fill(password);
    await page.locator('form button[type="submit"]').click();

    await expect(page.getByPlaceholder('000000')).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder('000000').fill('123456');
    await page.getByRole('button', { name: 'Verify & Sign In' }).click();

    await expect(page.locator('button[title*="Dev User"], button[title*="dev@tradetalk.local"]')).toBeVisible({ timeout: 15000 });
  });
});
