import { expect, test } from '@playwright/test';

test('account auth gate renders', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Register' })).toBeVisible();
	await expect(page.getByPlaceholder('x-admin-token')).toHaveCount(0);
	await expect(page.getByText('dev-admin-password')).toHaveCount(0);
});
