// Tests for the Google OAuth callback landing page (B-035)
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { homePathFor } from '../components/layout/navSections';
import GoogleCallback from '../pages/GoogleCallback';


const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const mockComplete = vi.fn();
vi.mock('../api/sso', () => ({
  completeGoogleLogin: (...args: unknown[]) => mockComplete(...args) as unknown,
}));

const mockGetMe = vi.fn();
vi.mock('../api/auth', () => ({
  getMe: (...args: unknown[]) => mockGetMe(...args) as unknown,
}));

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthProvider>
        <Routes>
          <Route path="/auth/google/callback" element={<GoogleCallback />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('GoogleCallback page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /** Drive one full exchange for a user holding `roles`. */
  async function completeLoginAs(roles: string[]): Promise<void> {
    mockComplete.mockResolvedValue({
      access_token: 'jwt',
      token_type: 'bearer',
      user_id: 'u1',
    });
    mockGetMe.mockResolvedValue({
      user_id: 'u1',
      full_name: 'Google User',
      email: 'g@example.com',
      roles,
    });

    renderAt('/auth/google/callback?code=abc&state=xyz');

    await waitFor(() => {
      expect(mockComplete).toHaveBeenCalledWith('abc', 'xyz');
      expect(mockNavigate).toHaveBeenCalled();
    });
  }

  it('exchanges code+state, then navigates a candidate to the dashboard', async () => {
    await completeLoginAs(['candidate']);

    expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
  });

  // SSO must land people where the password login lands them. Before this the
  // callback hardcoded /dashboard, so a Google-authenticated privileged user
  // arrived on the candidate page with a sidebar that has no link to it.
  it.each([
    ['platform_owner'],
    ['super_admin'],
    ['hr_manager'],
    ['admin'],
    ['candidate'],
    ['some_future_role'],
  ])('sends a %s to the same home as the password login', async (role) => {
    await completeLoginAs([role]);

    expect(mockNavigate).toHaveBeenCalledWith(homePathFor([role]), { replace: true });
  });

  it('never leaves a privileged user on the candidate dashboard', async () => {
    await completeLoginAs(['hr_manager']);

    expect(mockNavigate).toHaveBeenCalledWith('/hr', { replace: true });
    expect(mockNavigate).not.toHaveBeenCalledWith('/dashboard', { replace: true });
  });

  it('shows an error when code/state are missing (no exchange attempted)', async () => {
    renderAt('/auth/google/callback');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        /missing authorization code/i,
      );
    });
    expect(mockComplete).not.toHaveBeenCalled();
  });

  it('surfaces the server detail when the exchange fails', async () => {
    mockComplete.mockRejectedValue(new Error('INVALID_OR_EXPIRED_STATE'));

    renderAt('/auth/google/callback?code=abc&state=xyz');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'INVALID_OR_EXPIRED_STATE',
      );
    });
  });

  it('shows a cancelled message when Google returns ?error', async () => {
    renderAt('/auth/google/callback?error=access_denied');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/cancelled/i);
    });
    expect(mockComplete).not.toHaveBeenCalled();
  });
});
