// AccommodationsSection — PH4-D2. Disability-adjacent data: interviewer_note,
// internal_note and other_adjustment reach different audiences, and a
// round/exam-round scope needs an application (the server 422s otherwise).
// What matters here:
//   • the scope rules — a round or exam-round scope is unavailable, and
//     explained as such, without an application to attach it to;
//   • the client refuses to submit a round scope with no round chosen, or an
//     adjustment with nothing set, rather than round-tripping a 422;
//   • each note field carries a visible statement of who reads it;
//   • record / revise / revoke all reach the right endpoint with the right
//     body, and the server's own error sentence is shown verbatim.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Accommodation, EffectiveAccommodation } from '../api/accommodations';

const accApi = {
  listAccommodations: vi.fn(),
  recordAccommodation: vi.fn(),
  reviseAccommodation: vi.fn(),
  revokeAccommodation: vi.fn(),
  getEffectiveAccommodation: vi.fn(),
};
vi.mock('../api/accommodations', () => ({
  listAccommodations: (...a: unknown[]) => accApi.listAccommodations(...a) as unknown,
  recordAccommodation: (...a: unknown[]) => accApi.recordAccommodation(...a) as unknown,
  reviseAccommodation: (...a: unknown[]) => accApi.reviseAccommodation(...a) as unknown,
  revokeAccommodation: (...a: unknown[]) => accApi.revokeAccommodation(...a) as unknown,
  getEffectiveAccommodation: (...a: unknown[]) => accApi.getEffectiveAccommodation(...a) as unknown,
}));

const workflowsApi = {
  listWorkflows: vi.fn(),
  getWorkflow: vi.fn(),
};
vi.mock('../api/workflows', () => ({
  listWorkflows: (...a: unknown[]) => workflowsApi.listWorkflows(...a) as unknown,
  getWorkflow: (...a: unknown[]) => workflowsApi.getWorkflow(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

import AccommodationsSection from '../components/hr/AccommodationsSection';

function row(over: Partial<Accommodation> = {}): Accommodation {
  return {
    id: 'acc-1',
    applicant_id: 'ap-1',
    enrolment_id: null,
    round_id: null,
    exam_round_id: null,
    extra_time_percent: 50,
    deadline_extension_days: null,
    relax_auto_submit: false,
    other_adjustment: null,
    interviewer_note: null,
    internal_note: null,
    basis: 'hr_initiated',
    requested_on: null,
    effective_from: '2026-09-01T00:00:00.000Z',
    effective_until: null,
    status: 'active',
    recorded_by_user_id: 'u-hr-1',
    revoked_by_user_id: null,
    revoked_at: null,
    revoke_reason: null,
    supersedes_id: null,
    superseded_at: null,
    superseded_by_id: null,
    redacted_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-01T00:00:00.000Z',
    ...over,
  };
}

function effective(over: Partial<EffectiveAccommodation> = {}): EffectiveAccommodation {
  return { effective: false, ...over };
}

function renderSection(props: {
  applicantId?: string;
  enrolmentId?: string | null;
  requisitionId?: string | null;
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccommodationsSection
        applicantId={props.applicantId ?? 'ap-1'}
        enrolmentId={props.enrolmentId}
        requisitionId={props.requisitionId}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  accApi.listAccommodations.mockResolvedValue([]);
  accApi.getEffectiveAccommodation.mockResolvedValue(effective());
  workflowsApi.listWorkflows.mockResolvedValue([]);
  workflowsApi.getWorkflow.mockResolvedValue(undefined);
});

describe('AccommodationsSection — history', () => {
  it('says so when nothing has been recorded', async () => {
    renderSection();
    expect(await screen.findByText('No accommodation recorded for this applicant.')).toBeInTheDocument();
  });

  it("shows a recorded row's scope, parameters and basis", async () => {
    accApi.listAccommodations.mockResolvedValue([row({ extra_time_percent: 50 })]);
    renderSection();

    expect(await screen.findByText('Whole applicant')).toBeInTheDocument();
    expect(screen.getByText('+50% time')).toBeInTheDocument();
    expect(screen.getByText(/HR-initiated/)).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  it('scopes a row to the application when it carries only an enrolment_id', async () => {
    accApi.listAccommodations.mockResolvedValue([row({ enrolment_id: 'en-1' })]);
    renderSection({ enrolmentId: 'en-1' });
    expect(await screen.findByText('This application')).toBeInTheDocument();
  });

  it('describes a workflow-round scope by the round’s own title', async () => {
    accApi.listAccommodations.mockResolvedValue([
      row({ enrolment_id: 'en-1', round_id: 'r-panel' }),
    ]);
    workflowsApi.listWorkflows.mockResolvedValue([{ id: 'wf-1', status: 'published' }]);
    workflowsApi.getWorkflow.mockResolvedValue({
      rounds: [{ id: 'r-panel', title: 'Panel interview', exam_round_id: null }],
    });
    renderSection({ enrolmentId: 'en-1', requisitionId: 'req-1' });

    expect(await screen.findByText('Workflow round — Panel interview')).toBeInTheDocument();
  });

  it('shows a superseded row without revise/revoke controls', async () => {
    accApi.listAccommodations.mockResolvedValue([
      row({ id: 'acc-old', superseded_at: '2026-09-05T00:00:00.000Z' }),
    ]);
    renderSection();

    expect(await screen.findByText('superseded')).toBeInTheDocument();
    expect(screen.getByText(/see the newer record/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revise' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
  });

  it('shows a revoked row’s reason, and no revise/revoke controls', async () => {
    accApi.listAccommodations.mockResolvedValue([
      row({ status: 'revoked', revoke_reason: 'Candidate withdrew the request' }),
    ]);
    renderSection();

    expect(await screen.findByText('revoked')).toBeInTheDocument();
    expect(screen.getByText(/Candidate withdrew the request/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
  });

  it('says the history could not be loaded, rather than showing none', async () => {
    accApi.listAccommodations.mockRejectedValue(new Error('boom'));
    renderSection();
    expect(
      await screen.findByText("Could not load this applicant’s accommodations."),
    ).toBeInTheDocument();
  });

  it('shows what is currently effective on an open application', async () => {
    accApi.getEffectiveAccommodation.mockResolvedValue(
      effective({ effective: true, extra_time_percent: 30, deadline_extension_days: null, relax_auto_submit: false }),
    );
    renderSection({ enrolmentId: 'en-1' });
    expect(
      await screen.findByText('Currently effective on this application: +30% time'),
    ).toBeInTheDocument();
  });

  it('does not ask what is effective without an application open', async () => {
    renderSection({ enrolmentId: null });
    await screen.findByText('No accommodation recorded for this applicant.');
    expect(accApi.getEffectiveAccommodation).not.toHaveBeenCalled();
  });
});

describe('AccommodationsSection — scope rules', () => {
  it('disables application/round/exam-round scopes without an application, and says why', async () => {
    const user = userEvent.setup();
    renderSection({ enrolmentId: null });

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));

    expect(screen.getByRole('radio', { name: 'This application' })).toBeDisabled();
    expect(screen.getByRole('radio', { name: 'One workflow round' })).toBeDisabled();
    expect(screen.getByRole('radio', { name: 'One exam round' })).toBeDisabled();
    expect(screen.getByRole('radio', { name: 'Whole applicant' })).toBeEnabled();
    expect(
      screen.getByText(/a round-scoped adjustment needs an application/i),
    ).toBeInTheDocument();
  });

  it('enables every scope once opened from a specific application', async () => {
    const user = userEvent.setup();
    renderSection({ enrolmentId: 'en-1' });

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));

    expect(screen.getByRole('radio', { name: 'This application' })).toBeEnabled();
    expect(screen.getByRole('radio', { name: 'One workflow round' })).toBeEnabled();
    expect(screen.getByRole('radio', { name: 'One exam round' })).toBeEnabled();
  });

  it('will not submit a round scope with no round chosen', async () => {
    const user = userEvent.setup();
    renderSection({ enrolmentId: 'en-1' });

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));
    await user.click(screen.getByRole('radio', { name: 'One workflow round' }));
    await user.type(screen.getByLabelText('Extra time percent'), '50');
    await user.click(screen.getByRole('button', { name: 'Record accommodation' }));

    expect(await screen.findByText('Choose a round.')).toBeInTheDocument();
    expect(accApi.recordAccommodation).not.toHaveBeenCalled();
  });

  it('will not submit with no adjustment at all', async () => {
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));
    await user.click(screen.getByRole('button', { name: 'Record accommodation' }));

    expect(await screen.findByText('Record at least one adjustment.')).toBeInTheDocument();
    expect(accApi.recordAccommodation).not.toHaveBeenCalled();
  });

  it('lists exam rounds separately from workflow rounds, sourced from the exam-backed rounds', async () => {
    const user = userEvent.setup();
    workflowsApi.listWorkflows.mockResolvedValue([{ id: 'wf-1', status: 'published' }]);
    workflowsApi.getWorkflow.mockResolvedValue({
      rounds: [
        { id: 'r-panel', title: 'Panel interview', exam_round_id: null },
        { id: 'r-mcq', title: 'Aptitude MCQ', exam_round_id: 'er-1' },
      ],
    });
    renderSection({ enrolmentId: 'en-1', requisitionId: 'req-1' });

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));
    await user.click(screen.getByRole('radio', { name: 'One workflow round' }));
    const roundSelect = await screen.findByLabelText('Workflow round');
    expect(within(roundSelect).getByText('Panel interview')).toBeInTheDocument();
    expect(within(roundSelect).getByText('Aptitude MCQ')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'One exam round' }));
    const examSelect = await screen.findByLabelText('Exam round');
    expect(within(examSelect).queryByText('Panel interview')).not.toBeInTheDocument();
    expect(within(examSelect).getByText('Aptitude MCQ')).toBeInTheDocument();
  });
});

describe('AccommodationsSection — the note warnings', () => {
  it('warns that the other-adjustment text is HR-only, never the candidate or an interviewer', async () => {
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));

    expect(
      screen.getByText(/HR only\. The candidate is told only that an adjustment was recorded/),
    ).toBeInTheDocument();
  });

  it("warns that the interviewer note is shown to interviewers and never to the candidate", async () => {
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));

    expect(
      screen.getByText(/Shown to interviewers assigned to this candidate.s rounds/),
    ).toBeInTheDocument();
    expect(screen.getByText(/The candidate never sees it\./)).toBeInTheDocument();
  });

  it('warns that the internal note reaches neither interviewers nor the candidate', async () => {
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));

    expect(
      screen.getByText(/HR only\. Interviewers do not see this note, and neither does the candidate/),
    ).toBeInTheDocument();
  });
});

describe('AccommodationsSection — recording', () => {
  it('records a whole-applicant adjustment with the fields entered', async () => {
    accApi.recordAccommodation.mockResolvedValue({ id: 'acc-new' });
    const user = userEvent.setup();
    renderSection({ applicantId: 'ap-1', enrolmentId: null });

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));
    await user.type(screen.getByLabelText('Extra time percent'), '50');
    await user.type(
      screen.getByPlaceholderText('What an interviewer needs to know to run this round fairly'),
      'Extra time granted — no other change needed.',
    );
    await user.click(screen.getByRole('button', { name: 'Record accommodation' }));

    await waitFor(() => expect(accApi.recordAccommodation).toHaveBeenCalledTimes(1));
    const [applicantId, body] = accApi.recordAccommodation.mock.calls[0] as [string, Record<string, unknown>];
    expect(applicantId).toBe('ap-1');
    expect(body).toMatchObject({
      enrolment_id: null,
      round_id: null,
      exam_round_id: null,
      extra_time_percent: 50,
      basis: 'hr_initiated',
      interviewer_note: 'Extra time granted — no other change needed.',
      internal_note: null,
      other_adjustment: null,
    });
    expect(toastSuccess).toHaveBeenCalledWith('Accommodation recorded');
  });

  it('scopes the request to the open application when that scope is chosen', async () => {
    accApi.recordAccommodation.mockResolvedValue({ id: 'acc-new' });
    const user = userEvent.setup();
    renderSection({ applicantId: 'ap-1', enrolmentId: 'en-1' });

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));
    await user.click(screen.getByRole('radio', { name: 'This application' }));
    await user.type(screen.getByLabelText('Deadline extension days'), '3');
    await user.click(screen.getByRole('radio', { name: /candidate request/i }));
    await user.click(screen.getByRole('button', { name: 'Record accommodation' }));

    await waitFor(() => expect(accApi.recordAccommodation).toHaveBeenCalledTimes(1));
    const [, body] = accApi.recordAccommodation.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).toMatchObject({
      enrolment_id: 'en-1',
      deadline_extension_days: 3,
      basis: 'candidate_request',
    });
  });

  it("shows the server's own refusal verbatim, distinct from the client-side guard", async () => {
    accApi.recordAccommodation.mockRejectedValue(
      new Error("This changed while you were working. Reload and try again."),
    );
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Record adjustment' }));
    await user.type(screen.getByLabelText('Extra time percent'), '20');
    await user.click(screen.getByRole('button', { name: 'Record accommodation' }));

    expect(
      await screen.findByText('This changed while you were working. Reload and try again.'),
    ).toBeInTheDocument();
    expect(toastError).toHaveBeenCalledWith(
      'This changed while you were working. Reload and try again.',
    );
  });
});

describe('AccommodationsSection — revise and revoke', () => {
  it('revises a live accommodation in the same scope', async () => {
    accApi.listAccommodations.mockResolvedValue([row({ id: 'acc-1', extra_time_percent: 50 })]);
    accApi.reviseAccommodation.mockResolvedValue({ id: 'acc-2' });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Revise' }));
    const timeInput = screen.getByLabelText('Extra time percent');
    await user.clear(timeInput);
    await user.type(timeInput, '75');
    await user.click(screen.getByRole('button', { name: 'Save revision' }));

    await waitFor(() => expect(accApi.reviseAccommodation).toHaveBeenCalledTimes(1));
    const [id, body] = accApi.reviseAccommodation.mock.calls[0] as [string, Record<string, unknown>];
    expect(id).toBe('acc-1');
    expect(body).toMatchObject({ extra_time_percent: 75 });
    expect(toastSuccess).toHaveBeenCalledWith('Accommodation revised — the earlier record is kept');
  });

  it('revokes an accommodation with a typed reason after confirming', async () => {
    accApi.listAccommodations.mockResolvedValue([row({ id: 'acc-1' })]);
    accApi.revokeAccommodation.mockResolvedValue({ status: 'revoked' });
    const user = userEvent.setup();
    renderSection();

    await screen.findByText('Whole applicant');
    await user.type(screen.getByPlaceholderText('Revoke reason (optional)'), 'No longer needed');
    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    await user.click(
      within(screen.getByRole('group', { name: /confirm deletion/i })).getByRole('button', {
        name: 'Revoke',
      }),
    );

    await waitFor(() =>
      expect(accApi.revokeAccommodation).toHaveBeenCalledWith('acc-1', 'No longer needed'),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Accommodation revoked');
  });
});
