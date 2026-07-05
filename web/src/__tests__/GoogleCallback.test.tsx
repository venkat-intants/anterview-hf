// Tests for the Google OAuth callback landing page (B-035)
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
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

  it('exchanges code+state, then navigates to the dashboard', async () => {
    mockComplete.mockResolvedValue({
      access_token: 'jwt',
      token_type: 'bearer',
      user_id: 'u1',
    });
    mockGetMe.mockResolvedValue({
      user_id: 'u1',
      full_name: 'Google User',
      email: 'g@example.com',
      roles: ['candidate'],
    });

    renderAt('/auth/google/callback?code=abc&state=xyz');

    await waitFor(() => {
      expect(mockComplete).toHaveBeenCalledWith('abc', 'xyz');
      expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
    });
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
