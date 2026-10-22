// Audit CA-M9, CA-M15, CA-M16, CA-M17, CA-M21, CA-M27, CA-L8: the sign-in
// pages speak the visitor's language, describe what the consent covers, and
// return a signed-out visitor to the page they asked for.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../lib/i18n';
import { ApiError } from '../api/client';

const login = vi.fn();
const getMe = vi.fn();
const verifyEmail = vi.fn();
vi.mock('../api/auth', () => ({
  login: (...a: unknown[]) => login(...a) as unknown,
  getMe: (...a: unknown[]) => getMe(...a) as unknown,
  verifyEmail: (...a: unknown[]) => verifyEmail(...a) as unknown,
  register: vi.fn(),
}));
vi.mock('../api/sso', () => ({ googleLoginUrl: vi.fn(() => 'https://accounts.example/x') }));

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: { error: (m: string) => { toastError(m); }, success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

let authed = false;
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: authed,
    isInitializing: false,
    user: authed ? { user_id: 'u', full_name: 'A', email: 'a@x.example', roles: ['candidate'] } : null,
    accessToken: authed ? 'tok' : null,
    setAuth: vi.fn(),
    clearAuth: vi.fn(),
  }),
}));

import Login from '../pages/Login';
import Register from '../pages/Register';
import VerifyEmail from '../pages/VerifyEmail';
import RoleRoute from '../components/RoleRoute';
import { PasswordStrength } from '../components/layout/AuthLayout';
import ModeSwitch from '../components/ModeSwitch';

function wrap(node: JSX.Element, entry: { pathname: string; state?: unknown } = { pathname: '/login' }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  authed = false;
  sessionStorage.clear();
  login.mockResolvedValue({ access_token: 'tok', expires_in: 900, user_id: 'u', roles: ['candidate'] });
  getMe.mockResolvedValue({ user_id: 'u', full_name: 'A', email: 'a@x.example', roles: ['candidate'] });
});

afterEach(async () => {
  await i18n.changeLanguage('en');
});

async function signIn() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/email address/i), 'a@x.example');
  await user.type(screen.getByLabelText(/^password/i), 'longenough1');
  await user.click(screen.getByRole('button', { name: /^sign in$/i }));
}

describe('sign-in return path (CA-M27)', () => {
  it('a signed-out visit to a protected page carries that page to /login', () => {
    function LoginProbe() {
      const loc = useLocation();
      return <p>login from {(loc.state as { from?: string } | null)?.from}</p>;
    }
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/applications?x=1']}>
          <Routes>
            <Route element={<RoleRoute />}>
              <Route path="/applications" element={<p>apps</p>} />
            </Route>
            <Route path="/login" element={<LoginProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('login from /applications?x=1')).toBeInTheDocument();
  });

  it('signing in returns to the page that asked for it', async () => {
    wrap(<Login />, { pathname: '/login', state: { from: '/scorecard/abc' } });
    await signIn();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/scorecard/abc', { replace: true }));
  });

  it('ignores an off-site "from"', async () => {
    wrap(<Login />, { pathname: '/login', state: { from: '//evil.example' } });
    await signIn();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/dashboard', { replace: true }));
  });

  it('remembers the page across the Google round trip', async () => {
    wrap(<Login />, { pathname: '/login', state: { from: '/applications' } });
    const user = userEvent.setup();
    const assign = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { ...window.location, assign },
    });
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /sign in with google/i }));
    expect(sessionStorage.getItem('intants:return-to')).toBe('/applications');
  });
});

describe('localised sign-in (CA-M17, CA-M21)', () => {
  it('shows field errors in Hindi on a Hindi page', async () => {
    await i18n.changeLanguage('hi');
    wrap(<Login />);
    await userEvent.click(screen.getByRole('button', { name: 'साइन इन करें' }));
    expect(await screen.findByText('एक मान्य ईमेल पता दर्ज करें।')).toBeInTheDocument();
  });

  it('turns an English 403 into the page language', async () => {
    login.mockRejectedValueOnce(
      new ApiError('Please confirm your email before signing in. We have re-sent it.', 403),
    );
    await i18n.changeLanguage('te');
    wrap(<Login />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('ఈమెయిల్ చిరునామా'), 'a@x.example');
    await user.type(screen.getByLabelText(/^పాస్‌వర్డ్/), 'longenough1');
    await user.click(screen.getByRole('button', { name: 'సైన్ ఇన్ చేయండి' }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls[0][0]).toMatch(/^సైన్ ఇన్ చేసే ముందు/);
  });
});

describe('consent copy (CA-M15, CA-L8)', () => {
  it('names what is agreed to and where to write, instead of documents that do not exist', () => {
    wrap(<Register />, { pathname: '/register' });
    expect(screen.queryByText(/terms/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/privacy policy/i)).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /recording my interviews/i })).toBeInTheDocument();
    expect(screen.getByText(/support@intants\.com/)).toBeInTheDocument();
    expect(screen.getByText(/outside India/)).toBeInTheDocument();
  });

  it('the password hint only promises the rule that is enforced', () => {
    wrap(<Register />, { pathname: '/register' });
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
    expect(screen.queryByText(/with a number and a symbol/i)).not.toBeInTheDocument();
  });
});

describe('verify email (CA-M9, CA-M16)', () => {
  it('does not point to a resend control that does not exist', async () => {
    window.location.hash = '';
    wrap(<VerifyEmail />, { pathname: '/verify-email' });
    expect(await screen.findByText('Could not verify your email')).toBeInTheDocument();
    expect(screen.queryByText(/from your profile/i)).not.toBeInTheDocument();
    expect(screen.getByText(/try signing in/i)).toBeInTheDocument();
  });

  it('renders in Telugu', async () => {
    await i18n.changeLanguage('te');
    window.location.hash = '';
    wrap(<VerifyEmail />, { pathname: '/verify-email' });
    expect(await screen.findByText('మీ ఇమెయిల్‌ను ధృవీకరించలేకపోయాం')).toBeInTheDocument();
  });
});

describe('shared controls (CA-M17)', () => {
  it('the strength meter and mode switch follow the UI language', async () => {
    await i18n.changeLanguage('hi');
    render(
      <>
        <PasswordStrength password="Abcdefgh1!xyz" />
        <ModeSwitch />
      </>,
    );
    expect(screen.getByText('मज़बूत')).toBeInTheDocument();
    expect(screen.getByRole('radiogroup', { name: 'रंग मोड' })).toBeInTheDocument();
  });
});
