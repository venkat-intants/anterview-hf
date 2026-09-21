// TaskSubmissionSection — PH4-D4. The HR drawer's view of a job simulation /
// portfolio submission: lifecycle, the candidate's own content once it is
// readable, re-issue and withdraw.
//
// The one thing this file exists to pin: THERE IS NO REJECT ACTION HERE. A
// submission is evidence, not a decision (CLAUDE.md constraint 9 / D-05) —
// the round's pass/hold verdict is recorded on the decision queue, through
// the existing round-review action, never from this section.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { TaskItem, TaskResponseOut, TaskSubmissionSummary } from '../api/jobTasks';

const listEnrolmentTasks = vi.fn();
const reissueTaskSubmission = vi.fn();
const withdrawTaskSubmission = vi.fn();
const downloadTaskArtifact = vi.fn();
vi.mock('../api/jobTasks', async () => {
  const actual = await vi.importActual<typeof import('../api/jobTasks')>('../api/jobTasks');
  return {
    ...actual,
    listEnrolmentTasks: (...a: unknown[]) => listEnrolmentTasks(...a) as unknown,
    reissueTaskSubmission: (...a: unknown[]) => reissueTaskSubmission(...a) as unknown,
    withdrawTaskSubmission: (...a: unknown[]) => withdrawTaskSubmission(...a) as unknown,
    downloadTaskArtifact: (...a: unknown[]) => downloadTaskArtifact(...a) as unknown,
  };
});

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import TaskSubmissionSection from '../components/hr/TaskSubmissionSection';

function renderSection(enrolmentId = 'en-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TaskSubmissionSection enrolmentId={enrolmentId} />
    </QueryClientProvider>,
  );
}

const SUBMISSION: TaskSubmissionSummary = {
  id: 'sub-1',
  enrolment_id: 'en-1',
  round_id: 'r-1',
  round_title: 'Take-home simulation',
  kind: 'job_simulation',
  status: 'submitted',
  candidate_name: 'Nadia Newbie',
  due_at: '2026-09-20T00:00:00.000Z',
  started_at: '2026-09-18T00:00:00.000Z',
  submitted_at: '2026-09-19T00:00:00.000Z',
  attempt_no: 1,
  created_at: '2026-09-15T00:00:00.000Z',
  superseded_at: null,
  is_current: true,
  consent_withdrawn: false,
  brief: null,
  items: [],
  responses: [],
};

const ITEM_TEXT: TaskItem = {
  key: 'design_doc',
  prompt: 'Describe your approach to the assignment.',
  response_type: 'text',
  required: true,
  max_chars: 2000,
};

const ITEM_LINK: TaskItem = {
  key: 'demo_link',
  prompt: 'Share a link to your working demo.',
  response_type: 'link',
  required: true,
  max_chars: null,
};

const ITEM_FILE: TaskItem = {
  key: 'plan_file',
  prompt: 'Upload your plan.',
  response_type: 'file',
  required: true,
  max_chars: null,
};

const TEXT_RESPONSE: TaskResponseOut = {
  id: 'r-text',
  item_key: 'design_doc',
  response_type: 'text',
  text_value: 'I would start by mapping out the requirements.',
  link_url: null,
  link_kind: null,
  title: null,
  description: null,
  original_name: null,
  content_type: null,
  size_bytes: null,
};

const LINK_RESPONSE: TaskResponseOut = {
  id: 'r-link',
  item_key: 'demo_link',
  response_type: 'link',
  text_value: null,
  link_url: 'https://github.com/nadia/demo',
  link_kind: 'repository',
  title: null,
  description: null,
  original_name: null,
  content_type: null,
  size_bytes: null,
};

const FILE_RESPONSE: TaskResponseOut = {
  id: 'r-file',
  item_key: 'plan_file',
  response_type: 'file',
  text_value: null,
  link_url: null,
  link_kind: null,
  title: null,
  description: null,
  original_name: 'plan.pdf',
  content_type: 'application/pdf',
  size_bytes: 2048,
};

// A submitted, consent-intact row — the server sends brief/items/responses
// only once both hold (M1), so this is the fixture for content tests.
const READABLE_SUBMISSION: TaskSubmissionSummary = {
  ...SUBMISSION,
  brief: 'Design a small feature end to end.',
  items: [ITEM_TEXT, ITEM_LINK, ITEM_FILE],
  responses: [TEXT_RESPONSE, LINK_RESPONSE, FILE_RESPONSE],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TaskSubmissionSection', () => {
  it('renders nothing for an application with no task round', async () => {
    listEnrolmentTasks.mockResolvedValue([]);
    const { container } = renderSection();
    await waitFor(() => expect(listEnrolmentTasks).toHaveBeenCalledWith('en-1'));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows the round, its lifecycle state and the key dates', async () => {
    listEnrolmentTasks.mockResolvedValue([SUBMISSION]);
    renderSection();

    expect(await screen.findByText('Take-home simulation')).toBeInTheDocument();
    expect(screen.getByText('submitted')).toBeInTheDocument();
  });

  it('never offers a reject action of any kind', async () => {
    listEnrolmentTasks.mockResolvedValue([SUBMISSION]);
    renderSection();
    await screen.findByText('Take-home simulation');

    for (const label of [/reject/i, /fail/i, /decline/i, /hire/i, /advance/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
    }
  });

  it('withdraws an open submission with an optional reason', async () => {
    const user = userEvent.setup();
    listEnrolmentTasks.mockResolvedValue([
      { ...SUBMISSION, status: 'in_progress', submitted_at: null },
    ]);
    withdrawTaskSubmission.mockResolvedValue({ ...SUBMISSION, status: 'withdrawn' });
    renderSection();

    await screen.findByText('Take-home simulation');
    await user.type(
      screen.getByLabelText(/withdraw reason for take-home simulation/i),
      'Role was closed',
    );
    await user.click(screen.getByRole('button', { name: /^withdraw$/i }));

    await waitFor(() =>
      expect(withdrawTaskSubmission).toHaveBeenCalledWith('sub-1', 'Role was closed'),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('offers no withdraw control once a submission is no longer open', async () => {
    listEnrolmentTasks.mockResolvedValue([SUBMISSION]); // status: submitted
    renderSection();
    await screen.findByText('Take-home simulation');
    expect(screen.queryByRole('button', { name: /^withdraw$/i })).not.toBeInTheDocument();
  });

  it('re-issues a fresh link for an expired submission', async () => {
    const user = userEvent.setup();
    listEnrolmentTasks.mockResolvedValue([
      { ...SUBMISSION, status: 'expired', submitted_at: null },
    ]);
    reissueTaskSubmission.mockResolvedValue({ ...SUBMISSION, id: 'sub-2', attempt_no: 2 });
    renderSection();

    await screen.findByText('Take-home simulation');
    await user.click(screen.getByRole('button', { name: /re-issue a link/i }));

    await waitFor(() => expect(reissueTaskSubmission).toHaveBeenCalledWith('sub-1'));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('says so when the submissions could not be loaded', async () => {
    listEnrolmentTasks.mockRejectedValue(new Error('boom'));
    renderSection();
    expect(
      await screen.findByText(/could not load this application.s task submissions/i),
    ).toBeInTheDocument();
  });

  // PH4-D4 wave 5: a candidate can withdraw consent for a submission the
  // hiring team already has.
  it('shows a consent-withdrawn tag instead of treating the submission as empty', async () => {
    listEnrolmentTasks.mockResolvedValue([{ ...SUBMISSION, consent_withdrawn: true }]);
    renderSection();

    expect(await screen.findByText('Take-home simulation')).toBeInTheDocument();
    expect(screen.getByText(/consent withdrawn/i)).toBeInTheDocument();
  });

  it('does not show the consent-withdrawn tag when consent is intact', async () => {
    listEnrolmentTasks.mockResolvedValue([{ ...SUBMISSION, consent_withdrawn: false }]);
    renderSection();

    await screen.findByText('Take-home simulation');
    expect(screen.queryByText(/consent withdrawn/i)).not.toBeInTheDocument();
  });

  // PH4-D4 gap 4 fix: `GET /hr/enrolments/{id}/tasks` now carries a readable
  // submission's brief, item prompts and responses — previously HR could
  // reach a candidate's actual answers only through an assigned reviewer's
  // scorecard.
  describe('a readable submission (submitted, consent intact)', () => {
    it('shows the brief, each item prompt and a text answer', async () => {
      listEnrolmentTasks.mockResolvedValue([READABLE_SUBMISSION]);
      renderSection();

      expect(await screen.findByText('Design a small feature end to end.')).toBeInTheDocument();
      expect(
        screen.getByText('Describe your approach to the assignment.'),
      ).toBeInTheDocument();
      expect(
        screen.getByText('I would start by mapping out the requirements.'),
      ).toBeInTheDocument();
    });

    it('renders a link answer through the interstitial, never a bare anchor', async () => {
      listEnrolmentTasks.mockResolvedValue([READABLE_SUBMISSION]);
      renderSection();

      await screen.findByText('Share a link to your working demo.');
      // The candidate's link is untrusted content (ExternalLinkGate's own
      // rule) — nothing may render it as a directly clickable <a> before the
      // interstitial is confirmed.
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /open link/i })).toBeInTheDocument();
    });

    it("downloads a file answer through the submission's artifact route", async () => {
      const user = userEvent.setup();
      downloadTaskArtifact.mockResolvedValue({
        url: 'https://files.example.com/plan.pdf',
        expires_in: 300,
      });
      listEnrolmentTasks.mockResolvedValue([READABLE_SUBMISSION]);
      renderSection();

      const button = await screen.findByRole('button', { name: /plan\.pdf/i });
      await user.click(button);

      await waitFor(() =>
        expect(downloadTaskArtifact).toHaveBeenCalledWith('sub-1', 'r-file'),
      );
    });

    it('never shows content when consent has been withdrawn, even for an otherwise-readable row', async () => {
      // Defensive: even if a row carries content (a backend bug, or a stale
      // cache read), a withdrawn row must not render it — the same M1 gate
      // the server itself applies.
      listEnrolmentTasks.mockResolvedValue([{ ...READABLE_SUBMISSION, consent_withdrawn: true }]);
      renderSection();

      await screen.findByText('Take-home simulation');
      expect(screen.getByText(/consent withdrawn/i)).toBeInTheDocument();
      expect(screen.queryByText('Design a small feature end to end.')).not.toBeInTheDocument();
      expect(
        screen.queryByText('Describe your approach to the assignment.'),
      ).not.toBeInTheDocument();
    });
  });

  // PH4-D4 gap 4 fix: the live row is `is_current`, never list position.
  it('uses is_current, not list position, to decide which row offers withdraw/re-issue', async () => {
    const OLD_ROW: TaskSubmissionSummary = {
      ...SUBMISSION,
      id: 'sub-old',
      round_title: 'Old attempt',
      status: 'expired',
      submitted_at: null,
      is_current: false,
      attempt_no: 1,
    };
    const CURRENT_ROW: TaskSubmissionSummary = {
      ...SUBMISSION,
      id: 'sub-new',
      round_title: 'Current attempt',
      status: 'in_progress',
      submitted_at: null,
      is_current: true,
      attempt_no: 2,
    };
    // The non-current row is listed FIRST — is_current must still decide.
    listEnrolmentTasks.mockResolvedValue([OLD_ROW, CURRENT_ROW]);
    renderSection();

    await screen.findByText('Old attempt');
    await screen.findByText(/Current attempt/);

    expect(screen.queryByRole('button', { name: /re-issue a link/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^withdraw$/i })).toBeInTheDocument();
  });
});
