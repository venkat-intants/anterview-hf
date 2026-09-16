// The workflow builder: starting from a template, the human gates the canvas
// draws, what blocks publishing, and what publishing locks.
//
// Every test starts from its own opening, created through the API.

import { Api, createOpening, expect, signIn, test } from './support/fixtures';

interface WorkflowSummary {
  id: string;
  status: string;
  version: number;
}

interface WorkflowDetail extends WorkflowSummary {
  rounds: { id: string; title: string; kind: string }[];
}

test.describe('workflow builder', () => {
  test('a template draws the journey with its human gates', async ({ page, request, tenant }) => {
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Python Developer');
    await signIn(page, tenant.accounts.hr_manager);

    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    await expect(page.getByTestId('template-technical')).toBeVisible();
    await expect(page.getByTestId('template-non_technical')).toBeVisible();
    await expect(page.getByTestId('template-interview_only')).toBeVisible();

    await page.getByTestId('template-interview_only').click();

    await expect(page.getByTestId('workflow-meta')).toContainText('v1 · draft');
    // Nothing starts until a person shortlists, and nobody is rejected by a
    // threshold: both are drawn, not implied (D-05).
    await expect(page.getByTestId('shortlist-gate')).toContainText('You shortlist');
    await expect(page.getByTestId('hold-path')).toContainText('Nobody is rejected automatically');
    await expect(page.getByText('A person decides', { exact: true }).first()).toBeVisible();
  });

  test('publishing is blocked while test rounds have no questions', async ({ page, request, tenant }) => {
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Data Engineer');
    await signIn(page, tenant.accounts.hr_manager);

    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    await page.getByTestId('template-technical').click();
    await expect(page.getByTestId('workflow-meta')).toContainText('v1 · draft');

    const lifecycle = page.getByRole('list', { name: 'Workflow lifecycle' });
    await expect(lifecycle).toContainText(/issues? to fix/);
    // The header button and the lifecycle step agree (they used to disagree).
    const publish = page.getByTestId('publish-workflow');
    await expect(publish).toBeDisabled();
    await expect(publish).toHaveAttribute('title', /Fix \d+ issues? before publishing/);
    await expect(lifecycle.getByRole('button', { name: '4. Publish' })).toBeDisabled();
  });

  test('an interview-only workflow publishes and becomes read-only', async ({ page, request, tenant }) => {
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Product Manager');
    await signIn(page, tenant.accounts.hr_manager);

    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    await page.getByTestId('template-interview_only').click();
    await expect(page.getByTestId('workflow-meta')).toContainText('v1 · draft');

    const publish = page.getByTestId('publish-workflow');
    await expect(publish).toBeEnabled();
    await publish.click();
    await expect(page.getByTestId('confirm-publish')).toHaveText('Publish version 1');
    await page.getByTestId('confirm-publish').click();

    await expect(page.getByText(/Version 1 is live/)).toBeVisible();
    await expect(page.getByTestId('workflow-meta')).toContainText('v1 · live');
    await expect(page.getByTestId('edit-as-new-version')).toBeVisible();
    await expect(page.getByText(/This version is live, so it is read-only/)).toBeVisible();

    // The lock is the server's, not just the page's.
    const versions = await api.get<WorkflowSummary[]>(`/hr/requisitions/${opening.id}/workflows`);
    const live = versions.find((w) => w.status === 'published');
    expect(live, 'a published version exists').toBeTruthy();
    const detail = await api.get<WorkflowDetail>(`/hr/workflows/${live!.id}`);
    const edit = await api.patchRaw(`/hr/workflows/${live!.id}/rounds/${detail.rounds[0].id}`, {
      title: 'Edited after publishing',
    });
    expect(edit.status).toBe(409);
    expect(edit.body).toContain('Create a new version');
  });
});
