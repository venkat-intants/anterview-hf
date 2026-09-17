// Tests for the HR resume-screening console (FE-2).
//
// The widest page in the staff surface and the entry point to the whole
// pipeline. Pinned here: the search is SERVER-side (a client-side filter would
// silently drop every candidate whose skills are only semantically related),
// the status filter reaches the API rather than the rendered list, and the
// per-applicant actions act on the applicant that was opened.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Applicant, ReindexResult } from '../api/applicants';

const BASE = {
  target_job_title: 'Backend Engineer',
  target_level: 'mid',
  ats_breakdown: null,
  ats_strengths: null,
  ats_concerns: null,
  ats_summary: null,
  created_at: '2026-08-01T10:00:00.000Z',
} as const;

const SCORED: Applicant = {
  ...BASE,
  id: 'ap-1',
  full_name: 'Bhavya Nair',
  email: 'bhavya@example.com',
  status: 'new',
  ats_overall: 84,
  ats_recommendation: 'strong',
};

const UNSCORED: Applicant = {
  ...BASE,
  id: 'ap-2',
  full_name: 'Chetan Iyer',
  email: null,
  status: 'shortlisted',
  ats_overall: null,
  ats_recommendation: null,
};

const NO_BACKLOG: ReindexResult = { reindexed: 0, failed: 0, remaining: 0 };

const listApplicants = vi.fn();
const getReindexStatus = vi.fn();
const reindexApplicants = vi.fn();
const updateApplicantStatus = vi.fn();
const rescoreApplicant = vi.fn();
const bulkUploadApplicants = vi.fn();
const getUploadProgress = vi.fn();
const whyMatch = vi.fn();
const listApplications = vi.fn();
vi.mock('../api/applicants', () => ({
  listApplications: (...a: unknown[]) => listApplications(...a) as unknown,
  listApplicants: (...a: unknown[]) => listApplicants(...a) as unknown,
  getReindexStatus: (...a: unknown[]) => getReindexStatus(...a) as unknown,
  reindexApplicants: (...a: unknown[]) => reindexApplicants(...a) as unknown,
  updateApplicantStatus: (...a: unknown[]) => updateApplicantStatus(...a) as unknown,
  rescoreApplicant: (...a: unknown[]) => rescoreApplicant(...a) as unknown,
  bulkUploadApplicants: (...a: unknown[]) => bulkUploadApplicants(...a) as unknown,
  getUploadProgress: (...a: unknown[]) => getUploadProgress(...a) as unknown,
  whyMatch: (...a: unknown[]) => whyMatch(...a) as unknown,
}));

// The upload form's opening picker (B5).
const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
}));

// O4 — the reject flow now needs a structured reason before it can fire.
const REJECT_REASONS = [
  { code: 'weak-fit', label: 'Not a fit for the role', applies_to: 'rejected' as const, requires_explanation: false },
];
const listDecisionReasons = vi.fn();
vi.mock('../api/scorecards', () => ({
  listDecisionReasons: (...a: unknown[]) => listDecisionReasons(...a) as unknown,
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

// The specialist panel is its own feature with its own API; stub it so these
// tests fail for screening reasons only.
vi.mock('../components/agent/CandidatePanel', () => ({
  default: () => null,
}));

import Applicants from '../pages/hr/Applicants';

// Returns the QueryClient alongside the render result. The reindex-status query
// renders NOTHING when the backlog is zero, so a test asserting its absence has
// no DOM signal to wait on — the cache is the only place that success is
// observable. See "stays out of the way…" below.
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Applicants />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

const REINDEX_STATUS_KEY = ['hr', 'applicants', 'reindex-status'];

beforeEach(() => {
  vi.clearAllMocks();
  listApplicants.mockResolvedValue([SCORED, UNSCORED]);
  getReindexStatus.mockResolvedValue(NO_BACKLOG);
  reindexApplicants.mockResolvedValue({ reindexed: 2, failed: 0, remaining: 0 });
  updateApplicantStatus.mockImplementation((id: string, status: string) =>
    Promise.resolve({ ...SCORED, id, status }),
  );
  rescoreApplicant.mockResolvedValue({ ...SCORED, ats_overall: 90 });
  listDecisionReasons.mockResolvedValue(REJECT_REASONS);
  // One application each unless a test says otherwise (B5).
  listApplications.mockImplementation((id: string) =>
    Promise.resolve([
      { enrolment_id: `en-${id}`, requisition_id: 'r1', opening_title: 'Backend Engineer',
        status: 'new', stored_status: 'new', ats_overall: 84, ats_breakdown: null,
        ats_strengths: null, ats_concerns: null, ats_recommendation: null, ats_summary: null,
        best_exam_percent: null, exam_passed: null, interview_score: null, scorecard_id: null,
        applied_at: '2026-09-01T00:00:00Z', is_latest: true },
    ]),
  );
  listRequisitions.mockResolvedValue([
    { id: 'req-9', title: 'Staff Nurse', level: 'senior', status: 'open' },
  ]);
  bulkUploadApplicants.mockResolvedValue({
    batch_id: 'batch-1', requisition_id: 'req-9', total_files: 1, accepted: 1,
    failed_count: 0, failed: [],
  });
  getUploadProgress.mockResolvedValue({
    batch_id: 'batch-1', requisition_id: 'req-9', requisition_title: 'Staff Nurse',
    uploaded_by: 'HR', total_files: 3, queued: 1, created: 1, failed: 1, being_scored: 1,
    finished: false, created_at: '2026-09-13T10:00:00Z', finished_at: null,
    failures: [{ filename: 'scan.pdf', error: 'Could not read the PDF.' }],
  });
});

describe('Applicants — bulk upload', () => {
  it('files the batch under the chosen opening — and nothing else', async () => {
    // E5: the opening is a real requisition id. A typed role is not an opening,
    // so the role, level and job description are the opening's, not the form's.
    const user = userEvent.setup();
    const { container } = renderPage();

    await screen.findByRole('option', { name: /staff nurse · senior/i });
    await user.selectOptions(screen.getByLabelText('Opening'), 'req-9');
    expect(screen.queryByLabelText('Target role')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Job description')).not.toBeInTheDocument();

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' }));
    await user.click(screen.getByRole('button', { name: /upload 1 resume/i }));

    await waitFor(() => expect(bulkUploadApplicants).toHaveBeenCalled());
    const fd = bulkUploadApplicants.mock.calls[0][0] as FormData;
    expect(fd.get('requisition_id')).toBe('req-9');
    expect(fd.get('target_job_title')).toBeNull();
  });

  it('will not upload without an opening', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();

    await screen.findByLabelText('Opening');
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' }));
    expect(screen.getByRole('button', { name: /upload 1 resume/i })).toBeDisabled();
    expect(bulkUploadApplicants).not.toHaveBeenCalled();
  });

  it('follows the accepted batch as it is read, listing every failed file', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();

    await screen.findByRole('option', { name: /staff nurse · senior/i });
    await user.selectOptions(screen.getByLabelText('Opening'), 'req-9');
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' }));
    await user.click(screen.getByRole('button', { name: /upload 1 resume/i }));

    const panel = await screen.findByTestId('upload-progress');
    expect(getUploadProgress).toHaveBeenCalledWith('batch-1');
    expect(within(panel).getByText(/Processing upload — Staff Nurse/)).toBeInTheDocument();
    expect(within(panel).getByText('2 of 3 files read')).toBeInTheDocument();
    expect(within(panel).getByText(/1 waiting · 1 added · 1 being scored/)).toBeInTheDocument();
    expect(within(panel).getByText('scan.pdf')).toBeInTheDocument();
    expect(within(panel).getByText(/You can leave this page/)).toBeInTheDocument();
  });
});

describe('Applicants — list', () => {
  it('shows each applicant with their role and ATS score', async () => {
    renderPage();

    await screen.findByText('Bhavya Nair');
    const row = screen.getByRole('button', { name: /open details for bhavya nair/i });
    expect(within(row).getByText('bhavya@example.com')).toBeInTheDocument();
    expect(within(row).getByText('84')).toBeInTheDocument();
  });

  it('says "No email" rather than leaving the cell blank', async () => {
    // The name and email are parsed out of the PDF, so a missing email is a
    // real and common outcome the operator needs to see, not a render gap.
    renderPage();

    await screen.findByText('Chetan Iyer');
    const row = screen.getByRole('button', { name: /open details for chetan iyer/i });
    expect(within(row).getByText('No email')).toBeInTheDocument();
  });

  it('counts the applicants on screen', async () => {
    renderPage();
    expect(await screen.findByText('2 applicants')).toBeInTheDocument();
  });

  it('distinguishes an empty database from an empty filter result', async () => {
    listApplicants.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('No applicants yet')).toBeInTheDocument();
    expect(screen.getByText(/upload resumes above/i)).toBeInTheDocument();
  });
});

describe('Applicants — search and filter', () => {
  it('sends the search phrase to the server, not to a client-side filter', async () => {
    // Hybrid pgvector + full-text ranking lives in data_gateway. Filtering the
    // already-fetched page here would drop every semantic match.
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.type(screen.getByLabelText(/search applicants/i), 'kubernetes');

    await waitFor(
      () => expect(listApplicants).toHaveBeenLastCalledWith({ q: 'kubernetes', status: undefined }),
      { timeout: 3000 },
    );
  });

  it('sends the status filter as a query parameter', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('tab', { name: /shortlisted/i }));

    await waitFor(() =>
      expect(listApplicants).toHaveBeenLastCalledWith({ q: undefined, status: 'shortlisted' }),
    );
  });

  it('labels results as matches while a search is active', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.type(screen.getByLabelText(/search applicants/i), 'kubernetes');

    expect(await screen.findByText(/matches$/, undefined, { timeout: 3000 })).toBeInTheDocument();
  });

  it('clears the search box and re-queries without a phrase', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Bhavya Nair');
    await user.type(screen.getByLabelText(/search applicants/i), 'kubernetes');
    await user.click(await screen.findByRole('button', { name: /clear search/i }));

    expect(screen.getByLabelText(/search applicants/i)).toHaveValue('');
    await waitFor(
      () => expect(listApplicants).toHaveBeenLastCalledWith({ q: undefined, status: undefined }),
      { timeout: 3000 },
    );
  });
});

describe('Applicants — search-index backfill', () => {
  it('stays out of the way when every resume is already indexed', async () => {
    const { client } = renderPage();

    await screen.findByText('Bhavya Nair');

    // Synchronise on the reindex-status query itself. It is a SEPARATE query
    // from the applicant list, so waiting for the list proved nothing about the
    // banner: at that moment getReindexStatus may not have resolved, and the
    // button is absent for EVERY backlog value while it is pending. The
    // assertion below therefore passed identically with remaining: 7 — the
    // exact case the next test says must render a button.
    //
    // Waiting for the mock to have been *called* would not fix it either; that
    // proves the request fired, not that its result reached the render. Cache
    // status 'success' is the first moment the component has actually seen
    // remaining: 0.
    await waitFor(() =>
      expect(client.getQueryState(REINDEX_STATUS_KEY)?.status).toBe('success'),
    );

    expect(screen.queryByRole('button', { name: /make searchable/i })).not.toBeInTheDocument();
  });

  it('warns and offers a backfill when older resumes are not searchable', async () => {
    getReindexStatus.mockResolvedValue({ reindexed: 0, failed: 0, remaining: 7 });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/7 earlier resumes aren't searchable yet/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /make searchable/i }));

    await waitFor(() => expect(reindexApplicants).toHaveBeenCalled());
    expect(toastSuccess).toHaveBeenCalledWith('All resumes are now searchable.');
  });

  it('reports honestly when a backfill leaves resumes unindexed', async () => {
    getReindexStatus.mockResolvedValue({ reindexed: 0, failed: 0, remaining: 3 });
    reindexApplicants.mockResolvedValue({ reindexed: 0, failed: 3, remaining: 3 });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: /make searchable/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'Some resumes could not be indexed — try again shortly.',
      ),
    );
  });
});

describe('Applicants — per-applicant actions', () => {
  it('acts on the applicant whose row was opened', async () => {
    const user = userEvent.setup();
    renderPage();

    // Bhavya is 'new', so Shortlist is live for her and only her.
    await user.click(
      await screen.findByRole('button', { name: /open details for bhavya nair/i }),
    );
    await user.click(await screen.findByRole('button', { name: /^shortlist$/i }));

    // With the application it is about (B5).
    await waitFor(() =>
      expect(updateApplicantStatus).toHaveBeenCalledWith('ap-1', 'shortlisted', 'en-ap-1'),
    );
  });

  it('acts per opening for someone with several applications', async () => {
    // B5: the row's status is only the latest application's, so with two
    // applications each gets its own actions and the person-level ones go.
    listApplications.mockResolvedValue([
      { enrolment_id: 'en-py', requisition_id: 'r1', opening_title: 'Python Developer',
        status: 'shortlisted', stored_status: 'shortlisted', ats_overall: 80, ats_breakdown: null,
        ats_strengths: null, ats_concerns: null, ats_recommendation: null, ats_summary: null,
        best_exam_percent: null, exam_passed: null, interview_score: null, scorecard_id: null,
        applied_at: '2026-09-01T00:00:00Z', is_latest: false },
      { enrolment_id: 'en-nu', requisition_id: 'r2', opening_title: 'Staff Nurse',
        status: 'new', stored_status: 'new', ats_overall: 40, ats_breakdown: null,
        ats_strengths: null, ats_concerns: null, ats_recommendation: null, ats_summary: null,
        best_exam_percent: null, exam_passed: null, interview_score: null, scorecard_id: null,
        applied_at: '2026-09-02T00:00:00Z', is_latest: true },
    ]);
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /open details for bhavya nair/i }),
    );
    await user.click(await screen.findByRole('button', { name: /shortlist for staff nurse/i }));
    await waitFor(() =>
      expect(updateApplicantStatus).toHaveBeenCalledWith('ap-1', 'shortlisted', 'en-nu'),
    );
    expect(screen.getByRole('button', { name: /shortlist for python developer/i })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /^shortlist$/i })).not.toBeInTheDocument();
  });

  it('disables the action a candidate is already in', async () => {
    // Chetan is already shortlisted; offering Shortlist again invites a
    // no-op write and reads as though the status had not been recorded.
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /open details for chetan iyer/i }),
    );

    expect(await screen.findByRole('button', { name: /^shortlist$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^reject$/i })).toBeEnabled();
  });

  it('re-scores the opened applicant against the role', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /open details for bhavya nair/i }),
    );
    await user.click(await screen.findByRole('button', { name: /^re-score$/i }));

    await waitFor(() => expect(rescoreApplicant).toHaveBeenCalledWith('ap-1'));
    expect(toastSuccess).toHaveBeenCalledWith('Rescored');
  });

  it('surfaces a failed status change instead of showing a stale row', async () => {
    updateApplicantStatus.mockRejectedValue(new Error('Applicant already decided'));
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /open details for bhavya nair/i }),
    );
    // Rejecting now always needs a structured reason AND free text (O4).
    await user.click(await screen.findByRole('button', { name: /^reject$/i }));
    await user.selectOptions(await screen.findByLabelText('Reason'), 'weak-fit');
    await user.type(screen.getByLabelText(/^why/i), 'Not a fit.');
    await user.click(screen.getByRole('button', { name: /confirm reject/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Applicant already decided'));
  });

  it('will not reject without a structured reason chosen', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /open details for bhavya nair/i }),
    );
    await user.click(await screen.findByRole('button', { name: /^reject$/i }));

    const confirm = await screen.findByRole('button', { name: /confirm reject/i });
    expect(confirm).toBeDisabled();
    expect(screen.getByText('Choose a reason first.')).toBeInTheDocument();
    expect(updateApplicantStatus).not.toHaveBeenCalled();
  });

  it('sends the chosen reason code and free-text reason with the reject write', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: /open details for bhavya nair/i }),
    );
    await user.click(await screen.findByRole('button', { name: /^reject$/i }));
    await user.selectOptions(await screen.findByLabelText('Reason'), 'weak-fit');
    await user.type(screen.getByLabelText(/^why/i), 'Did not meet the bar in screening.');
    await user.click(screen.getByRole('button', { name: /confirm reject/i }));

    await waitFor(() =>
      expect(updateApplicantStatus).toHaveBeenCalledWith(
        'ap-1',
        'rejected',
        'en-ap-1',
        'Did not meet the bar in screening.',
        'weak-fit',
      ),
    );
  });
});

// ---------------------------------------------------------------------------
// Every status the server sends gets a badge
// ---------------------------------------------------------------------------
// The status maps were typed to the three names in `ApplicantStatus` and indexed
// with no fallback, so held, interviewed and hired applicants rendered an EMPTY
// badge. The server sends six. `held` matters most: it is the D-05 state —
// below a round threshold, awaiting a person — and the one that must be seen.
describe('Applicants — status badges', () => {
  it.each([
    ['held', 'Held'],
    ['interviewed', 'Interviewed'],
    ['hired', 'Hired'],
  ])('labels a %s applicant rather than leaving the badge blank', async (status, label) => {
    // Cast: the exported union lists three statuses; the API returns six.
    listApplicants.mockResolvedValue([
      { ...SCORED, status: status as Applicant['status'] },
    ]);
    renderPage();

    await screen.findByText('Bhavya Nair');
    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  });

  it('shows an unknown status as its raw value, not a blank', async () => {
    listApplicants.mockResolvedValue([
      { ...SCORED, status: 'on_the_moon' as Applicant['status'] },
    ]);
    renderPage();

    await screen.findByText('Bhavya Nair');
    expect(screen.getAllByText('on_the_moon').length).toBeGreaterThan(0);
  });
});
