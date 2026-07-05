// Tests for JobsList page
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { ConsentProvider } from '../context/ConsentContext';
import JobsList from '../pages/JobsList';
import { useEffect } from 'react';

// Mock the jobs + sessions API modules
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
      {
        id: '22222222-2222-2222-2222-222222222222',
        title: 'Sales Associate',
        description: 'Customer-facing...',
        level: 'entry',
        language: 'en',
        is_active: true,
      },
      {
        id: '33333333-3333-3333-3333-333333333333',
        title: 'Data Entry Operator',
        description: 'Accuracy-focused...',
        level: 'entry',
        language: 'en',
        is_active: true,
      },
    ],
    total: 3,
    page: 1,
    per_page: 20,
  }),
}));

vi.mock('../api/sessions', () => ({
  createSession: vi.fn().mockResolvedValue({ session_id: 'mock-session-abc' }),
}));

// Consent API — default to already-consented so the session flow is unblocked
vi.mock('../api/consent', () => ({
  getConsentStatus: vi.fn().mockResolvedValue({
    consented: true,
    consent_id: 'existing-id',
    granted_at: '2026-05-27T10:00:00Z',
  }),
  postConsent: vi.fn().mockResolvedValue({
    consented: true,
    consent_id: 'new-id',
    granted_at: '2026-05-27T10:00:00Z',
  }),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

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
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

describe('JobsList page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('renders all 3 job cards after loading', async () => {
    renderJobsList();

    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    expect(screen.getByText('Sales Associate')).toBeInTheDocument();
    expect(screen.getByText('Data Entry Operator')).toBeInTheDocument();
  });

  it('renders a "Start Interview" button for each job', async () => {
    renderJobsList();
    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    const buttons = screen.getAllByRole('button', { name: /start interview/i });
    expect(buttons).toHaveLength(3);
  });

  it('shows loading skeletons while fetching', () => {
    renderJobsList();
    // Skeletons are aria-hidden — query by animate-pulse class presence instead
    const { container } = renderJobsList();
    const skeletons = container.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('calls createSession and navigates to interview route on "Start Interview" click', async () => {
    const user = userEvent.setup();
    renderJobsList();

    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    const firstButton = screen.getAllByRole('button', { name: /start interview/i })[0];
    await user.click(firstButton);

    const { createSession } = await import('../api/sessions');
    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith(
        { job_id: '11111111-1111-1111-1111-111111111111', language: 'en' },
        'mock-access-token',
      );
    });

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/interview/mock-session-abc');
    });
  });

  // S4-002: Language picker tests

  it('defaults to "en" when no localStorage entry exists', async () => {
    // localStorage already cleared in beforeEach
    renderJobsList();

    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    const select = screen.getByRole('combobox', { name: /interview language/i });
    expect(select).toHaveValue('en');
  });

  it('persists language selection to localStorage and restores on re-render', async () => {
    const user = userEvent.setup();
    const { unmount } = renderJobsList();

    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    // Change to Hindi
    const select = screen.getByRole('combobox', { name: /interview language/i });
    await user.selectOptions(select, 'hi');

    expect(localStorage.getItem('intants:interview-language')).toBe('hi');

    // Unmount and re-render to simulate page revisit
    unmount();
    renderJobsList();

    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    const restoredSelect = screen.getByRole('combobox', { name: /interview language/i });
    expect(restoredSelect).toHaveValue('hi');
  });

  it('passes selectedLanguage to createSession on Start Interview click', async () => {
    const user = userEvent.setup();
    renderJobsList();

    await waitFor(() => {
      expect(screen.getByText('Junior Java Developer')).toBeInTheDocument();
    });

    // Switch to Telugu
    const select = screen.getByRole('combobox', { name: /interview language/i });
    await user.selectOptions(select, 'te');

    // Click the first Start Interview button
    const firstButton = screen.getAllByRole('button', { name: /start interview/i })[0];
    await user.click(firstButton);

    const { createSession } = await import('../api/sessions');
    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith(
        { job_id: '11111111-1111-1111-1111-111111111111', language: 'te' },
        'mock-access-token',
      );
    });
  });
});
