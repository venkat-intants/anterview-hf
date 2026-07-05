// Tests for StartInterview page (B-036)
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { ConsentProvider } from '../context/ConsentContext';
import StartInterview from '../pages/StartInterview';
import { useEffect } from 'react';

// --- API mocks ---
const mockCreateCustomJob = vi.fn();
vi.mock('../api/jobs', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  createCustomJob: (...args: unknown[]) => mockCreateCustomJob(...args),
}));

const mockCreateSession = vi.fn();
vi.mock('../api/sessions', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  createSession: (...args: unknown[]) => mockCreateSession(...args),
}));

const mockGetAvatars = vi.fn();
vi.mock('../api/avatars', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  getAvatars: (...args: unknown[]) => mockGetAvatars(...args),
}));

const mockGetMe = vi.fn();
vi.mock('../api/auth', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  getMe: (...args: unknown[]) => mockGetMe(...args),
}));

// Consent API mocks — start as consented so most tests don't hit the modal
const mockGetConsentStatus = vi.fn();
const mockPostConsent = vi.fn();
vi.mock('../api/consent', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  getConsentStatus: (...args: unknown[]) => mockGetConsentStatus(...args),
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  postConsent: (...args: unknown[]) => mockPostConsent(...args),
}));

// navigate mock
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Helper: inject a real access token so queries are enabled
function TokenSetter({ children }: { children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('mock-token', {
      user_id: 'u1',
      full_name: 'Test User',
      email: 'test@intants.com',
      roles: ['candidate'],
    });
  }, [setAuth]);
  return <>{children}</>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <ConsentProvider>
            <TokenSetter>
              <StartInterview />
            </TokenSetter>
          </ConsentProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Stable avatar fixture that every test shares unless overridden
const MOCK_AVATARS = [
  { id: 'lucas', name: 'Lucas', gender: 'male', thumbnail_url: 'https://example.com/lucas.mp4' },
  { id: 'anna', name: 'Anna', gender: 'female', thumbnail_url: 'https://example.com/anna.mp4' },
  {
    id: 'gloria',
    name: 'Gloria',
    gender: 'female',
    thumbnail_url: 'https://example.com/gloria.mp4',
  },
];

describe('StartInterview page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: has resume + already consented + avatars load OK
    mockGetMe.mockResolvedValue({
      user_id: 'u1',
      full_name: 'Test User',
      email: 'test@intants.com',
      roles: ['candidate'],
      has_resume: true,
    });
    mockGetConsentStatus.mockResolvedValue({ consented: true });
    mockGetAvatars.mockResolvedValue(MOCK_AVATARS);
    // Clear persisted avatar selection so tests start from the default
    localStorage.removeItem('intants:interview-avatar');
  });

  it('renders the form with all expected fields', async () => {
    renderPage();
    expect(screen.getByRole('heading', { name: /start an interview/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/job title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/company/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/job description/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/experience level/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/interview language/i)).toBeInTheDocument();
    // The submit button text depends on consent state (loaded async).
    // Wait for consent to resolve (consented=true) → button reads "Start Interview".
    await screen.findByRole('button', { name: /start interview/i });
    // Avatar step heading is rendered statically
    expect(screen.getByText(/choose your interviewer/i)).toBeInTheDocument();
  });

  it('shows "resume on file" when has_resume is true', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/your resume is on file/i)).toBeInTheDocument();
    });
  });

  it('shows upload nudge with dashboard link when has_resume is false', async () => {
    mockGetMe.mockResolvedValue({
      user_id: 'u1',
      full_name: 'Test User',
      email: 'test@intants.com',
      roles: ['candidate'],
      has_resume: false,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /uploading your resume/i })).toHaveAttribute(
        'href',
        '/dashboard',
      );
    });
  });

  it('shows validation error when title is empty on submit', async () => {
    const user = userEvent.setup();
    renderPage();
    // Wait for consent to load (consented=true) so the submit button reads "Start Interview"
    const submitBtn = await screen.findByRole('button', { name: /start interview/i });
    await user.click(submitBtn);
    await waitFor(() => {
      expect(screen.getByRole('alert', { hidden: false })).toHaveTextContent(
        /job title is required/i,
      );
    });
    expect(mockCreateCustomJob).not.toHaveBeenCalled();
  });

  it('happy path: creates job + session then navigates to /interview/:id', async () => {
    mockCreateCustomJob.mockResolvedValue({ id: 'job-abc', title: 'Dev' });
    mockCreateSession.mockResolvedValue({ session_id: 'sess-xyz' });

    const user = userEvent.setup();
    renderPage();
    // Wait for consent to load (so consented === true)
    await waitFor(() => expect(mockGetConsentStatus).toHaveBeenCalled());

    await user.type(screen.getByLabelText(/job title/i), 'Dev');
    await user.type(screen.getByLabelText(/company/i), 'Acme');
    await user.click(screen.getByRole('button', { name: /start interview/i }));

    await waitFor(() => {
      expect(mockCreateCustomJob).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Dev', company_name: 'Acme' }),
        'mock-token',
      );
    });
    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: 'job-abc' }),
        'mock-token',
      );
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/interview/sess-xyz');
    });
  });

  it('consent gating: shows ConsentModal when user has not consented', async () => {
    // Not yet consented
    mockGetConsentStatus.mockResolvedValue({ consented: false });

    const user = userEvent.setup();
    renderPage();
    // Wait until consent state is resolved (false) — button text becomes "Accept consent to begin"
    const submitBtn = await screen.findByRole('button', { name: /accept consent to begin/i });
    await user.type(screen.getByLabelText(/job title/i), 'QA Engineer');
    await user.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: /data & privacy consent/i })).toBeInTheDocument();
    });
    // Job/session creation must NOT have started yet
    expect(mockCreateCustomJob).not.toHaveBeenCalled();
  });

  it('consent gating: agreeing to consent proceeds to session', async () => {
    mockGetConsentStatus.mockResolvedValue({ consented: false });
    mockPostConsent.mockResolvedValue({});
    // After recordConsent calls fetchStatus again, return consented:true
    mockGetConsentStatus
      .mockResolvedValueOnce({ consented: false })
      .mockResolvedValue({ consented: true });
    mockCreateCustomJob.mockResolvedValue({ id: 'job-consent', title: 'QA' });
    mockCreateSession.mockResolvedValue({ session_id: 'sess-consent' });

    const user = userEvent.setup();
    renderPage();
    // Wait for consent to resolve (false) — button becomes "Accept consent to begin"
    const submitBtn = await screen.findByRole('button', { name: /accept consent to begin/i });

    await user.type(screen.getByLabelText(/job title/i), 'QA');
    await user.click(submitBtn);

    // Modal should appear
    const agreeBtn = await screen.findByRole('button', { name: /i agree/i });
    await user.click(agreeBtn);

    await waitFor(() => {
      expect(mockPostConsent).toHaveBeenCalled();
      expect(mockCreateCustomJob).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/interview/sess-consent');
    });
  });

  it('shows error message when createCustomJob fails', async () => {
    mockCreateCustomJob.mockRejectedValue(new Error('Server error'));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(mockGetConsentStatus).toHaveBeenCalled());

    await user.type(screen.getByLabelText(/job title/i), 'Bad Job');
    await user.click(screen.getByRole('button', { name: /start interview/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/server error/i);
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  // ── Avatar picker tests ────────────────────────────────────────────────────

  it('avatar picker: renders avatar cards after loading', async () => {
    renderPage();
    // Wait for avatars to appear
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: /select lucas/i })).toBeInTheDocument();
      expect(screen.getByRole('radio', { name: /select anna/i })).toBeInTheDocument();
      expect(screen.getByRole('radio', { name: /select gloria/i })).toBeInTheDocument();
    });
  });

  it('avatar picker: default selection is anna', async () => {
    renderPage();
    await waitFor(() => {
      const annaBtn = screen.getByRole('radio', { name: /select anna/i });
      expect(annaBtn).toHaveAttribute('aria-checked', 'true');
    });
  });

  it('avatar picker: clicking a card changes selection', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: /select lucas/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('radio', { name: /select lucas/i }));
    expect(screen.getByRole('radio', { name: /select lucas/i })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('radio', { name: /select anna/i })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });

  it('avatar picker: selected avatar_id is sent in createSession', async () => {
    mockCreateCustomJob.mockResolvedValue({ id: 'job-avatar', title: 'Dev' });
    mockCreateSession.mockResolvedValue({ session_id: 'sess-avatar' });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(mockGetConsentStatus).toHaveBeenCalled());
    // Wait for avatars
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: /select lucas/i })).toBeInTheDocument(),
    );

    // Select lucas
    await user.click(screen.getByRole('radio', { name: /select lucas/i }));

    await user.type(screen.getByLabelText(/job title/i), 'Dev');
    await user.click(screen.getByRole('button', { name: /start interview/i }));

    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalledWith(
        expect.objectContaining({ avatar_id: 'lucas' }),
        'mock-token',
      );
    });
  });

  it('avatar picker: falls back gracefully on fetch error — start still works', async () => {
    mockGetAvatars.mockRejectedValue(new Error('Network error'));
    mockCreateCustomJob.mockResolvedValue({ id: 'job-fallback', title: 'Dev' });
    mockCreateSession.mockResolvedValue({ session_id: 'sess-fallback' });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(mockGetConsentStatus).toHaveBeenCalled());

    // The avatar query has retry:1, so the error panel appears after one retry.
    // Allow extra time for the retry delay (default ~1 s exponential back-off).
    await waitFor(
      () => {
        expect(screen.getByText(/could not load avatar list/i)).toBeInTheDocument();
      },
      { timeout: 5000 },
    );

    // User can still fill and submit the form
    await user.type(screen.getByLabelText(/job title/i), 'Dev');
    // Consent is true so button reads "Start Interview"
    await user.click(screen.getByRole('button', { name: /start interview/i }));

    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: 'job-fallback' }),
        'mock-token',
      );
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/interview/sess-fallback');
    });
  });
});
