// An HR manager opens a role: the form, the list, and the opening's dashboard.

import { expect, signIn, test } from './support/fixtures';

test.describe('creating an opening', () => {
  test('HR creates an opening and lands on its dashboard', async ({ page, tenant }) => {
    const title = `E2E Backend Engineer ${Date.now().toString(36)}`;
    await signIn(page, tenant.accounts.hr_manager);

    await page.goto('/hr/requisitions');
    await page.getByTestId('new-opening').click();
    await page.locator('#new-title').fill(title);
    await page.locator('#new-level').selectOption('mid');
    await page.locator('#new-target').fill('2');
    // Three steps: role basics, an optional budget, then review and create.
    await page.getByTestId('new-opening-continue').click();
    await page.getByTestId('new-opening-continue').click();
    await page.getByTestId('create-opening').click();
    await expect(page.getByText(`Opened “${title}”`)).toBeVisible();

    // Creating lands on the new opening's Setup tab.
    await expect(page).toHaveURL(/\/hr\/requisitions\/[0-9a-f-]{36}\?tab=setup$/);
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    // Nothing is published yet, and the dashboard says so rather than implying
    // candidates are moving.
    await expect(page.getByText(/no live workflow/i).first()).toBeVisible();
  });

  test('an opening needs a title before it can be created', async ({ page, tenant }) => {
    await signIn(page, tenant.accounts.hr_manager);
    await page.goto('/hr/requisitions');
    await page.getByTestId('new-opening').click();
    await page.getByTestId('new-opening-continue').click();
    await expect(page.getByRole('alert')).toContainText(/at least 2 characters/);
    await expect(page.getByTestId('new-opening-step-budget')).toHaveCount(0);
    await page.locator('#new-title').fill('A');
    await page.getByTestId('new-opening-continue').click();
    await expect(page.getByTestId('new-opening-step-budget')).toHaveCount(0);
    await page.locator('#new-title').fill('QA Engineer');
    await page.getByTestId('new-opening-continue').click();
    await expect(page.getByTestId('new-opening-step-budget')).toBeVisible();
  });
});
