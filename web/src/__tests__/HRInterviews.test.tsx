// Tests for the HR interview-invite console (FE-2).
//
// This page mints a magic link that lets someone with NO account start a paid
// interview session. The properties worth pinning are therefore about who can
// be invited, that the once-only link is actually surfaced to be copied, and
// that revoke/reschedule are offered only where they are valid.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  EligibleApplicant,
  InterviewInvite,
  InviteResult,
} from '../api/interviewInvites';

const ELIGIBLE: EligibleApplicant[] = [
  {
    id: 'ap-1',
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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HRInterviews />
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

    await selectApplicant(user, 'ap-1');
    await user.selectOptions(screen.getByLabelText('Interview language'), 'te');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    await waitFor(() =>
      expect(createInvite).toHaveBeenCalledWith({
        applicant_id: 'ap-1',
        language: 'te',
        scheduled_at: null,
      }),
    );
  });

  it('shows the minted link once, ready to copy', async () => {
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'ap-1');
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

    await selectApplicant(user, 'ap-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));
    await user.click(await screen.findByRole('button', { name: /copy interview link/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(MINTED.magic_link));
  });

  it('surfaces an invite failure rather than implying a link exists', async () => {
    createInvite.mockRejectedValue(new Error('Applicant already has an active invite'));
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'ap-1');
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
