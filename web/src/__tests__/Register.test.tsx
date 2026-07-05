// Smoke tests for Register page
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from '../context/AuthContext';
import Register from '../pages/Register';

vi.mock('../api/auth', () => ({
  register: vi.fn().mockResolvedValue({
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

// The Register component imports googleLoginUrl from @/api/sso — mock to avoid
// undefined VITE_API_BASE_URL in the test environment.
vi.mock('../api/sso', () => ({
  googleLoginUrl: vi.fn().mockReturnValue('https://mock-google-login'),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function renderRegister() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <Register />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The DPDP consent checkbox gates the submit button. Check it before submitting. */
async function checkDpdpConsent(user: ReturnType<typeof userEvent.setup>) {
  const checkbox = screen.getByRole('checkbox', {
    name: /i agree to the terms and dpdp/i,
  });
  await user.click(checkbox);
}

describe('Register page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the registration form', () => {
    renderRegister();
    expect(screen.getByRole('heading', { name: /create your account/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/full name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument();
  });

  it('shows validation errors for empty form submit', async () => {
    const user = userEvent.setup();
    renderRegister();
    // The submit button is gated by the DPDP consent checkbox; check it first.
    await checkDpdpConsent(user);
    await user.click(screen.getByRole('button', { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText(/at least 2 characters/i)).toBeInTheDocument();
    });
  });

  it('shows email validation error for invalid email', async () => {
    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByLabelText(/full name/i), 'Test User');
    await user.type(screen.getByLabelText(/email address/i), 'not-an-email');
    await user.type(screen.getByLabelText(/password/i), 'password123');
    // Check the DPDP consent checkbox before submitting so the button is enabled.
    await checkDpdpConsent(user);
    await user.click(screen.getByRole('button', { name: /create account/i }));
    await waitFor(() => {
      expect(screen.getByText(/enter a valid email address/i)).toBeInTheDocument();
    });
  });

  it('submits and navigates to dashboard on valid input', async () => {
    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByLabelText(/full name/i), 'Test Candidate');
    await user.type(screen.getByLabelText(/email address/i), 'test@intants.com');
    await user.type(screen.getByLabelText(/password/i), 'securepassword123');
    // Check the DPDP consent checkbox before submitting so the button is enabled.
    await checkDpdpConsent(user);
    await user.click(screen.getByRole('button', { name: /create account/i }));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });
    });
  });

  it('has a link to the login page', () => {
    renderRegister();
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login');
  });
});
