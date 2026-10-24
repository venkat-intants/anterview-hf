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
import type { EligibleApplicant, InterviewInvite, InviteResult } from '../api/interviewInvites';
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
  avatar_mode: 'avatar',
  status: 'invited',
  scheduled_at: null,
  expires_at: '2026-08-20T10:00:00.000Z',
  created_at: '2026-08-05T10:00:00.000Z',
  composite_score: null,
  scorecard_id: null,
  missed_slot: false,
  can_reschedule: true,
};

const COMPLETED: InterviewInvite = {
  invite_id: 'inv-2',
  applicant_id: 'ap-3',
  applicant_name: 'Deepa Menon',
  job_title: 'Backend Engineer',
  language: 'en',
  avatar_mode: 'avatar',
  status: 'completed',
  scheduled_at: '2026-08-04T09:30:00.000Z',
  expires_at: '2026-08-20T10:00:00.000Z',
  created_at: '2026-08-01T10:00:00.000Z',
  composite_score: 8.42,
  scorecard_id: 'sc-9',
  missed_slot: false,
  can_reschedule: false,
};

// A live invite with a scheduled time — for the reschedule pre-fill bug.
const SCHEDULED: InterviewInvite = {
  invite_id: 'inv-4',
  applicant_id: 'ap-4',
  applicant_name: 'Esha Kapoor',
  job_title: 'Backend Engineer',
  language: 'en',
  avatar_mode: 'avatar',
  status: 'invited',
  scheduled_at: '2026-09-20T04:15:00.000Z',
  expires_at: '2026-10-20T10:00:00.000Z',
  created_at: '2026-09-01T10:00:00.000Z',
  composite_score: null,
  scorecard_id: null,
  missed_slot: false,
  can_reschedule: true,
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
const endStartedInterview = vi.fn();
const rescheduleInvite = vi.fn();
const setInviteAvatarMode = vi.fn();
const getInviteResult = vi.fn();
vi.mock('../api/interviewInvites', () => ({
  getInviteResult: (...a: unknown[]) => getInviteResult(...a) as unknown,
  listEligibleApplicants: (...a: unknown[]) => listEligibleApplicants(...a) as unknown,
  listInvites: (...a: unknown[]) => listInvites(...a) as unknown,
  createInvite: (...a: unknown[]) => createInvite(...a) as unknown,
  revokeInvite: (...a: unknown[]) => revokeInvite(...a) as unknown,
  endStartedInterview: (...a: unknown[]) => endStartedInterview(...a) as unknown,
  rescheduleInvite: (...a: unknown[]) => rescheduleInvite(...a) as unknown,
  setInviteAvatarMode: (...a: unknown[]) => setInviteAvatarMode(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
const toastInfo = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: (...a: unknown[]) => toastInfo(...a) as unknown,
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
  await user.selectOptions(screen.getByLabelText('Candidate'), id);
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
  endStartedInterview.mockResolvedValue({ ...INVITED, status: 'revoked' });
  rescheduleInvite.mockResolvedValue(INVITED);
});

describe('HRInterviews - invite form', () => {
  it('offers only eligible applicants, flagging who already has a live link', async () => {
    renderPage();

    // The <select> renders before its options do, so wait on an OPTION.
    await screen.findByRole('option', { name: /bhavya nair - exam passed/i });
    const select = screen.getByLabelText('Candidate');
    // Inviting someone twice mints a second paid session — say so in the list.
    expect(
      within(select).getByRole('option', {
        name: /chetan iyer - shortlisted \(already invited\)/i,
      }),
    ).toBeInTheDocument();
  });

  it('cannot generate a link until an applicant is chosen', async () => {
    renderPage();

    await screen.findByLabelText('Candidate');
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
        // Defaults to the avatar, so nothing about an existing invite changes
        // unless someone deliberately switches it.
        avatar_mode: 'avatar',
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
    await user.selectOptions(screen.getByLabelText('Candidate'), 'en-9');
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

describe('HRInterviews - invite list', () => {
  it("opens a completed interview's result inside the console and shows the score", async () => {
    // It used to link to /scorecard/:id, the candidate-facing page outside the
    // HR shell, with candidate copy and no way back to this list.
    getInviteResult.mockResolvedValue({
      invite_id: 'inv-2',
      applicant_id: 'ap-3',
      applicant_name: 'Deepa Menon',
      status: 'completed',
      session_status: 'completed',
      scorecard_id: 'sc-9',
      composite_score: 8.42,
      scores: { communication: 8.1, technical: 8.6 },
      strengths: ['Clear explanations'],
      improvements: [{ area: 'Depth', suggestion: 'Go one level deeper' }],
      summary: 'A strong, structured interview.',
    });
    const user = userEvent.setup();
    renderPage();

    const toggle = await screen.findByRole('button', { name: /show result for deepa menon/i });
    expect(screen.getByLabelText('Score: 8.4 out of 10')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /scorecard/i })).not.toBeInTheDocument();
    await user.click(toggle);

    const panel = await screen.findByRole('region', { name: /interview result for deepa menon/i });
    expect(await within(panel).findByText('A strong, structured interview.')).toBeInTheDocument();
    expect(within(panel).getByText('Overall 8.4/10')).toBeInTheDocument();
    expect(within(panel).getByText('Depth: Go one level deeper')).toBeInTheDocument();
    expect(getInviteResult).toHaveBeenCalledWith('inv-2');
  });

  it('shows the interview language instead of a result link while none exists', async () => {
    renderPage();

    await screen.findByText('Bhavya Nair');
    // Scoped to the row: the same label is also a language option in the form.
    expect(within(rowFor('Bhavya Nair')).getByText('हिंदी')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /result for bhavya nair/i }),
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
      const input = screen.getByLabelText<HTMLInputElement>(/new interview time/i);

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
    await user.click(
      screen.getByRole('button', { name: /revoke interview link for bhavya nair/i }),
    );
    // One click only arms it: the link stops working for good.
    expect(revokeInvite).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Revoke link' }));

    await waitFor(() => expect(revokeInvite).toHaveBeenCalledWith('inv-1'));
    expect(toastSuccess).toHaveBeenCalledWith('Link revoked');
  });

  it('filters the list by status without refetching', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('button', { name: /^completed$/i }));

    expect(screen.getByText('Deepa Menon')).toBeInTheDocument();
    expect(screen.queryByText('Bhavya Nair')).not.toBeInTheDocument();
    // The filter is client-side over one fetch — no extra network round trip.
    expect(listInvites).toHaveBeenCalledTimes(1);
  });

  it('distinguishes "nothing yet" from "nothing matches this filter"', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('button', { name: /^expired$/i }));
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

describe('HRInterviews - interview citation deep link', () => {
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
    // The human-interview sessions live in the drawer section of this name.
    expect(screen.getByText(/interview schedule/i)).toBeInTheDocument();
  });

  // ── Avatar vs audio ────────────────────────────────────────────────────
  //
  // The avatar is the only per-minute cost in a session, so whether a
  // candidate meets a face or a voice is a budget call — and on a weak
  // connection an accessibility one. It used to be a deployment-wide env var,
  // which put it with whoever holds the deploy key rather than whoever is
  // running the hiring.

  it('sends the chosen mode when minting a link', async () => {
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.selectOptions(screen.getByLabelText('Interview mode'), 'audio');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    await waitFor(() =>
      expect(createInvite).toHaveBeenCalledWith(expect.objectContaining({ avatar_mode: 'audio' })),
    );
  });

  it('lets the mode be changed on an invite nobody chose one for', async () => {
    // THE CASE THAT MATTERS. Most invites are minted by the workflow with no
    // human in the loop, so a create-only control would be unreachable for the
    // majority of interviews.
    setInviteAvatarMode.mockResolvedValue({ ...INVITED, avatar_mode: 'audio' });
    const user = userEvent.setup();
    renderPage();

    const row = await screen.findByText('Bhavya Nair');
    const control = within(row.closest('div[id^="interview-"]') as HTMLElement).getByLabelText(
      'Interview mode for Bhavya Nair',
    );
    await user.selectOptions(control, 'audio');

    await waitFor(() => expect(setInviteAvatarMode).toHaveBeenCalledWith('inv-1', 'audio'));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(expect.stringMatching(/audio-only/i)),
    );
  });

  it('shows the mode as read-only once the interview has started', async () => {
    // The server 409s after the candidate starts, because the mode is copied
    // onto the session at that point. Offering a live control that cannot work
    // is worse than offering none.
    const user = userEvent.setup();
    renderPage();

    const row = await screen.findByText('Deepa Menon');
    const scope = within(row.closest('div[id^="interview-"]') as HTMLElement);
    expect(scope.queryByLabelText('Interview mode for Deepa Menon')).not.toBeInTheDocument();
    expect(scope.getByText('Video avatar')).toBeInTheDocument();
    expect(setInviteAvatarMode).not.toHaveBeenCalled();
    void user;
  });

  it("surfaces the server's refusal rather than a generic failure", async () => {
    setInviteAvatarMode.mockRejectedValue(
      new Error(
        'This interview is already consumed - the mode is fixed once the candidate starts.',
      ),
    );
    const user = userEvent.setup();
    renderPage();

    const row = await screen.findByText('Bhavya Nair');
    const control = within(row.closest('div[id^="interview-"]') as HTMLElement).getByLabelText(
      'Interview mode for Bhavya Nair',
    );
    await user.selectOptions(control, 'audio');

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/already consumed/i)),
    );
  });
});

// ---------------------------------------------------------------------------
// Audit wave 2 (interviews): replacing a live link, failed loads, the join
// window, missed slots and per-row pending state.
// ---------------------------------------------------------------------------

describe('HRInterviews - replacing a live link (IV-H2)', () => {
  it('asks before replacing a live link, and only then sends replace_existing', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole('option', { name: /chetan iyer/i });
    await user.selectOptions(screen.getByLabelText('Candidate'), 'en-2');
    expect(
      screen.queryByRole('button', { name: /generate interview link/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/already has an active interview link/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Replace interview link' }));
    expect(createInvite).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Replace link and send' }));

    await waitFor(() =>
      expect(createInvite).toHaveBeenCalledWith(
        expect.objectContaining({
          applicant_id: 'ap-2',
          enrolment_id: 'en-2',
          replace_existing: true,
        }),
      ),
    );
  });

  it('offers no replacement for a started interview, and says to end it first', async () => {
    // Re-audit: a started interview was shown as "already invited" with a
    // Replace button the server always refuses (409).
    listEligibleApplicants.mockResolvedValue([
      ELIGIBLE[0],
      { ...ELIGIBLE[1], has_active_invite: true, has_started_invite: true },
    ]);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole('option', { name: /chetan iyer .*\(interview in progress\)/i });
    await user.selectOptions(screen.getByLabelText('Candidate'), 'en-2');
    expect(screen.getByText(/has already started this interview/i)).toBeInTheDocument();
    expect(screen.getByText(/end the started interview/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Replace interview link' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /generate interview link/i }),
    ).not.toBeInTheDocument();
  });

  it('never sends replace_existing for someone with no live link', async () => {
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));
    await waitFor(() => expect(createInvite).toHaveBeenCalled());
    expect(createInvite.mock.calls[0][0]).not.toHaveProperty('replace_existing');
  });

  it('says so when the server answered with the link it already sent', async () => {
    createInvite.mockResolvedValue({ ...MINTED, magic_link: '', reused: true });
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));

    // A short title; the explanation goes in the description.
    await waitFor(() =>
      expect(toastInfo).toHaveBeenCalledWith(
        'Bhavya Nair already has this link',
        expect.objectContaining({
          description: expect.stringMatching(/no new email was sent/i) as unknown,
        }),
      ),
    );
    expect(screen.queryByLabelText('Magic interview link')).not.toBeInTheDocument();
    expect(toastSuccess).not.toHaveBeenCalledWith('Interview link created');
  });

  it('tells HR the link was emailed, not that they must deliver it', async () => {
    const user = userEvent.setup();
    renderPage();

    await selectApplicant(user, 'en-1');
    await user.click(screen.getByRole('button', { name: /generate interview link/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/we have emailed this link/i);
  });
});

describe('HRInterviews - failed loads (IV-M3)', () => {
  it('shows a failed invite list as an error with a retry, not as "no interviews yet"', async () => {
    listInvites.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('Could not load interviews.')).toBeInTheDocument();
    expect(screen.queryByText(/no interviews yet/i)).not.toBeInTheDocument();
    listInvites.mockResolvedValue([INVITED]);
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Bhavya Nair')).toBeInTheDocument();
  });

  it('shows a failed applicant list as an error, not as an empty dropdown', async () => {
    listEligibleApplicants.mockRejectedValue(new Error('boom'));
    renderPage();

    expect(await screen.findByText('Could not load eligible candidates.')).toBeInTheDocument();
  });
});

describe('HRInterviews - time, window and expiry (IV-H1, IV-M1, IV-M10)', () => {
  it('states the 10-minute join window and the zone HR is typing in', async () => {
    renderPage();

    const input = await screen.findByLabelText(/interview time \(optional, your time/i);
    expect(input).toHaveAttribute('min');
    expect(screen.getByText(/can start within 10 minutes/i)).toBeInTheDocument();
    expect(screen.queryByText(/link works any time/i)).not.toBeInTheDocument();
  });

  it('shows the scheduled time with its year and zone, and when the link expires', async () => {
    listInvites.mockResolvedValue([SCHEDULED]);
    renderPage();

    const row = (await screen.findByText('Esha Kapoor')).closest(
      'div[id^="interview-"]',
    ) as HTMLElement;
    // formatDateTime always prints the year and the zone.
    expect(within(row).getAllByText(/2026/).length).toBeGreaterThanOrEqual(2);
    expect(within(row).getByText(/link expires/i)).toBeInTheDocument();
  });

  it('flags a missed join window instead of reading "Invited"', async () => {
    listInvites.mockResolvedValue([{ ...SCHEDULED, missed_slot: true }]);
    renderPage();

    const row = (await screen.findByText('Esha Kapoor')).closest(
      'div[id^="interview-"]',
    ) as HTMLElement;
    expect(within(row).getByText('Missed slot')).toBeInTheDocument();
    expect(within(row).queryByText('Invited')).not.toBeInTheDocument();
    expect(
      within(row).getByRole('button', { name: /reschedule interview for esha kapoor/i }),
    ).toBeInTheDocument();
  });

  it('offers neither reschedule nor revoke on an interview in progress (IV-M4, IV-M12)', async () => {
    listInvites.mockResolvedValue([{ ...INVITED, status: 'consumed', can_reschedule: false }]);
    renderPage();

    await screen.findByText('Bhavya Nair');
    expect(
      screen.queryByRole('button', { name: /reschedule interview for bhavya nair/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /revoke interview link for bhavya nair/i }),
    ).not.toBeInTheDocument();
  });

  it('ends a started interview only with a reason and a confirm', async () => {
    listInvites.mockResolvedValue([{ ...INVITED, status: 'consumed', can_reschedule: false }]);
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /end started interview for bhavya nair/i }),
    );
    const reason = screen.getByLabelText('Why are you ending this interview?');
    expect(reason).toHaveFocus();
    // No reason, no way to confirm: the server requires one too.
    expect(screen.queryByRole('button', { name: 'End interview' })).not.toBeInTheDocument();

    await user.selectOptions(reason, 'candidate_left');
    await user.click(screen.getByRole('button', { name: 'End interview' }));
    // One click only arms it.
    expect(endStartedInterview).not.toHaveBeenCalled();
    const group = screen.getByRole('group', { name: /end the started interview for bhavya nair/i });
    await user.click(within(group).getByRole('button', { name: 'End interview' }));

    await waitFor(() =>
      expect(endStartedInterview).toHaveBeenCalledWith('inv-1', 'candidate_left'),
    );
    expect(revokeInvite).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Interview ended for Bhavya Nair', {
        description: 'You can now send a new link.',
      }),
    );
  });

  it('offers no way to end an interview that is not in progress', async () => {
    renderPage();

    await screen.findByText('Bhavya Nair');
    expect(
      screen.queryByRole('button', { name: /end started interview/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the server's refusal when the interview has already finished", async () => {
    listInvites.mockResolvedValue([{ ...INVITED, status: 'consumed', can_reschedule: false }]);
    endStartedInterview.mockRejectedValue(
      new Error('This interview has finished, so there is nothing left to end.'),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /end started interview for bhavya nair/i }),
    );
    await user.selectOptions(
      screen.getByLabelText('Why are you ending this interview?'),
      'technical_problem',
    );
    await user.click(screen.getByRole('button', { name: 'End interview' }));
    const group = screen.getByRole('group', { name: /end the started interview/i });
    await user.click(within(group).getByRole('button', { name: 'End interview' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/finished/i)),
    );
    // The form stays open with what HR typed.
    expect(screen.getByLabelText('Why are you ending this interview?')).toHaveValue(
      'technical_problem',
    );
  });
});

describe('HRInterviews - inline reschedule (IV-L2, IV-L14)', () => {
  it('keeps the form open when the server refuses, and shows the reason', async () => {
    rescheduleInvite.mockRejectedValue(new Error('The new interview time cannot be in the past.'));
    listInvites.mockResolvedValue([SCHEDULED]);
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /reschedule interview for esha kapoor/i }),
    );
    const input = screen.getByLabelText<HTMLInputElement>(/new interview time/i);
    // Focus moves into the form that just opened.
    expect(input).toHaveFocus();
    await user.click(screen.getByRole('button', { name: /save new time for esha kapoor/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('The new interview time cannot be in the past.'),
    );
    expect(screen.getByLabelText(/new interview time/i)).toBeInTheDocument();
  });

  it('closes the form once the new time is saved', async () => {
    listInvites.mockResolvedValue([SCHEDULED]);
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /reschedule interview for esha kapoor/i }),
    );
    await user.click(screen.getByRole('button', { name: /save new time for esha kapoor/i }));
    await waitFor(() =>
      expect(screen.queryByLabelText(/new interview time/i)).not.toBeInTheDocument(),
    );
  });
});

describe('HRInterviews - per-row state (IV-L3, IV-L5)', () => {
  it('shows the new mode at once and leaves other rows usable while it saves', async () => {
    setInviteAvatarMode.mockReturnValue(new Promise(() => undefined));
    listInvites.mockResolvedValue([INVITED, SCHEDULED]);
    const user = userEvent.setup();
    renderPage();

    const mine = await screen.findByLabelText<HTMLSelectElement>('Interview mode for Bhavya Nair');
    await user.selectOptions(mine, 'audio');

    await waitFor(() => expect(mine.value).toBe('audio'));
    expect(mine).toBeDisabled();
    expect(screen.getByLabelText('Interview mode for Esha Kapoor')).not.toBeDisabled();
  });

  it('lists a missed slot under its own filter, not under Invited', async () => {
    // Re-audit: the filter matched the stored status, so "Invited" listed rows
    // whose pill said "Missed slot" and nothing isolated the missed ones.
    listInvites.mockResolvedValue([INVITED, { ...SCHEDULED, missed_slot: true }]);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Esha Kapoor');
    await user.click(screen.getByRole('button', { name: /^invited$/i }));
    expect(screen.getByText('Bhavya Nair')).toBeInTheDocument();
    expect(screen.queryByText('Esha Kapoor')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^missed slot$/i }));
    expect(screen.getByText('Esha Kapoor')).toBeInTheDocument();
    expect(screen.queryByText('Bhavya Nair')).not.toBeInTheDocument();
  });

  it('counts what the filter shows next to the total', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('Interviews (2)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^completed$/i }));
    expect(screen.getByText('Interviews (1 of 2)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^completed$/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});
