import { expect, test } from '@playwright/test';

test('operator token gate renders', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Connect to gateway admin' })).toBeVisible();
	await expect(page.getByPlaceholder('x-admin-token')).toBeVisible();
});
