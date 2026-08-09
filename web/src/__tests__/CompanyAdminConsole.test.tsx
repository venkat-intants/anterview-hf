// Tests for the company super_admin console (FE-2).
//
// The middle tier of platform_owner → super_admin → hr_manager. Its one job is
// creating HR managers for the caller's OWN company, and the tenant boundary is
// resolved SERVER-side from the session — this page must never send a company
// id. That property is asserted here, because "the page started sending a
// company id" is a cross-tenant bug that would look like a harmless refactor.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AuthUser, MeResponse } from '../types/auth';
import type { HrManager } from '../api/hr';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ME: MeResponse = {
  user_id: 'u-sa-1',
  full_name: 'Asha Rao',
  email: 'sa@acme.edu',
  roles: ['super_admin'],
  has_resume: false,
  company_id: 'c-acme',
  company_name: 'Acme Skills University',
};

const HRS: HrManager[] = [
  {
    user_id: 'u-hr-1',
    email: 'hr1@acme.edu',
    full_name: 'Bhavya Nair',
    company_id: 'c-acme',
    must_change_password: false,
    created_at: '2026-07-03T10:00:00.000Z',
  },
  {
    user_id: 'u-hr-2',
    email: 'hr2@acme.edu',
    full_name: 'Chetan Iyer',
    company_id: 'c-acme',
    must_change_password: true,
    created_at: '2026-08-01T10:00:00.000Z',
  },
];

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const api = {
  listMyHrManagers: vi.fn(),
  createMyHrManager: vi.fn(),
  deleteMyHrManager: vi.fn(),
};
vi.mock('../api/hr', () => ({
  listMyHrManagers: (...a: unknown[]) => api.listMyHrManagers(...a) as unknown,
  createMyHrManager: (...a: unknown[]) => api.createMyHrManager(...a) as unknown,
  deleteMyHrManager: (...a: unknown[]) => api.deleteMyHrManager(...a) as unknown,
}));

const getMe = vi.fn();
vi.mock('../api/auth', () => ({
  getMe: (...a: unknown[]) => getMe(...a) as unknown,
}));

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => mockUseAuth() as unknown,
}));

import CompanyAdminConsole from '../pages/superadmin/CompanyAdminConsole';
import SuperAdminRoute from '../components/SuperAdminRoute';
import { homePathFor } from '../components/layout/navSections';

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

function renderConsole() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CompanyAdminConsole />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderGuarded(roles: string[]) {
  const user: AuthUser = {
    user_id: 'u-1',
    full_name: 'Test User',
    email: 'test@example.com',
    roles,
  };
  mockUseAuth.mockReturnValue({ isAuthenticated: true, isInitializing: false, user });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/superadmin']}>
        <Routes>
          <Route element={<SuperAdminRoute />}>
            <Route path="/superadmin" element={<CompanyAdminConsole />} />
          </Route>
          <Route path="/dashboard" element={<div>dashboard page</div>} />
          {/* A denied user now returns to their OWN console rather than the
              candidate dashboard, so each home target must be mountable here.
              See navSections.homePathFor. */}
          <Route path="/hr" element={<div>home page</div>} />
          <Route path="/platform" element={<div>home page</div>} />
          <Route path="/admin/overview" element={<div>home page</div>} />
          <Route path="/login" element={<div>login page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getMe.mockResolvedValue(ME);
  api.listMyHrManagers.mockResolvedValue(HRS);
  api.createMyHrManager.mockResolvedValue(HRS[0]);
  api.deleteMyHrManager.mockResolvedValue(undefined);
});

// ---------------------------------------------------------------------------

describe('CompanyAdminConsole — scoping', () => {
  it("names the caller's own company from the session, not from a URL param", async () => {
    renderConsole();

    expect(await screen.findByRole('heading', { name: /super admin/i })).toBeInTheDocument();
    expect(
      await screen.findByText(/hr managers for acme skills university/i),
    ).toBeInTheDocument();
  });

  it('falls back to a neutral label before /auth/me resolves', () => {
    getMe.mockImplementation(() => new Promise(() => undefined));
    renderConsole();

    expect(screen.getByText(/hr managers for your company/i)).toBeInTheDocument();
  });

  it('lists the HR managers the server scoped to this company', async () => {
    renderConsole();

    const list = await screen.findByRole('list', { name: /hr managers/i });
    expect(within(list).getByText('Bhavya Nair')).toBeInTheDocument();
    expect(within(list).getByText('Chetan Iyer')).toBeInTheDocument();
    // The bootstrap state is visible, so an operator can see who has not yet
    // set a password rather than assuming the invite worked.
    expect(within(list).getByText(/pending first login/i)).toBeInTheDocument();
    expect(within(list).getByText(/^active$/i)).toBeInTheDocument();
  });

  it('prompts rather than showing an empty box when there are no HR managers', async () => {
    api.listMyHrManagers.mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText(/no hr managers yet/i)).toBeInTheDocument();
  });
});

describe('CompanyAdminConsole — creating an HR manager', () => {
  it('posts only email and full name — the company comes from the session', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /super admin/i });
    await user.type(screen.getByLabelText(/^email$/i), '  new.hr@acme.edu  ');
    await user.type(screen.getByLabelText(/full name/i), '  Deepa Menon  ');
    await user.click(screen.getByRole('button', { name: /^add hr$/i }));

    await waitFor(() =>
      expect(api.createMyHrManager).toHaveBeenCalledWith({
        email: 'new.hr@acme.edu',
        full_name: 'Deepa Menon',
      }),
    );
    // A company_id in this payload would be a cross-tenant vector: the server
    // must derive the tenant from the caller, never trust the client.
    const payload = api.createMyHrManager.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(['email', 'full_name']);
  });

  it('never ships a password — a set-password link is emailed instead', async () => {
    renderConsole();

    await screen.findByRole('heading', { name: /super admin/i });
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(screen.getByText(/set your password.*link is emailed/i)).toBeInTheDocument();
  });

  it('refuses a half-filled form instead of creating a nameless account', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /super admin/i });
    await user.type(screen.getByLabelText(/^email$/i), 'only.email@acme.edu');
    await user.click(screen.getByRole('button', { name: /^add hr$/i }));

    expect(api.createMyHrManager).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Email and name are required.');
  });

  it('surfaces the server error rather than silently doing nothing', async () => {
    api.createMyHrManager.mockRejectedValue(new Error('Email already registered'));
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /super admin/i });
    await user.type(screen.getByLabelText(/^email$/i), 'dupe@acme.edu');
    await user.type(screen.getByLabelText(/full name/i), 'Dupe');
    await user.click(screen.getByRole('button', { name: /^add hr$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Email already registered'));
  });
});

describe('CompanyAdminConsole — removing an HR manager', () => {
  it('needs a confirm click and removes the row that was clicked', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('button', { name: /remove chetan iyer/i }));
    expect(api.deleteMyHrManager).not.toHaveBeenCalled();

    await user.click(
      within(screen.getByRole('group', { name: /confirm deletion/i })).getByRole('button', {
        name: /^delete$/i,
      }),
    );
    await waitFor(() => expect(api.deleteMyHrManager).toHaveBeenCalledWith('u-hr-2'));
  });
});

describe('CompanyAdminConsole — a wrong-role user sees nothing', () => {
  // Server-side scoping is the real boundary; this stops the UI offering an
  // HR-creation form to someone whose every submit would 403.
  it.each([['platform_owner'], ['hr_manager'], ['admin'], ['candidate']])(
    'redirects a %s away from the super-admin console',
    async (role) => {
      renderGuarded([role]);

      // Behaviour changed deliberately: a denied user lands on THEIR OWN home,
      // not a shared /dashboard fallback their scoped nav does not link to. For
      // a candidate that home genuinely is /dashboard, hence the branch.
      const expected = homePathFor([role]) === '/dashboard' ? 'dashboard page' : 'home page';
      expect(await screen.findByText(expected)).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /super admin/i })).not.toBeInTheDocument();
      expect(api.listMyHrManagers).not.toHaveBeenCalled();
    },
  );

  it('lets a super_admin through to the console', async () => {
    renderGuarded(['super_admin']);

    expect(await screen.findByRole('heading', { name: /super admin/i })).toBeInTheDocument();
    await waitFor(() => expect(api.listMyHrManagers).toHaveBeenCalled());
  });
});
