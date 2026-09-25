// Tests for the HR interview-invite console (FE-2).
//
// This page mints a magic link that lets someone with NO account start a paid
// interview session. The properties worth pinning are therefore about who can
// be invited, that the once-only link is actually surfaced to be copied, and
// that revoke/reschedule are offered only where they are valid.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  EligibleApplicant,
  InterviewInvite,
  InviteResult,
} from '../api/interviewInvites';
import { toLocalInputValue } from '../lib/localDatetime';

const ELIGIBLE: EligibleApplicant[] = [
  {
    id: 'ap-1',
    enrolment_id: 'en-1',
    opening_title: 'Backend Engineer',
    full_name: 'Bhavya Nair',
    target_job_title: 'Backend Engineer',
    target_level: 'mid',
    status: 'shortlisted',
    ats_overall: 84,
    passed_exam: true,
    has_active_invite: false,
  },
  {
    id: 'ap-2',
    enrolment_id: 'en-2',
    opening_title: 'Backend Engineer',
    full_name: 'Chetan Iyer',
    target_job_title: 'Backend Engineer',
    target_level: 'junior',
    status: 'shortlisted',
    ats_overall: 71,
    passed_exam: false,
    has_active_invite: true,
  },
];

const INVITED: InterviewInvite = {
  invite_id: 'inv-1',
  applicant_id: 'ap-1',
  applicant_name: 'Bhavya Nair',
  job_title: 'Backend Engineer',
  language: 'hi',
  status: 'invited',
  scheduled_at: null,
  expires_at: '2026-08-20T10:00:00.000Z',
  created_at: '2026-08-05T10:00:00.000Z',
  composite_score: null,
  scorecard_id: null,
};

const COMPLETED: InterviewInvite = {
  invite_id: 'inv-2',
  applicant_id: 'ap-3',
  applicant_name: 'Deepa Menon',
  job_title: 'Backend Engineer',
  language: 'en',
  status: 'completed',
  scheduled_at: '2026-08-04T09:30:00.000Z',
  expires_at: '2026-08-20T10:00:00.000Z',
  created_at: '2026-08-01T10:00:00.000Z',
  composite_score: 8.42,
  scorecard_id: 'sc-9',
};

// A live invite with a scheduled time — for the reschedule pre-fill bug.
const SCHEDULED: InterviewInvite = {
  invite_id: 'inv-4',
  applicant_id: 'ap-4',
  applicant_name: 'Esha Kapoor',
  job_title: 'Backend Engineer',
  language: 'en',
  status: 'invited',
  scheduled_at: '2026-09-20T04:15:00.000Z',
  expires_at: '2026-10-20T10:00:00.000Z',
  created_at: '2026-09-01T10:00:00.000Z',
  composite_score: null,
  scorecard_id: null,
};

const MINTED: InviteResult = {
  invite_id: 'inv-3',
  applicant_id: 'ap-1',
  applicant_name: 'Bhavya Nair',
  job_title: 'Backend Engineer',
  magic_link: 'https://app.test/i/tok_secret_123',
  expires_at: '2026-08-20T10:00:00.000Z',
  scheduled_at: null,
  status: 'invited',
};

const listEligibleApplicants = vi.fn();
const listInvites = vi.fn();
const createInvite = vi.fn();
const revokeInvite = vi.fn();
const rescheduleInvite = vi.fn();
vi.mock('../api/interviewInvites', () => ({
  listEligibleApplicants: (...a: unknown[]) => listEligibleApplicants(...a) as unknown,
  listInvites: (...a: unknown[]) => listInvites(...a) as unknown,
  createInvite: (...a: unknown[]) => createInvite(...a) as unknown,
  revokeInvite: (...a: unknown[]) => revokeInvite(...a) as unknown,
  rescheduleInvite: (...a: unknown[]) => rescheduleInvite(...a) as unknown,
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

import HRInterviews from '../pages/hr/HRInterviews';

/** The grid row for one invite (name → flex wrapper → grid). */
function rowFor(name: string): HTMLElement {
  return screen.getByText(name).parentElement!.parentElement!;
}

/**
 * Pick an applicant once the eligible-list query has populated the <select>.
 * Selecting before then silently no-ops and the assertion that follows is
 * testing an empty form.
 */
async function selectApplicant(
  user: ReturnType<typeof userEvent.setup>,
  id: string,
): Promise<void> {
  await screen.findByRole('option', { name: /bhavya nair/i });
  await user.selectOptions(screen.getByLabelText('Applicant'), id);
}

/**
 * @param path Mounted as App.tsx mounts it, so `/hr/interviews/{id}` — the
 *             copilot's interview-citation route — drives the same component
 *             through a real `:interviewId` param.
 */
function renderPage(path = '/hr/interviews') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/hr/interviews" element={<HRInterviews />} />
          <Route path="/hr/interviews/:interviewId" element={<HRInterviews />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listEligibleApplicants.mockResolvedValue(ELIGIBLE);
  listInvites.mockResolvedValue([INVITED, COMPLETED]);
  createInvite.mockResolvedValue(MINTED);
  revokeInvite.mockResolvedValue({ ...INVITED, status: 'revoked' });
  rescheduleInvite.mockResolvedValue(INVITED);
});

describe('HRInterviews — invite form', () => {
  it('offers only eligible applicants, flagging who already has a live link', async () => {
    renderPage();

    // The <select> renders before its options do, so wait on an OPTION.
    await screen.findByRole('option', { name: /bhavya nair — exam passed/i });
    const select = screen.getByLabelText('Applicant');
    // Inviting someone twice mints a second paid session — say so in the list.
    expect(
      within(select).getByRole('option', { name: /chetan iyer — shortlisted \(already invited\)/i }),
    ).toBeInTheDocument();
  });

  it('cannot generate a link until an applicant is chosen', async () => {
    renderPage();

    await screen.findByLabelText('Applicant');
    expect(screen.getByRole('button', { name: /generate interview link/i })).toBeDisabled();
  });

  it('sends the chosen applicant and language, with no schedule when none is set', async () => {
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.selectOptions(screen.getByLabelText('Interview language'), 'te');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    await waitFor(() =>
      expect(createInvite).toHaveBeenCalledWith({
        applicant_id: 'ap-1',
        // B5: the interview is for one application, and says which.
        enrolment_id: 'en-1',
        language: 'te',
        scheduled_at: null,
      }),
    );
  });

  it('offers a person once per opening, and invites for the one chosen', async () => {
    // B5: Bhavya is eligible in two openings. Each is its own entry, and the
    // invite carries the application picked — not just the person.
    listEligibleApplicants.mockResolvedValue([
      ...ELIGIBLE,
      { ...ELIGIBLE[0], enrolment_id: 'en-9', opening_title: 'Data Engineer' },
    ]);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole('option', { name: /bhavya nair .* for data engineer/i });
    expect(screen.getAllByRole('option', { name: /bhavya nair/i })).toHaveLength(2);
    await user.selectOptions(screen.getByLabelText('Applicant'), 'en-9');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    await waitFor(() =>
      expect(createInvite).toHaveBeenCalledWith(
        expect.objectContaining({ applicant_id: 'ap-1', enrolment_id: 'en-9' }),
      ),
    );
  });

  it('shows the minted link once, ready to copy', async () => {
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    const field = await screen.findByLabelText('Magic interview link');
    expect(field).toHaveValue('https://app.test/i/tok_secret_123');
    expect(screen.getByRole('alert')).toHaveTextContent(/shown once/i);
  });

  it('copies the link to the clipboard on request', async () => {
    const user = userEvent.setup();
    // Installed AFTER setup(): user-event v14 swaps in its own clipboard stub,
    // which would otherwise replace this spy and make the assertion vacuous.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));
    await user.click(await screen.findByRole('button', { name: /copy interview link/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(MINTED.magic_link));
  });

  it('surfaces an invite failure rather than implying a link exists', async () => {
    createInvite.mockRejectedValue(new Error('Applicant already has an active invite'));
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Applicant already has an active invite'),
    );
    expect(screen.queryByLabelText('Magic interview link')).not.toBeInTheDocument();
  });
});

describe('HRInterviews — invite list', () => {
  it('links a completed interview to its scorecard and shows the score', async () => {
    renderPage();

    const link = await screen.findByRole('link', { name: /view scorecard for deepa menon/i });
    expect(link).toHaveAttribute('href', '/scorecard/sc-9');
    expect(screen.getByLabelText('Score: 8.4 out of 10')).toBeInTheDocument();
  });

  it('shows the interview language instead of a result link while none exists', async () => {
    renderPage();

    await screen.findByText('Bhavya Nair');
    // Scoped to the row: the same label is also a language option in the form.
    expect(within(rowFor('Bhavya Nair')).getByText('हिंदी')).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: /view scorecard for bhavya nair/i }),
    ).not.toBeInTheDocument();
  });

  it('offers reschedule only while the interview can still be moved', async () => {
    renderPage();

    await screen.findByText('Bhavya Nair');
    expect(
      screen.getByRole('button', { name: /reschedule interview for bhavya nair/i }),
    ).toBeInTheDocument();
    // A completed interview cannot be rescheduled.
    expect(
      screen.queryByRole('button', { name: /reschedule interview for deepa menon/i }),
    ).not.toBeInTheDocument();
  });

  it('pre-fills the reschedule time as LOCAL wall time, not a UTC time shown as if it were local', async () => {
    // The bug: `new Date(iso).toISOString().slice(0, 16)` is always UTC — shown
    // in a datetime-local input, it silently displays a UTC clock reading as
    // though it were the reader's own, off by their UTC offset. Forcing a
    // non-UTC zone makes the two disagree, so this fails against the old code
    // in any timezone, including a CI runner that happens to run in UTC.
    const originalTz = process.env.TZ;
    process.env.TZ = 'America/New_York';
    try {
      listInvites.mockResolvedValue([SCHEDULED]);
      const user = userEvent.setup();
      renderPage();

      await user.click(
        await screen.findByRole('button', { name: /reschedule interview for esha kapoor/i }),
      );
      const input = screen.getByLabelText<HTMLInputElement>('New scheduled time');

      expect(input.value).toBe(toLocalInputValue(SCHEDULED.scheduled_at));
      expect(input.value).not.toBe(
        new Date(SCHEDULED.scheduled_at as string).toISOString().slice(0, 16),
      );
    } finally {
      // Assigning `undefined` to process.env.TZ sets the literal string
      // "undefined" (Node coerces env values to strings) rather than
      // unsetting it — which then broke every later Intl.DateTimeFormat call
      // in this worker for the rest of the run. Delete when there was
      // nothing to restore.
      if (originalTz === undefined) delete process.env.TZ;
      else process.env.TZ = originalTz;
    }
  });

  it('revokes the link for the row that was acted on', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('button', { name: /revoke interview link for bhavya nair/i }));

    await waitFor(() => expect(revokeInvite).toHaveBeenCalledWith('inv-1'));
    expect(toastSuccess).toHaveBeenCalledWith('Link revoked');
  });

  it('filters the list by status without refetching', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('tab', { name: /completed/i }));

    expect(screen.getByText('Deepa Menon')).toBeInTheDocument();
    expect(screen.queryByText('Bhavya Nair')).not.toBeInTheDocument();
    // The filter is client-side over one fetch — no extra network round trip.
    expect(listInvites).toHaveBeenCalledTimes(1);
  });

  it('distinguishes "nothing yet" from "nothing matches this filter"', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('tab', { name: /^expired$/i }));
    expect(screen.getByText(/no interviews match this filter/i)).toBeInTheDocument();
    expect(screen.queryByText(/no interviews yet/i)).not.toBeInTheDocument();
  });

  it('prompts for a first invite when there are none at all', async () => {
    listInvites.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/no interviews yet/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// An interview citation lands on the interview it names
//
// PH5-E1: CITATION_ROUTES.interview is `/hr/interviews/{id}`, and App.tsx
// mounted only the list — so every interview chip opened the 404 page. This list
// IS the screen for an AI interview, so the citation focuses the row.
// ---------------------------------------------------------------------------

describe('HRInterviews — interview citation deep link', () => {
  it('focuses the cited invite row', async () => {
    renderPage(`/hr/interviews/${COMPLETED.invite_id}`);

    await screen.findByText('Deepa Menon');
    // Focused, not merely scrolled to: a citation followed by keyboard has to
    // land on the record as well.
    await waitFor(() =>
      expect(document.activeElement?.id).toBe(`interview-${COMPLETED.invite_id}`),
    );
    expect(screen.queryByText(/not in this list/i)).not.toBeInTheDocument();
  });

  it('focuses nothing on the plain list route', async () => {
    renderPage();
    await screen.findByText('Bhavya Nair');
    expect(document.activeElement?.id ?? '').not.toContain('interview-');
  });

  it('says where else to look for an id this list does not hold', async () => {
    // A human panel interview is cited with the same `interview` kind but lives
    // on the candidate's own record — an interview_sessions id is not an invite
    // id, and silently highlighting nothing would read as "no such interview".
    renderPage('/hr/interviews/00000000-0000-4000-8000-0000000000ff');

    expect(await screen.findByText(/not in this list/i)).toBeInTheDocument();
    expect(screen.getByText(/human interview/i)).toBeInTheDocument();
  });
});
