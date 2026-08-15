import { expect, test } from '@playwright/test';

test('account auth gate renders', async ({ page }) => {
	await page.goto('/');
	// 重设计后的登录页:品牌分栏 + segmented 页签(登录/注册) + 账号密码表单
	await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
	await expect(page.getByRole('button', { name: '注册' })).toBeVisible();
	await expect(page.getByRole('textbox', { name: '用户名' })).toBeVisible();
	await expect(page.getByRole('textbox', { name: '密码' })).toBeVisible();
	await expect(page.getByPlaceholder('x-admin-token')).toHaveCount(0);
	await expect(page.getByText('dev-admin-password')).toHaveCount(0);
});
