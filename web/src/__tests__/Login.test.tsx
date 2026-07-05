// Smoke tests for Login page
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import { AuthProvider } from '../context/AuthContext';
import Login from '../pages/Login';
import i18n from '../lib/i18n';

// Mock the auth API module
vi.mock('../api/auth', () => ({
  login: vi.fn().mockResolvedValue({
    access_token: 'mock-access-token',
    expires_in: 900,
    user_id: '11111111-1111-1111-1111-111111111111',
    roles: ['candidate'],
  }),
  getMe: vi.fn().mockResolvedValue({
    user_id: '11111111-1111-1111-1111-111111111111',
    full_name: 'Test Candidate',
    email: 'test@intants.com',
    roles: ['candidate'],
  }),
}));

// Mock SSO helper
vi.mock('../api/sso', () => ({
  googleLoginUrl: vi.fn().mockReturnValue('https://accounts.google.com/mock'),
}));

// Mock react-router-dom navigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function renderLogin() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AuthProvider>
            <Login />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe('Login page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the login form', () => {
    renderLogin();
    expect(screen.getByRole('heading', { name: /welcome back/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^sign in$/i })).toBeInTheDocument();
  });

  it('shows validation errors for empty form submit', async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));
    await waitFor(() => {
      expect(screen.getByText(/enter a valid email address/i)).toBeInTheDocument();
    });
  });

  it('shows validation error for short password', async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText(/email address/i), 'test@example.com');
    await user.type(screen.getByLabelText(/password/i), 'short');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));
    await waitFor(() => {
      expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
    });
  });

  it('submits and navigates to dashboard on valid credentials', async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText(/email address/i), 'test@intants.com');
    await user.type(screen.getByLabelText(/password/i), 'password123');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
    });
  });

  it('has a link to the register page', () => {
    renderLogin();
    expect(screen.getByRole('link', { name: /create one/i })).toHaveAttribute('href', '/register');
  });

  it('renders a "Sign in with Google" button (B-035)', () => {
    renderLogin();
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeInTheDocument();
  });
});
