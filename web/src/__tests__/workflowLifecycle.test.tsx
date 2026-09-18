// D5 — the lifecycle rules, and the candidate's side of a workflow.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import CandidatePreview from '../components/workflow/CandidatePreview';
import { applicationsWarning, publishImpact, stepStates } from '../lib/workflowLifecycle';
import type { Round } from '../api/workflows';

describe('the applications-open warning', () => {
  const base = { title: 'Welder', accepting: true, hasLiveVersion: false, draftVersion: 2 };

  it('warns when an opening takes applications with nothing live', () => {
    const msg = applicationsWarning({ ...base, waiting: { applied: 3, shortlisted: 1 } });
    expect(msg).toMatch(/Welder is accepting applications, but no workflow is live/);
    expect(msg).toMatch(/4 candidates are already waiting/);
    expect(msg).toMatch(/Publish version 2/);
  });

  it('is silent once a version is live, or when applications are closed', () => {
    expect(applicationsWarning({ ...base, hasLiveVersion: true })).toBeNull();
    expect(applicationsWarning({ ...base, accepting: false })).toBeNull();
  });

  it('points at building one when there is no draft either', () => {
    expect(applicationsWarning({ ...base, draftVersion: null })).toMatch(/Build and publish/);
  });
});

describe('what publishing will do', () => {
  it('says who starts and who waits, before it happens', () => {
    expect(publishImpact({ shortlisted: 2, applied: 1 })).toBe(
      '2 shortlisted candidates will start the first round; 1 applicant will join and wait for your shortlist.',
    );
    expect(publishImpact({ shortlisted: 0, applied: 0 })).toBeNull();
  });
});

describe('the lifecycle steps', () => {
  it('walks draft → dry run → review → approved → published', () => {
    // Not dry-run yet.
    expect(
      stepStates({ status: 'draft', reviewStatus: 'draft', simulated: false, publishable: true }),
    ).toMatchObject({ dryRun: 'current', review: 'todo' });

    // Dry-run done, publishable — ready to submit for review.
    expect(
      stepStates({ status: 'draft', reviewStatus: 'draft', simulated: true, publishable: true }),
    ).toMatchObject({ dryRun: 'done', review: 'current' });

    // Dry-run done, but validation still fails — review is blocked, not offered.
    expect(
      stepStates({ status: 'draft', reviewStatus: 'draft', simulated: true, publishable: false }),
    ).toMatchObject({ review: 'blocked' });

    // A super admin is looking at it — locked, waiting.
    expect(
      stepStates({ status: 'draft', reviewStatus: 'in_review', simulated: true, publishable: true }),
    ).toMatchObject({ review: 'current', approved: 'todo', publish: 'todo' });

    // Approved, not yet published.
    expect(
      stepStates({ status: 'draft', reviewStatus: 'approved', simulated: true, publishable: true }),
    ).toMatchObject({ approved: 'done', publish: 'current' });

    // Live — every step reads as done.
    expect(
      stepStates({ status: 'published', reviewStatus: 'approved', simulated: true, publishable: true }),
    ).toMatchObject({ draft: 'done', dryRun: 'done', review: 'done', approved: 'done', publish: 'done' });
  });
});

describe('the candidate preview', () => {
  const round = (over: Partial<Round>): Round =>
    ({
      id: 'r', title: 'Round', kind: 'mcq', position: 0, pass_threshold: 60,
      time_limit_seconds: 1800, deadline_days: 5, exam_round_id: null, criteria: [],
      ...over,
    }) as unknown as Round;

  it('walks the rounds in order, in the words the candidate sees', () => {
    render(
      <CandidatePreview
        title="Backend Engineer"
        rounds={[
          round({ id: 'b', title: 'AI Interview', kind: 'ai_interview', position: 1,
                  time_limit_seconds: null, deadline_days: 7 }),
          round({ id: 'a', title: 'Aptitude', position: 0 }),
        ]}
      />,
    );
    expect(screen.getByText(/Round 1 of 2 · Aptitude/, { selector: 'p' })).toBeTruthy();
    expect(screen.getByText(/within 5 days, with 30 minutes once they start/)).toBeTruthy();
    expect(screen.getByText(/Round 2 of 2 · AI Interview/, { selector: 'p' })).toBeTruthy();
  });

  it('never shows candidates a score or threshold, and never says they failed', () => {
    const { container } = render(
      <CandidatePreview title="Welder" rounds={[round({ pass_threshold: 60 })]} />,
    );
    const text = container.textContent ?? '';
    // The preview SAYS they never see thresholds; what must not appear is one.
    expect(text).not.toMatch(/60|advance at/i);
    expect(text).toMatch(/Under review/);
    expect(text).toMatch(/not told they failed/);
  });

  it('says a human-review round asks nothing of the candidate', () => {
    render(
      <CandidatePreview
        title="Welder"
        rounds={[round({ kind: 'human_review', title: 'Final review', pass_threshold: null })]}
      />,
    );
    expect(screen.getByText('Nothing to do. They wait while you review.')).toBeTruthy();
  });
});
