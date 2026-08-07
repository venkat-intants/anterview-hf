// Smoke + behaviour tests for the five account pages that had no test (FE-2).
//
// Forgot / reset / verify / change-password / 404. These are low-traffic pages
// that are ALWAYS reached by someone already in trouble, so a silent render
// failure is discovered by a locked-out user rather than by us. Each suite
// asserts the page renders, that its client-side validation refuses to send a
// bad request, and — where it exists — the anti-enumeration or token behaviour
// that a redesign could quietly drop.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const forgotPassword = vi.fn();
const resetPassword = vi.fn();
const verifyEmail = vi.fn();
const changePassword = vi.fn();
vi.mock('../api/auth', () => ({
  forgotPassword: (...a: unknown[]) => forgotPassword(...a) as unknown,
  resetPassword: (...a: unknown[]) => resetPassword(...a) as unknown,
  verifyEmail: (...a: unknown[]) => verifyEmail(...a) as unknown,
  changePassword: (...a: unknown[]) => changePassword(...a) as unknown,
}));

vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => mockUseAuth() as unknown }));

import ForgotPassword from '../pages/ForgotPassword';
import ResetPassword from '../pages/ResetPassword';
import VerifyEmail from '../pages/VerifyEmail';
import ChangePassword from '../pages/ChangePassword';
import NotFound from '../pages/NotFound';

function renderPage(node: JSX.Element) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Put a token in the URL fragment — where both token pages read it from. */
function setHash(value: string): void {
  window.location.hash = value;
}

beforeEach(() => {
  vi.clearAllMocks();
  setHash('');
  mockUseAuth.mockReturnValue({
    isAuthenticated: false,
    isInitializing: false,
    user: null,
    accessToken: null,
    setAuth: vi.fn(),
  });
  forgotPassword.mockResolvedValue({ ok: true });
  resetPassword.mockResolvedValue({ ok: true });
  verifyEmail.mockResolvedValue({ ok: true });
  changePassword.mockResolvedValue({ ok: true });
});

afterEach(() => {
  setHash('');
});

// ---------------------------------------------------------------------------

describe('ForgotPassword', () => {
  it('renders the request form', () => {
    renderPage(<ForgotPassword />);
    expect(screen.getByRole('heading', { name: /forgot password/i })).toBeInTheDocument();
    expect(screen.getByRole('form', { name: /forgot password form/i })).toBeInTheDocument();
  });

  it('rejects a malformed address without calling the API', async () => {
    const user = userEvent.setup();
    renderPage(<ForgotPassword />);

    await user.type(screen.getByLabelText(/email/i), 'not-an-email');
    await user.click(screen.getByRole('button', { name: /send|reset|link/i }));

    expect(forgotPassword).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/valid email/i);
  });

  it('confirms without revealing whether the account exists', async () => {
    // Anti-enumeration: the endpoint always succeeds, and the confirmation must
    // stay the same phrasing for a known and an unknown address.
    const user = userEvent.setup();
    renderPage(<ForgotPassword />);

    await user.type(screen.getByLabelText(/email/i), 'nobody@example.com');
    await user.click(screen.getByRole('button', { name: /send|reset|link/i }));

    expect(await screen.findByRole('heading', { name: /check your inbox/i })).toBeInTheDocument();
    expect(screen.getByText(/if an account exists/i)).toBeInTheDocument();
    expect(forgotPassword).toHaveBeenCalledWith('nobody@example.com');
  });
});

describe('ResetPassword', () => {
  it('refuses to show the form when the link carries no token', () => {
    renderPage(<ResetPassword />);

    expect(screen.getByRole('heading', { name: /invalid reset link/i })).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: /reset password form/i })).not.toBeInTheDocument();
  });

  it('shows the form when a token is present in the fragment', () => {
    setHash('#tok-abc');
    renderPage(<ResetPassword />);

    expect(screen.getByRole('heading', { name: /set a new password/i })).toBeInTheDocument();
  });

  it('will not submit a password under 8 characters', async () => {
    setHash('#tok-abc');
    const user = userEvent.setup();
    renderPage(<ResetPassword />);

    await user.type(screen.getByLabelText(/new password/i), 'short');
    await user.type(screen.getByLabelText(/confirm/i), 'short');
    await user.click(screen.getByRole('button', { name: /update|reset|set/i }));

    expect(resetPassword).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/at least 8 characters/i);
  });

  it('will not submit when the confirmation does not match', async () => {
    setHash('#tok-abc');
    const user = userEvent.setup();
    renderPage(<ResetPassword />);

    await user.type(screen.getByLabelText(/new password/i), 'CorrectHorse1!');
    await user.type(screen.getByLabelText(/confirm/i), 'CorrectHorse2!');
    await user.click(screen.getByRole('button', { name: /update|reset|set/i }));

    expect(resetPassword).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/do not match/i);
  });

  it('sends the fragment token with the new password', async () => {
    setHash('#tok-abc');
    const user = userEvent.setup();
    renderPage(<ResetPassword />);

    await user.type(screen.getByLabelText(/new password/i), 'CorrectHorse1!');
    await user.type(screen.getByLabelText(/confirm/i), 'CorrectHorse1!');
    await user.click(screen.getByRole('button', { name: /update|reset|set/i }));

    await waitFor(() => expect(resetPassword).toHaveBeenCalledWith('tok-abc', 'CorrectHorse1!'));
  });
});

describe('VerifyEmail', () => {
  it('spends the single-use token exactly once', async () => {
    setHash('#verify-tok');
    renderPage(<VerifyEmail />);

    expect(await screen.findByRole('heading', { name: /email confirmed/i })).toBeInTheDocument();
    expect(verifyEmail).toHaveBeenCalledTimes(1);
    expect(verifyEmail).toHaveBeenCalledWith('verify-tok');
  });

  it('does not call the API at all when the link has no token', async () => {
    renderPage(<VerifyEmail />);

    expect(
      await screen.findByRole('heading', { name: /couldn.t verify your email/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/missing its token/i)).toBeInTheDocument();
    expect(verifyEmail).not.toHaveBeenCalled();
  });

  it('shows the server reason when the token is rejected', async () => {
    setHash('#stale');
    verifyEmail.mockRejectedValue(new Error('This link has expired.'));
    renderPage(<VerifyEmail />);

    expect(await screen.findByText('This link has expired.')).toBeInTheDocument();
  });
});

describe('ChangePassword', () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({
      isAuthenticated: true,
      isInitializing: false,
      accessToken: 'tok',
      user: {
        user_id: 'u-1',
        full_name: 'HR User',
        email: 'hr@acme.edu',
        roles: ['hr_manager'],
        must_change_password: true,
      },
      setAuth: vi.fn(),
    });
  });

  it('renders the forced-change form', () => {
    renderPage(<ChangePassword />);
    expect(screen.getByRole('form', { name: /change password form/i })).toBeInTheDocument();
  });

  it('enforces the same minimum length client-side as the reset page', async () => {
    const user = userEvent.setup();
    renderPage(<ChangePassword />);

    await user.type(screen.getByLabelText(/new password/i), 'short');
    await user.type(screen.getByLabelText(/confirm/i), 'short');
    await user.click(screen.getByRole('button', { name: /update|change|save|continue/i }));

    expect(changePassword).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('lands an HR manager on their own console, not the candidate dashboard', async () => {
    // The role-specific landing is the reason this page owns a redirect at all:
    // sending an hr_manager to /dashboard after a forced reset drops them into
    // the candidate app.
    const user = userEvent.setup();
    renderPage(<ChangePassword />);

    await user.type(screen.getByLabelText(/new password/i), 'CorrectHorse1!');
    await user.type(screen.getByLabelText(/confirm/i), 'CorrectHorse1!');
    await user.click(screen.getByRole('button', { name: /update|change|save|continue/i }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/hr', { replace: true }));
  });
});

describe('NotFound', () => {
  it('sends a signed-out visitor to the marketing home and offers sign-in', () => {
    renderPage(<NotFound />);

    expect(screen.getByText('404')).toBeInTheDocument();
    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(links).toContain('/');
    expect(links).toContain('/login');
  });

  it('sends a signed-in visitor to their dashboard and drops the sign-in link', () => {
    mockUseAuth.mockReturnValue({
      isAuthenticated: true,
      isInitializing: false,
      user: null,
      accessToken: 'tok',
      setAuth: vi.fn(),
    });
    renderPage(<NotFound />);

    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(links).toContain('/dashboard');
    expect(links).not.toContain('/login');
  });
});
