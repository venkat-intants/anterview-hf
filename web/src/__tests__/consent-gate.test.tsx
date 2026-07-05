// Tests for the consent gate on the JobsList page — S3-011
// Covers: consented=true skips modal, consented=false shows modal,
// I Agree -> session create, Decline -> banner, banner content.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React, { useEffect } from 'react';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { ConsentProvider } from '../context/ConsentContext';
import JobsList from '../pages/JobsList';

// ---- Module mocks ----

vi.mock('../api/jobs', () => ({
  getJobs: vi.fn().mockResolvedValue({
    items: [
      {
        id: '11111111-1111-1111-1111-111111111111',
        title: 'Junior Java Developer',
        description: 'Entry-level backend...',
        level: 'entry',
        language: 'en',
        is_active: true,
      },
    ],
    total: 1,
    page: 1,
    per_page: 20,
  }),
}));

vi.mock('../api/sessions', () => ({
  createSession: vi.fn().mockResolvedValue({ session_id: 'mock-session-abc' }),
}));

// Consent API — controlled per-test via vi.mocked() after import
vi.mock('../api/consent', () => ({
  getConsentStatus: vi.fn(),
  postConsent: vi.fn(),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// ---- Typed references to mocked functions ----
// Import after vi.mock so we get the mocked version
import * as consentApi from '../api/consent';

// ---- Test helpers ----

function TokenSetter({ children }: { children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('mock-access-token', {
      user_id: '11111111-1111-1111-1111-111111111111',
      full_name: 'Test Candidate',
      email: 'test@intants.com',
      roles: ['candidate'],
    });
  }, [setAuth]);
  return <>{children}</>;
}

function renderJobsList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <TokenSetter>
            <ConsentProvider>
              <JobsList />
            </ConsentProvider>
          </TokenSetter>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Wait for the job cards to finish loading
async function waitForJobs() {
  await waitFor(() => {
    expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
  });
}

// ---- Tests ----

describe('Consent gate on JobsList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    localStorage.clear();
  });

  // Test 6: consented=true -> skips modal, proceeds to session create
  it('skips consent modal when user has already consented', async () => {
    const user = userEvent.setup();
    vi.mocked(consentApi.getConsentStatus).mockResolvedValue({
      consented: true,
      consent_id: 'existing-consent-id',
      granted_at: '2026-05-27T10:00:00Z',
    });

    renderJobsList();
    await waitForJobs();

    const button = screen.getByRole('button', { name: /start interview for junior java developer/i });
    await user.click(button);

    // Modal should NOT appear
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Session create should be called directly
    const { createSession } = await import('../api/sessions');
    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith(
        { job_id: '11111111-1111-1111-1111-111111111111', language: 'en' },
        'mock-access-token',
      );
    });
  });

  // Test 7: consented=false -> modal appears, I Agree -> proceeds to session create
  it('shows consent modal when user has not consented, then proceeds on I Agree', async () => {
    const user = userEvent.setup();

    // First call on mount: not consented; second call after recordConsent: consented
    vi.mocked(consentApi.getConsentStatus)
      .mockResolvedValueOnce({ consented: false, consent_id: null, granted_at: null })
      .mockResolvedValue({
        consented: true,
        consent_id: 'new-consent-id',
        granted_at: new Date().toISOString(),
      });

    vi.mocked(consentApi.postConsent).mockResolvedValue({
      consented: true,
      consent_id: 'new-consent-id',
      granted_at: new Date().toISOString(),
    });

    renderJobsList();
    await waitForJobs();

    const button = screen.getByRole('button', { name: /start interview for junior java developer/i });
    await user.click(button);

    // Modal must appear
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Click I Agree
    await user.click(screen.getByRole('button', { name: /i agree/i }));

    // postConsent must have been called with the access token
    await waitFor(() => {
      expect(consentApi.postConsent).toHaveBeenCalledWith('mock-access-token');
    });

    // Modal dismissed
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    // Session create called
    const { createSession } = await import('../api/sessions');
    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith(
        { job_id: '11111111-1111-1111-1111-111111111111', language: 'en' },
        'mock-access-token',
      );
    });
  });

  // Test 3 & 8: Decline -> modal closed, navigated to /jobs, banner shown
  it('closes modal and navigates to /jobs with banner on Decline', async () => {
    const user = userEvent.setup();
    vi.mocked(consentApi.getConsentStatus).mockResolvedValue({
      consented: false,
      consent_id: null,
      granted_at: null,
    });

    renderJobsList();
    await waitForJobs();

    const button = screen.getByRole('button', { name: /start interview for junior java developer/i });
    await user.click(button);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /decline/i }));

    // Modal should be gone
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    // navigated to /jobs
    expect(mockNavigate).toHaveBeenCalledWith('/jobs');

    // Decline banner visible
    await waitFor(() => {
      expect(
        screen.getByText(/you must consent to use the interview feature/i),
      ).toBeInTheDocument();
    });
  });

  // Test 4: Esc closes the modal as Decline
  it('closes modal and shows banner when Escape is pressed', async () => {
    const user = userEvent.setup();
    vi.mocked(consentApi.getConsentStatus).mockResolvedValue({
      consented: false,
      consent_id: null,
      granted_at: null,
    });

    renderJobsList();
    await waitForJobs();

    await user.click(
      screen.getByRole('button', { name: /start interview for junior java developer/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Press Escape — focus is on "I Agree" button inside the modal
    await user.keyboard('{Escape}');

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    expect(mockNavigate).toHaveBeenCalledWith('/jobs');

    await waitFor(() => {
      expect(
        screen.getByText(/you must consent to use the interview feature/i),
      ).toBeInTheDocument();
    });
  });

  // Test 5: Focus moves to "I Agree" on mount
  it('moves focus to "I Agree" when modal opens', async () => {
    const user = userEvent.setup();
    vi.mocked(consentApi.getConsentStatus).mockResolvedValue({
      consented: false,
      consent_id: null,
      granted_at: null,
    });

    renderJobsList();
    await waitForJobs();

    await user.click(
      screen.getByRole('button', { name: /start interview for junior java developer/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /i agree/i })).toHaveFocus();
  });

  // Test 1: Required DPDP copy present in modal
  it('renders DPDP-compliant copy inside the modal', async () => {
    const user = userEvent.setup();
    vi.mocked(consentApi.getConsentStatus).mockResolvedValue({
      consented: false,
      consent_id: null,
      granted_at: null,
    });

    renderJobsList();
    await waitForJobs();

    await user.click(
      screen.getByRole('button', { name: /start interview for junior java developer/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    expect(screen.getByText(/record your voice during the session/i)).toBeInTheDocument();
    expect(screen.getByText(/english, hindi, telugu/i)).toBeInTheDocument();
    expect(screen.getByText(/90 days/i)).toBeInTheDocument();
    expect(screen.getByText(/dpdp act 2023/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /support@intants\.com/i })).toBeInTheDocument();
  });

  // Test 2: postConsent called with correct token on I Agree
  it('calls postConsent with the access token on I Agree', async () => {
    const user = userEvent.setup();

    vi.mocked(consentApi.getConsentStatus)
      .mockResolvedValueOnce({ consented: false, consent_id: null, granted_at: null })
      .mockResolvedValue({
        consented: true,
        consent_id: 'c1',
        granted_at: new Date().toISOString(),
      });

    vi.mocked(consentApi.postConsent).mockResolvedValue({
      consented: true,
      consent_id: 'c1',
      granted_at: new Date().toISOString(),
    });

    renderJobsList();
    await waitForJobs();

    await user.click(
      screen.getByRole('button', { name: /start interview for junior java developer/i }),
    );

    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /i agree/i }));

    await waitFor(() => {
      expect(consentApi.postConsent).toHaveBeenCalledWith('mock-access-token');
    });
  });
});
