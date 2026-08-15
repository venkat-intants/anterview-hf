// Tests for the admin Jobs & JD-library page (FE-2).
//
// The JD is what the role engine derives an interview's competencies from, so
// uploading one against the WRONG job silently mis-scores every interview for
// that role. The gate the page relies on is "no job selected ⇒ no upload zone",
// and the upload must carry the id of the job actually chosen — neither had a
// test.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { JobsListResponse } from '../types/interview';

const JOBS: JobsListResponse = {
  items: [
    {
      id: 'job-be',
      title: 'Backend Engineer',
      description: 'Services and APIs',
      level: 'mid',
      language: 'en',
      is_active: true,
    },
    {
      id: 'job-qa',
      title: 'QA Engineer',
      description: 'Test automation',
      level: 'entry',
      language: 'en',
      is_active: true,
    },
  ],
  total: 2,
  page: 1,
  per_page: 20,
};

const getJobs = vi.fn();
vi.mock('../api/jobs', () => ({ getJobs: (...a: unknown[]) => getJobs(...a) as unknown }));

const uploadJd = vi.fn();
vi.mock('../api/jd', () => ({ uploadJd: (...a: unknown[]) => uploadJd(...a) as unknown }));

vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => mockUseAuth() as unknown }));

import AdminJobJd from '../pages/AdminJobJd';

/**
 * Radix's Select drives its open/close off the Pointer Events API and scrolls
 * the highlighted item into view. jsdom implements neither, so without these
 * shims the listbox never opens and the test reads as "the job list is broken"
 * when the component is fine. Installed per-file rather than in setup.ts —
 * only the pages using a Radix Select need them.
 */
function shimPointerEvents(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  proto.hasPointerCapture ??= () => false;
  proto.setPointerCapture ??= () => undefined;
  proto.releasePointerCapture ??= () => undefined;
  proto.scrollIntoView ??= () => undefined;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AdminJobJd />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  shimPointerEvents();
  mockUseAuth.mockReturnValue({
    isAuthenticated: true,
    isInitializing: false,
    accessToken: 'tok-1',
    user: { user_id: 'u-1', full_name: 'Admin', email: 'admin@intants.com', roles: ['admin'] },
  });
  getJobs.mockResolvedValue(JOBS);
  uploadJd.mockResolvedValue({ message: 'ok', jd_s3_key: 'jds/job-be.pdf', text_length: 4200 });
});

describe('AdminJobJd', () => {
  it('renders the page and loads the job list', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: /jobs & jd library/i })).toBeInTheDocument();
    await waitFor(() => expect(getJobs).toHaveBeenCalled());
    expect(await screen.findByLabelText(/select job posting/i)).toBeInTheDocument();
  });

  it('withholds the upload zone until a job is chosen', async () => {
    renderPage();

    await screen.findByLabelText(/select job posting/i);
    expect(screen.getByText(/select a job above to enable jd upload/i)).toBeInTheDocument();
    expect(screen.queryByText(/jd document for/i)).not.toBeInTheDocument();
  });

  it('names the chosen job above the upload zone', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByLabelText(/select job posting/i));
    await user.click(await screen.findByRole('option', { name: 'QA Engineer' }));

    expect(await screen.findByText(/jd document for/i)).toHaveTextContent('QA Engineer');
    expect(screen.queryByText(/select a job above/i)).not.toBeInTheDocument();
  });

  it('uploads against the job that was actually selected', async () => {
    // The property the file header names first and the one nothing asserted:
    // `uploadJd` was mocked but never inspected, so the page could have sent
    // items[0] — or any hard-coded id — and every test here still passed. A JD
    // filed against the wrong job silently re-derives that role's competencies,
    // mis-scoring every interview for it, with no error anywhere.
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByLabelText(/select job posting/i));
    await user.click(await screen.findByRole('option', { name: 'QA Engineer' }));
    await screen.findByText(/jd document for/i);

    await user.upload(
      screen.getByLabelText('Job Description'),
      new File(['%PDF-1.4 jd'], 'qa-jd.pdf', { type: 'application/pdf' }),
    );

    await waitFor(() => expect(uploadJd).toHaveBeenCalled());
    // 'job-qa', not 'job-be' — the SECOND item in the list, chosen so a
    // first-item default fails this assertion instead of passing by luck.
    expect(uploadJd.mock.calls[0]?.[0]).toBe('job-qa');
  });

  it('offers a retry rather than a dead page when the job list fails', async () => {
    getJobs.mockRejectedValue(new Error('jobs service unavailable'));
    renderPage();

    // The page sets its own `retry: 1`, so the error state only appears after
    // a second failed attempt and react-query's 1s backoff.
    expect(
      await screen.findByRole('button', { name: /retry/i }, { timeout: 5000 }),
    ).toBeInTheDocument();
    // No select means no way to upload against a guessed job id.
    expect(screen.queryByLabelText(/select job posting/i)).not.toBeInTheDocument();
  });

  it('does not fetch at all without an access token', async () => {
    mockUseAuth.mockReturnValue({
      isAuthenticated: false,
      isInitializing: false,
      accessToken: null,
      user: null,
    });
    renderPage();

    expect(screen.getByRole('heading', { name: /jobs & jd library/i })).toBeInTheDocument();
    await waitFor(() => expect(getJobs).not.toHaveBeenCalled());
  });
});
