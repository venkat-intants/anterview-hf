// Tests for the candidate's magic-link landing page (FE-2).
//
// The only page in the app reached by someone with NO account, and the one that
// converts a URL fragment into a live authenticated interview session. Three
// properties are worth pinning and had no test:
//   • the token is read from the #fragment and stripped from the URL on entry,
//     so it never reaches a server log or a Referer header;
//   • DPDP consent gates the Begin button — no consent, no session;
//   • a TRANSIENT failure must not brand a valid link "invalid", which is the
//     difference between "retry" and "ask your recruiter for a new link".

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InterviewInviteInfo, InterviewRedeem } from '../api/publicInterview';
import { ApiError } from '../api/client';

const INFO: InterviewInviteInfo = {
  applicant_name: 'Bhavya Nair',
  job_title: 'Backend Engineer',
  level: 'mid',
  language: 'hi',
  status: 'invited',
  already_completed: false,
  scheduled_at: null,
};

const REDEEM: InterviewRedeem = {
  session_id: 'sess-77',
  access_token: 'jwt-for-guest',
  language: 'hi',
  user_id: 'u-guest',
  full_name: 'Bhavya Nair',
  email: null,
  roles: ['candidate'],
};

const getInterviewInvite = vi.fn();
const redeemInterviewInvite = vi.fn();
vi.mock('../api/publicInterview', () => ({
  getInterviewInvite: (...a: unknown[]) => getInterviewInvite(...a) as unknown,
  redeemInterviewInvite: (...a: unknown[]) => redeemInterviewInvite(...a) as unknown,
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const setAuth = vi.fn();
const clearAuth = vi.fn();
interface FakeUser {
  user_id: string;
  email: string;
  roles: string[];
}
let signedIn: FakeUser | null = null;
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    setAuth,
    clearAuth,
    isAuthenticated: signedIn !== null,
    isInitializing: false,
    user: signedIn,
  }),
}));
const logout = vi.fn();
vi.mock('../api/auth', () => ({
  logout: (...a: unknown[]) => logout(...a) as unknown,
}));

import InterviewInvite from '../pages/InterviewInvite';

function renderInvite() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <InterviewInvite />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const replaceState = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = '#tok_abcdef123456';
  window.history.replaceState = replaceState;
  localStorage.clear();
  // The page keeps its token in this tab's storage across reloads (CA-H3).
  sessionStorage.clear();
  signedIn = null;
  logout.mockResolvedValue({});
  getInterviewInvite.mockResolvedValue(INFO);
  redeemInterviewInvite.mockResolvedValue(REDEEM);
});

afterEach(() => {
  window.location.hash = '';
});

describe('InterviewInvite - link states', () => {
  it('greets the applicant by name for the role they were invited to', async () => {
    renderInvite();

    expect(
      await screen.findByRole('heading', { name: 'Backend Engineer' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/hi bhavya nair/i)).toBeInTheDocument();
    expect(getInterviewInvite).toHaveBeenCalledWith('tok_abcdef123456');
  });

  // Audit CA-M26: even a truncated token hint meant nothing to the candidate
  // and put part of a bearer secret in the DOM. /exam removed it; so does this.
  it('prints no part of the token', async () => {
    renderInvite();

    await screen.findByRole('heading', { name: 'Backend Engineer' });
    expect(screen.queryByText(/tok_abcd/)).not.toBeInTheDocument();
  });

  // Audit CA-H3: a reload used to land on "this link isn't valid".
  it('still opens after a reload, once the fragment is gone', async () => {
    const first = renderInvite();
    await screen.findByRole('heading', { name: 'Backend Engineer' });
    first.unmount();
    window.location.hash = '';
    getInterviewInvite.mockClear();

    renderInvite();
    await screen.findByRole('heading', { name: 'Backend Engineer' });
    expect(getInterviewInvite).toHaveBeenCalledWith('tok_abcdef123456');
  });

  it('refuses without ever calling the API when the link carries no token', async () => {
    window.location.hash = '';
    renderInvite();

    expect(await screen.findByText(/isn't valid/i)).toBeInTheDocument();
    expect(getInterviewInvite).not.toHaveBeenCalled();
  });

  it('calls a rejected link invalid', async () => {
    getInterviewInvite.mockRejectedValue(new ApiError('Invite revoked', 404));
    renderInvite();

    expect(await screen.findByText(/isn't valid/i)).toBeInTheDocument();
    expect(screen.getByText(/ask your recruiter for a fresh link/i)).toBeInTheDocument();
    // Reopening the emailed link comes first: it is the likelier fix.
    expect(screen.getByText(/Open the link in that email again/)).toBeInTheDocument();
    await waitFor(() => expect(sessionStorage.getItem('anthire.invite.token')).toBeNull());
  });

  it('offers a retry - not "invalid" - when the failure was transient', async () => {
    // A 503 during a Space replica swap must not tell a candidate with a
    // perfectly good link that their invite was revoked.
    getInterviewInvite.mockRejectedValue(new ApiError('Bad gateway', 503));
    renderInvite();

    // The page overrides the client's retry:false for transient errors — two
    // more attempts at react-query's default 1s/2s backoff before it gives up,
    // so this legitimately takes ~3s. That retrying happens at all is half the
    // behaviour under test.
    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 8000 }),
    ).toBeInTheDocument();
    expect(getInterviewInvite).toHaveBeenCalledTimes(3);
    expect(screen.queryByText(/isn't valid/i)).not.toBeInTheDocument();
  });

  it('closes the loop for an interview that is already done', async () => {
    getInterviewInvite.mockResolvedValue({ ...INFO, already_completed: true });
    renderInvite();

    expect(await screen.findByText(/already completed this interview/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /begin interview/i })).not.toBeInTheDocument();
    const next = screen.getByRole('heading', { name: 'What happens next' }).closest('section') as HTMLElement;
    // Promised only to those who move forward: progression may be automatic.
    expect(within(next).getByText(/if you move forward, you will get an email with the next step/)).toBeInTheDocument();
    expect(within(next).queryByText(/A person makes that decision/)).toBeNull();
    expect(within(next).getByRole('link', { name: 'Track this in My applications' })).toHaveAttribute('href', '/login');
  });
});

describe('InterviewInvite - consent gate', () => {
  it('keeps Begin disabled until consent is given', async () => {
    const user = userEvent.setup();
    renderInvite();

    await screen.findByRole('heading', { name: 'Backend Engineer' });
    const begin = screen.getByRole('button', { name: /begin interview/i });
    expect(begin).toBeDisabled();

    await user.click(screen.getByRole('checkbox'));
    expect(begin).toBeEnabled();
  });

  it('names microphone, camera and DPDP withdrawal in the consent text', async () => {
    // The wording is the legal basis for processing biometric-derived data;
    // a redesign that trims it to "I agree" is a compliance regression.
    renderInvite();

    await screen.findByRole('heading', { name: 'Backend Engineer' });
    const consent = screen.getByRole('checkbox').closest('label') as HTMLElement;
    expect(consent).toHaveTextContent(/microphone and camera/i);
    expect(consent).toHaveTextContent(/withdraw consent/i);
    expect(consent).toHaveTextContent(/DPDP/i);
  });
});

describe('InterviewInvite - redeeming', () => {
  it('signs the guest in and strips the token from the URL before leaving', async () => {
    const user = userEvent.setup();
    renderInvite();

    await screen.findByRole('heading', { name: 'Backend Engineer' });
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /begin interview/i }));

    await waitFor(() => expect(redeemInterviewInvite).toHaveBeenCalledWith('tok_abcdef123456', true));
    expect(setAuth).toHaveBeenCalledWith(
      'jwt-for-guest',
      expect.objectContaining({ user_id: 'u-guest', roles: ['candidate'] }),
    );
    // The token must not survive in the address bar / Referer.
    expect(replaceState).toHaveBeenCalledWith(null, '', '/interview-invite');
    expect(navigate).toHaveBeenCalledWith('/interview/sess-77', { replace: true });
  });

  it('carries the invite language into the interview', async () => {
    const user = userEvent.setup();
    renderInvite();

    await screen.findByRole('heading', { name: 'Backend Engineer' });
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /begin interview/i }));

    await waitFor(() =>
      expect(localStorage.getItem('intants:interview-language')).toBe('hi'),
    );
  });

  it('reports a failed redeem in place rather than navigating nowhere', async () => {
    redeemInterviewInvite.mockRejectedValue(new Error('This invite has expired.'));
    const user = userEvent.setup();
    renderInvite();

    await screen.findByRole('heading', { name: 'Backend Engineer' });
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /begin interview/i }));

    expect(await screen.findByText('This invite has expired.')).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});

// Audit CA-H4: starting an invited interview from the account used to replace
// the account session with a guest token, silently.
describe('InterviewInvite - a signed-in candidate', () => {
  async function begin(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    await screen.findByRole('heading', { name: 'Backend Engineer' });
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /begin interview/i }));
  }

  it('keeps the account session when the interview is their own', async () => {
    signedIn = { user_id: 'u-me', email: 'me@example.com', roles: ['candidate'] };
    redeemInterviewInvite.mockResolvedValue({ ...REDEEM, user_id: 'u-me', roles: ['guest_candidate'] });
    const user = userEvent.setup();
    renderInvite();
    await begin(user);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/interview/sess-77', { replace: true }));
    expect(setAuth).not.toHaveBeenCalled();
  });

  it('does not swap a different account for the guest without asking', async () => {
    signedIn = { user_id: 'u-other', email: 'other@example.com', roles: ['candidate'] };
    redeemInterviewInvite.mockResolvedValue({ ...REDEEM, roles: ['guest_candidate'] });
    const user = userEvent.setup();
    renderInvite();
    await begin(user);

    expect(await screen.findByText(/signed in as other@example.com/)).toBeInTheDocument();
    expect(setAuth).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Sign out and begin' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/interview/sess-77', { replace: true }));
    expect(logout).toHaveBeenCalled();
    expect(clearAuth).toHaveBeenCalled();
    expect(setAuth).toHaveBeenCalledWith('jwt-for-guest', expect.objectContaining({ user_id: 'u-guest' }));
  });

  it('still adopts the guest session over an earlier guest one', async () => {
    signedIn = { user_id: 'u-old-guest', email: '', roles: ['guest_candidate'] };
    const user = userEvent.setup();
    renderInvite();
    await begin(user);
    await waitFor(() => expect(setAuth).toHaveBeenCalledWith('jwt-for-guest', expect.anything()));
  });
});
