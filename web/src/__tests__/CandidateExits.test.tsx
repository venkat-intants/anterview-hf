// Audit CA-L15 / CA-L16: every dead end has a way out, and "home" means the
// signed-in person's own home, not the candidate dashboard.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

interface FakeUser {
  user_id: string;
  full_name: string;
  email: string;
  roles: string[];
  must_change_password: boolean;
}
let signedIn: FakeUser | null = null;
const clearAuth = vi.fn();
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: signedIn !== null,
    isInitializing: false,
    user: signedIn,
    accessToken: signedIn ? 'tok' : null,
    setAuth: vi.fn(),
    clearAuth,
  }),
}));

const logout = vi.fn();
vi.mock('../api/auth', () => ({
  logout: (...a: unknown[]) => logout(...a) as unknown,
  changePassword: vi.fn(),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../features/proctoring/useProctoring', () => ({ preloadProctorAssets: vi.fn() }));

import NotFound from '../pages/NotFound';
import ChangePassword from '../pages/ChangePassword';
import Interview from '../pages/Interview';

function user(roles: string[]): FakeUser {
  return { user_id: 'u1', full_name: 'A', email: 'a@x.example', roles, must_change_password: true };
}

function renderIn(node: JSX.Element) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  signedIn = null;
  logout.mockResolvedValue({});
});

describe('NotFound', () => {
  it('sends signed-in staff to their own home, not the candidate dashboard', () => {
    signedIn = user(['hr_manager']);
    renderIn(<NotFound />);
    expect(screen.getByRole('link', { name: /go to home/i })).toHaveAttribute('href', '/hr');
  });

  it('sends a candidate to the dashboard', () => {
    signedIn = user(['candidate']);
    renderIn(<NotFound />);
    expect(screen.getByRole('link', { name: /go to home/i })).toHaveAttribute('href', '/dashboard');
  });

  it('sends an invited guest, who has no account pages, to the front page', () => {
    signedIn = user(['guest_candidate']);
    renderIn(<NotFound />);
    expect(screen.getByRole('link', { name: /go to home/i })).toHaveAttribute('href', '/');
  });
});

describe('ChangePassword', () => {
  it('offers a way to sign out instead of being stuck on the forced reset', async () => {
    signedIn = user(['hr_manager']);
    renderIn(<ChangePassword />);
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/login', { replace: true }));
    expect(logout).toHaveBeenCalled();
    expect(clearAuth).toHaveBeenCalled();
  });
});

describe('Interview with no session', () => {
  it('has a link out', () => {
    signedIn = user(['candidate']);
    renderIn(<Interview />);
    expect(screen.getByRole('link', { name: /go to home/i })).toHaveAttribute('href', '/dashboard');
  });
});
