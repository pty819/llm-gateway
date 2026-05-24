import { expect, test } from '@playwright/test';

test('operator token gate renders', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Register' })).toBeVisible();
	await expect(page.getByPlaceholder('x-admin-token')).toBeVisible();
});
