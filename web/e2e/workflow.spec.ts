// The workflow builder: starting from a template, the human gates the canvas
// draws, what blocks a version from review, how a second person approves it
// (PH4-O6), and what publishing locks.
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

  test('a version with problems cannot go to review', async ({ page, request, tenant }) => {
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Data Engineer');
    await signIn(page, tenant.accounts.hr_manager);

    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    await page.getByTestId('template-technical').click();
    await expect(page.getByTestId('workflow-meta')).toContainText('v1 · draft');

    // Nothing unapproved can be published, so the header does not offer it.
    await expect(page.getByTestId('publish-workflow')).toHaveCount(0);
    const review = page.getByTestId('review-panel');
    await review.getByRole('button', { name: 'Submit for review' }).click();
    // The test rounds have no questions: the submission is refused, with why.
    await expect(review.getByRole('alert')).toContainText(/questions/);
    await expect(review.getByRole('button', { name: 'Submit for review' })).toBeVisible();
  });

  test('a second person approves a version before it goes live', async ({
    page,
    request,
    browser,
    tenant,
  }) => {
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Product Manager');
    await signIn(page, tenant.accounts.hr_manager);

    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    await page.getByTestId('template-interview_only').click();
    await expect(page.getByTestId('workflow-meta')).toContainText('v1 · draft');

    const review = page.getByTestId('review-panel');
    await review.getByRole('button', { name: 'Submit for review' }).click();
    await expect(review.getByText('Locked while a company super admin reviews it.')).toBeVisible();
    await expect(page.getByTestId('publish-workflow')).toHaveCount(0);

    const versions = await api.get<WorkflowSummary[]>(`/hr/requisitions/${opening.id}/workflows`);
    const draft = versions.find((w) => w.status === 'draft');
    expect(draft, 'the draft exists').toBeTruthy();
    // HR cannot approve their own work: the approval route is the super admin's.
    const selfApprove = await api.postRaw(`/admin/workflow-reviews/${draft!.id}/approve`, {
      note: null,
    });
    expect(selfApprove.status).toBe(403);

    // ── The company super admin, in their own session ──────────────────────
    const adminContext = await browser.newContext();
    const admin = await adminContext.newPage();
    await signIn(admin, tenant.accounts.super_admin);
    await admin.goto(`/superadmin/workflow-reviews/${draft!.id}`);
    await admin.getByRole('button', { name: 'Approve' }).click();
    await admin.getByRole('button', { name: 'Confirm approval' }).click();
    await expect(admin.getByText(/Approved — HR can publish it now/)).toBeVisible();
    await adminContext.close();

    // ── Back to HR: now, and only now, it can be published ──────────────────
    await page.reload();
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
    const after = await api.get<WorkflowSummary[]>(`/hr/requisitions/${opening.id}/workflows`);
    const live = after.find((w) => w.status === 'published');
    expect(live, 'a published version exists').toBeTruthy();
    const detail = await api.get<WorkflowDetail>(`/hr/workflows/${live!.id}`);
    const edit = await api.patchRaw(`/hr/workflows/${live!.id}/rounds/${detail.rounds[0].id}`, {
      title: 'Edited after publishing',
    });
    expect(edit.status).toBe(409);
    expect(edit.body).toContain('Create a new version');
  });
});
