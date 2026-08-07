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
const whyMatch = vi.fn();
vi.mock('../api/applicants', () => ({
  listApplicants: (...a: unknown[]) => listApplicants(...a) as unknown,
  getReindexStatus: (...a: unknown[]) => getReindexStatus(...a) as unknown,
  reindexApplicants: (...a: unknown[]) => reindexApplicants(...a) as unknown,
  updateApplicantStatus: (...a: unknown[]) => updateApplicantStatus(...a) as unknown,
  rescoreApplicant: (...a: unknown[]) => rescoreApplicant(...a) as unknown,
  bulkUploadApplicants: (...a: unknown[]) => bulkUploadApplicants(...a) as unknown,
  whyMatch: (...a: unknown[]) => whyMatch(...a) as unknown,
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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Applicants />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listApplicants.mockResolvedValue([SCORED, UNSCORED]);
  getReindexStatus.mockResolvedValue(NO_BACKLOG);
  reindexApplicants.mockResolvedValue({ reindexed: 2, failed: 0, remaining: 0 });
  updateApplicantStatus.mockImplementation((id: string, status: string) =>
    Promise.resolve({ ...SCORED, id, status }),
  );
  rescoreApplicant.mockResolvedValue({ ...SCORED, ats_overall: 90 });
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
    renderPage();

    await screen.findByText('Bhavya Nair');
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

    await waitFor(() => expect(updateApplicantStatus).toHaveBeenCalledWith('ap-1', 'shortlisted'));
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
    await user.click(await screen.findByRole('button', { name: /^reject$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Applicant already decided'));
  });
});
