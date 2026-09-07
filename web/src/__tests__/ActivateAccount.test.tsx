// Tests for the applicant account-activation page.
//
// The behaviour worth pinning:
//
//   • THE LINK IS CHECKED BEFORE THE FORM APPEARS. Somebody opening a week-old
//     email must be told on arrival, not after choosing a password.
//   • THE TWO OUTCOMES SAY DIFFERENT THINGS. `linked: 'existing'` means the
//     password they just typed was NOT used — telling them "your account is
//     ready" would send them to sign in with a credential that does not work.
//   • the token comes from the #fragment, and the preview call fires ONCE.
//     React 18 StrictMode double-invokes effects, and the endpoint is capped
//     at five requests a minute.
//   • the expired state does not offer a button that cannot work: nothing on
//     the public side can re-issue an applicant's activation link.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getActivationTarget = vi.fn();
const activateAccount = vi.fn();
vi.mock('../api/publicApply', () => ({
  getActivationTarget: (...a: unknown[]) => getActivationTarget(...a) as unknown,
  activateAccount: (...a: unknown[]) => activateAccount(...a) as unknown,
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import ActivateAccount from '../pages/ActivateAccount';

const TARGET = {
  full_name: 'Nadia Newbie',
  email: 'nadia@example.com',
  job_title: 'Backend Engineer',
  company_name: 'Acme Test Co',
};

function setHash(token: string): void {
  window.location.hash = token ? `#${token}` : '';
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ActivateAccount />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function fillPasswords(
  user: ReturnType<typeof userEvent.setup>,
  pw = 'GoodPass123!',
  confirm = 'GoodPass123!',
): Promise<void> {
  await user.type(screen.getByLabelText('Choose a password'), pw);
  await user.type(screen.getByLabelText('Confirm password'), confirm);
}

beforeEach(() => {
  vi.clearAllMocks();
  setHash('tok-abc-1234567890123456');
  getActivationTarget.mockResolvedValue(TARGET);
  activateAccount.mockResolvedValue({
    email: TARGET.email,
    linked: 'new',
    message: 'Your account is ready.',
  });
});

afterEach(() => {
  setHash('');
});

describe('ActivateAccount', () => {
  it('reads the token from the fragment and checks it first', async () => {
    renderPage();
    await waitFor(() =>
      expect(getActivationTarget).toHaveBeenCalledWith('tok-abc-1234567890123456'),
    );
  });

  it('checks the link exactly once', async () => {
    // The preview is rate limited; a StrictMode double-invoke would spend two
    // of five allowed requests for nothing.
    renderPage();
    await screen.findByText('Track your application');
    await new Promise((r) => setTimeout(r, 30));
    expect(getActivationTarget).toHaveBeenCalledTimes(1);
  });

  it('names the role and company it is for', async () => {
    renderPage();
    expect(
      await screen.findByText(/Backend Engineer application at Acme Test Co/),
    ).toBeInTheDocument();
  });

  it('shows the address the account will use', async () => {
    // People apply with more than one address; this is the bit they check.
    renderPage();
    await screen.findByText('Track your application');
    expect(screen.getByText('nadia@example.com')).toBeInTheDocument();
  });

  it('reports an expired link on arrival, before any form', async () => {
    getActivationTarget.mockRejectedValue(new Error('This link is invalid or has expired.'));
    renderPage();
    expect(await screen.findByText('This link has expired')).toBeInTheDocument();
    expect(screen.queryByLabelText('Choose a password')).not.toBeInTheDocument();
  });

  it('tells an applicant with a dead link that their application is safe', async () => {
    getActivationTarget.mockRejectedValue(new Error('This link is invalid or has expired.'));
    renderPage();
    await screen.findByText('This link has expired');
    expect(screen.getByText(/application is safe/i)).toBeInTheDocument();
  });

  it('offers no self-serve re-send, because none exists', async () => {
    getActivationTarget.mockRejectedValue(new Error('expired'));
    renderPage();
    await screen.findByText('This link has expired');
    expect(screen.queryByRole('link', { name: /new link/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /resend/i })).not.toBeInTheDocument();
  });

  it('handles a link with no token at all without calling the API', async () => {
    setHash('');
    renderPage();
    expect(await screen.findByText('This link has expired')).toBeInTheDocument();
    expect(getActivationTarget).not.toHaveBeenCalled();
  });

  it('refuses a short password without calling the API', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Track your application');
    await fillPasswords(user, 'short', 'short');
    await user.click(screen.getByRole('button', { name: 'Create my account' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/at least 8 characters/);
    expect(activateAccount).not.toHaveBeenCalled();
  });

  it('refuses a mismatched confirmation', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Track your application');
    await fillPasswords(user, 'GoodPass123!', 'OtherPass123!');
    await user.click(screen.getByRole('button', { name: 'Create my account' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/do not match/);
    expect(activateAccount).not.toHaveBeenCalled();
  });

  it('activates and confirms the account is ready', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Track your application');
    await fillPasswords(user);
    await user.click(screen.getByRole('button', { name: 'Create my account' }));

    await waitFor(() =>
      expect(activateAccount).toHaveBeenCalledWith('tok-abc-1234567890123456', 'GoodPass123!'),
    );
    expect(await screen.findByText('You’re all set')).toBeInTheDocument();
  });

  it('says the password was not used when the address already had an account', async () => {
    // The important one. Their existing password still works and the one they
    // just chose does not — so "your account is ready" would be a lie that
    // sends them to a failed sign-in.
    activateAccount.mockResolvedValue({
      email: TARGET.email,
      linked: 'existing',
      message: 'ignored',
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Track your application');
    await fillPasswords(user);
    await user.click(screen.getByRole('button', { name: 'Create my account' }));

    expect(await screen.findByText('Added to your account')).toBeInTheDocument();
    expect(screen.getByText(/existing password/i)).toBeInTheDocument();
  });

  it('surfaces a server refusal without losing the form', async () => {
    activateAccount.mockRejectedValue(new Error('This link is invalid or has expired.'));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Track your application');
    await fillPasswords(user);
    await user.click(screen.getByRole('button', { name: 'Create my account' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or has expired/);
    expect(screen.getByLabelText('Choose a password')).toBeInTheDocument();
  });
});
