// Signing in: every role reaches its own console, and the doors that should be
// shut are.

import { expect, signIn, test, type Role } from './support/fixtures';

const CONSOLES: { role: Role; path: string; title: string | RegExp }[] = [
  { role: 'platform_owner', path: '/platform', title: 'Platform Owner' },
  { role: 'super_admin', path: '/superadmin', title: 'Super Admin' },
  { role: 'hr_manager', path: '/hr', title: /Welcome|HR Console/ },
];

test.describe('signing in', () => {
  for (const { role, path, title } of CONSOLES) {
    test(`${role} lands on their own console`, async ({ page, tenant }) => {
      await signIn(page, tenant.accounts[role]);
      await expect(page).toHaveURL(new RegExp(`${path}$`));
      await expect(page.getByTestId('page-title')).toContainText(title);
    });
  }

  test('a first-time candidate is taken to onboarding', async ({ page, tenant }) => {
    await signIn(page, tenant.accounts.candidate);
    await expect(page).toHaveURL(/\/onboarding/);
    await expect(page.locator('#onboarding-name-title')).toBeVisible();
  });

  test('a wrong password is refused and nobody is signed in', async ({ page, tenant }) => {
    await page.goto('/login');
    await page.getByTestId('login-email').fill(tenant.accounts.hr_manager.email);
    await page.getByTestId('login-password').fill('not-the-password-123');
    await page.getByTestId('login-submit').click();
    await expect(page.getByText(/invalid email or password/i)).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe('access', () => {
  test('a signed-out visitor to an HR page is sent to sign in', async ({ page }) => {
    await page.goto('/hr/requisitions');
    await expect(page).toHaveURL(/\/login/);
  });

  test('an HR manager cannot open the company super admin console', async ({ page, tenant }) => {
    await signIn(page, tenant.accounts.hr_manager);
    await page.goto('/superadmin');
    await expect(page).not.toHaveURL(/\/superadmin/);
    await expect(page.getByTestId('page-title')).not.toContainText('Super Admin');
  });

  test('a candidate cannot open the HR console', async ({ page, tenant }) => {
    await signIn(page, tenant.accounts.candidate);
    await page.goto('/hr');
    await expect(page).not.toHaveURL(/\/hr$/);
  });
});
