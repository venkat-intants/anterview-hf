// Tests for the platform_owner console (FE-2).
//
// This is the top of the three-tier admin hierarchy
// (platform_owner → super_admin → hr_manager) and the only surface that creates
// tenant COMPANIES and their one super admin. It had no test at all.
//
// The assertions are behavioural, not snapshots: what the operator can see, and
// which API the button they press actually calls with which arguments. A
// mis-wired create button here provisions the wrong tenant's super admin — the
// exact failure a snapshot would happily record and ship.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AuthUser } from '../types/auth';
import type {
  Company,
  CompanyAdmin,
  HrManager,
  PlatformStats,
  FeatureFlag,
  AuditEvent,
} from '../api/hr';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ACME: Company = {
  id: 'c-acme',
  name: 'Acme Skills University',
  slug: 'acme-skills',
  created_at: '2026-07-01T10:00:00.000Z',
  is_active: true,
  has_admin: true,
  admin_email: 'sa@acme.edu',
  hr_count: 3,
};

const NOVA: Company = {
  id: 'c-nova',
  name: 'Nova Polytechnic',
  slug: 'nova-poly',
  created_at: '2026-07-20T10:00:00.000Z',
  is_active: false,
  has_admin: false,
  admin_email: null,
  hr_count: 0,
};

const ACME_ADMIN: CompanyAdmin = {
  user_id: 'u-sa-1',
  email: 'sa@acme.edu',
  full_name: 'Asha Rao',
  company_id: 'c-acme',
  must_change_password: false,
  created_at: '2026-07-02T10:00:00.000Z',
};

const ACME_HRS: HrManager[] = [
  {
    user_id: 'u-hr-1',
    email: 'hr1@acme.edu',
    full_name: 'Bhavya Nair',
    company_id: 'c-acme',
    must_change_password: false,
    created_at: '2026-07-03T10:00:00.000Z',
  },
];

const STATS: PlatformStats = {
  companies: 2,
  super_admins: 1,
  hr_managers: 3,
  candidates: 412,
  interviews_total: 217,
  interviews_30d: 31,
};

const FLAGS: FeatureFlag[] = [
  {
    key: 'coding_round',
    label: 'Coding round',
    description: 'Enable the coding exam stage',
    enabled: false,
    updated_at: null,
  },
];

const AUDIT: AuditEvent[] = [
  {
    ts: '2026-08-07T09:00:00.000Z',
    kind: 'consent_revoked',
    actor: 'candidate@example.com',
    summary: 'Withdrew interview_voice_recording consent',
  },
];

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const api = {
  listCompanies: vi.fn(),
  createCompany: vi.fn(),
  deleteCompany: vi.fn(),
  getCompanyAdmin: vi.fn(),
  createCompanyAdmin: vi.fn(),
  deleteCompanyAdmin: vi.fn(),
  listCompanyHrManagers: vi.fn(),
  getPlatformStats: vi.fn(),
  listFeatureFlags: vi.fn(),
  setFeatureFlag: vi.fn(),
  listAuditLog: vi.fn(),
  resendCompanyAdminInvite: vi.fn(),
};

vi.mock('../api/hr', () => ({
  listCompanies: (...a: unknown[]) => api.listCompanies(...a) as unknown,
  createCompany: (...a: unknown[]) => api.createCompany(...a) as unknown,
  deleteCompany: (...a: unknown[]) => api.deleteCompany(...a) as unknown,
  getCompanyAdmin: (...a: unknown[]) => api.getCompanyAdmin(...a) as unknown,
  createCompanyAdmin: (...a: unknown[]) => api.createCompanyAdmin(...a) as unknown,
  deleteCompanyAdmin: (...a: unknown[]) => api.deleteCompanyAdmin(...a) as unknown,
  listCompanyHrManagers: (...a: unknown[]) => api.listCompanyHrManagers(...a) as unknown,
  getPlatformStats: (...a: unknown[]) => api.getPlatformStats(...a) as unknown,
  listFeatureFlags: (...a: unknown[]) => api.listFeatureFlags(...a) as unknown,
  setFeatureFlag: (...a: unknown[]) => api.setFeatureFlag(...a) as unknown,
  listAuditLog: (...a: unknown[]) => api.listAuditLog(...a) as unknown,
  resendCompanyAdminInvite: (...a: unknown[]) => api.resendCompanyAdminInvite(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
const toastWarning = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: (...a: unknown[]) => toastWarning(...a) as unknown,
  },
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => mockUseAuth() as unknown,
}));

import PlatformOwnerConsole from '../pages/superadmin/PlatformOwnerConsole';
import { ApiError } from '../api/client';
import PlatformOwnerRoute from '../components/PlatformOwnerRoute';
import { homePathFor } from '../components/layout/navSections';

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

function renderConsole() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PlatformOwnerConsole />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Mount the console the way App.tsx does — behind its role guard. */
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
      <MemoryRouter initialEntries={['/platform']}>
        <Routes>
          <Route element={<PlatformOwnerRoute />}>
            <Route path="/platform" element={<PlatformOwnerConsole />} />
          </Route>
          <Route path="/dashboard" element={<div>dashboard page</div>} />
          {/* A denied user now returns to their OWN console rather than the
              candidate dashboard, so each home target must be mountable here.
              See navSections.homePathFor. */}
          <Route path="/hr" element={<div>home page</div>} />
          <Route path="/superadmin" element={<div>home page</div>} />
          <Route path="/admin/overview" element={<div>home page</div>} />
          <Route path="/login" element={<div>login page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listCompanies.mockResolvedValue([ACME, NOVA]);
  api.getPlatformStats.mockResolvedValue(STATS);
  api.getCompanyAdmin.mockResolvedValue(ACME_ADMIN);
  api.listCompanyHrManagers.mockResolvedValue(ACME_HRS);
  api.listFeatureFlags.mockResolvedValue(FLAGS);
  api.listAuditLog.mockResolvedValue(AUDIT);
  api.createCompany.mockResolvedValue({ ...NOVA, id: 'c-new', name: 'New Co' });
  api.createCompanyAdmin.mockResolvedValue(ACME_ADMIN);
  api.deleteCompany.mockResolvedValue(undefined);
  api.deleteCompanyAdmin.mockResolvedValue(undefined);
  api.setFeatureFlag.mockResolvedValue({ ...FLAGS[0], enabled: true });
  api.resendCompanyAdminInvite.mockResolvedValue({ email_queued: true });
});

// ---------------------------------------------------------------------------
// Companies tab
// ---------------------------------------------------------------------------

describe('PlatformOwnerConsole - tenant list', () => {
  it('lists every company with its super-admin state', async () => {
    renderConsole();

    expect(await screen.findByText('Acme Skills University')).toBeInTheDocument();
    expect(screen.getByText('Nova Polytechnic')).toBeInTheDocument();
    // Acme has one; Nova is flagged as needing one — the whole point of the tab.
    expect(screen.getByText('sa@acme.edu')).toBeInTheDocument();
    expect(screen.getByText(/needs a super admin/i)).toBeInTheDocument();
  });

  it('derives the stat tiles from the live platform counts, not from constants', async () => {
    renderConsole();

    await screen.findByText('Acme Skills University');

    // Asserted on the tile SUB-TEXT, not the big digits: those go through
    // `AnimatedNumber`, whose count-up is gated on framer-motion's `useInView`
    // and therefore on IntersectionObserver — which jsdom does not implement
    // and setup.ts stubs as a no-op, so the digits never leave their first
    // render. The sub-text is plain interpolation of the same response and is
    // what actually distinguishes live data from a placeholder.
    expect(screen.getByText(/31 in the last 30 days/i)).toBeInTheDocument();
    expect(screen.getByText(/1 with a super admin/i)).toBeInTheDocument();
    // 2 companies, 1 super admin ⇒ exactly one tenant is unprotected.
    expect(screen.getByText(/1 company without one/i)).toBeInTheDocument();
  });

  it('reports full coverage once every tenant has a super admin', async () => {
    api.getPlatformStats.mockResolvedValue({ ...STATS, super_admins: 2 });
    renderConsole();

    expect(await screen.findByText(/all companies covered/i)).toBeInTheDocument();
    expect(screen.queryByText(/company without one/i)).not.toBeInTheDocument();
  });

  it('marks an inactive tenant as Suspended', async () => {
    renderConsole();

    await screen.findByText('Nova Polytechnic');
    expect(screen.getByText('Suspended')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('tells the operator the list is empty rather than rendering a bare table', async () => {
    api.listCompanies.mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText(/no companies yet/i)).toBeInTheDocument();
  });
});

describe('PlatformOwnerConsole - creating a tenant', () => {
  it('creates the company with the trimmed name typed into the form', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Acme Skills University');
    // The form does not exist until the dialog is opened from the header.
    expect(screen.queryByLabelText(/company name/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^new company$/i }));
    await user.type(screen.getByLabelText(/company name/i), '  Zeta Institute  ');
    await user.click(screen.getByRole('button', { name: /^create company$/i }));

    await waitFor(() => expect(api.createCompany).toHaveBeenCalledWith('Zeta Institute'));
    // The dialog closes on success and the new company's panel takes over.
    await waitFor(() => expect(screen.queryByLabelText(/company name/i)).not.toBeInTheDocument());
  });

  it('Cancel discards what was typed', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Acme Skills University');
    await user.click(screen.getByRole('button', { name: /^new company$/i }));
    await user.type(screen.getByLabelText(/company name/i), 'Half typed');
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByLabelText(/company name/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^new company$/i }));
    expect(screen.getByLabelText(/company name/i)).toHaveValue('');
    expect(api.createCompany).not.toHaveBeenCalled();
  });

  it('keeps the dialog open and shows the server reason when the create fails', async () => {
    api.createCompany.mockRejectedValue(new Error('Company already exists'));
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Acme Skills University');
    await user.click(screen.getByRole('button', { name: /^new company$/i }));
    await user.type(screen.getByLabelText(/company name/i), 'Acme Skills University');
    await user.click(screen.getByRole('button', { name: /^create company$/i }));

    expect(await screen.findByText('Company already exists')).toBeInTheDocument();
    expect(screen.getByLabelText(/company name/i)).toHaveValue('Acme Skills University');
  });

  it('refuses an empty name instead of POSTing a blank tenant', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Acme Skills University');
    await user.click(screen.getByRole('button', { name: /^new company$/i }));
    await user.click(screen.getByRole('button', { name: /^create company$/i }));

    expect(api.createCompany).not.toHaveBeenCalled();
    expect(await screen.findByText('Company name is required.')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Company-admin panel — the role-scoped create action
// ---------------------------------------------------------------------------

describe('PlatformOwnerConsole - super-admin panel', () => {
  it('does not open a panel until a tenant is selected', async () => {
    renderConsole();

    await screen.findByText('Acme Skills University');
    expect(screen.getByText(/click a row above to manage its super admin/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add super admin/i })).not.toBeInTheDocument();
  });

  it('creates the super admin against the SELECTED company only', async () => {
    // The tenant boundary is the whole safety property here: creating Nova's
    // super admin must not send Acme's id.
    api.getCompanyAdmin.mockRejectedValue(
      new ApiError('This company has no super admin yet.', 404),
    );
    api.listCompanyHrManagers.mockResolvedValue([]);
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select nova polytechnic/i }));

    await user.click(await screen.findByRole('button', { name: /^add super admin$/i }));
    await user.type(await screen.findByLabelText(/^email$/i), 'boss@nova.edu');
    await user.type(screen.getByLabelText(/full name/i), 'Nova Boss');
    await user.click(screen.getByRole('button', { name: /create super admin/i }));

    await waitFor(() =>
      expect(api.createCompanyAdmin).toHaveBeenCalledWith('c-nova', {
        email: 'boss@nova.edu',
        full_name: 'Nova Boss',
      }),
    );
  });

  it('never sends a password - the server emails a set-password link', async () => {
    api.getCompanyAdmin.mockRejectedValue(
      new ApiError('This company has no super admin yet.', 404),
    );
    api.listCompanyHrManagers.mockResolvedValue([]);
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select nova polytechnic/i }));
    await user.click(await screen.findByRole('button', { name: /^add super admin$/i }));
    await user.type(await screen.findByLabelText(/^email$/i), 'boss@nova.edu');
    await user.type(screen.getByLabelText(/full name/i), 'Nova Boss');
    await user.click(screen.getByRole('button', { name: /create super admin/i }));

    await waitFor(() => expect(api.createCompanyAdmin).toHaveBeenCalled());
    const payload = api.createCompanyAdmin.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(Object.keys(payload)).toEqual(['email', 'full_name']);
    // The hint stays on the page after the dialog closes, not only inside it.
    await waitFor(() => expect(screen.queryByLabelText(/^email$/i)).not.toBeInTheDocument());
    expect(screen.getByText(/set your password.*link is emailed/i)).toBeInTheDocument();
  });

  it('hides the create form when the tenant already has a super admin', async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select acme/i }));

    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add super admin/i })).not.toBeInTheDocument();
  });

  it('shows the tenant HR managers read-only - the super admin owns that list', async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select acme/i }));

    const list = await screen.findByRole('list', {
      name: /hr managers for acme skills university/i,
    });
    expect(within(list).getByText('Bhavya Nair')).toBeInTheDocument();
    // Read-only: no add/remove control inside the HR list.
    expect(within(list).queryByRole('button')).not.toBeInTheDocument();
  });

  it('deletes a tenant only after the consequences are shown and its name is typed (AD-F13)', async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select acme/i }));
    await user.click(await screen.findByRole('button', { name: /delete company/i }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/every login in this company is removed/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/signed out straight away/i)).toBeInTheDocument();
    const confirm = within(dialog).getByRole('button', { name: 'Delete company' });
    expect(confirm).toBeDisabled();

    await user.type(
      within(dialog).getByLabelText(/type acme skills university to confirm/i),
      'Acme',
    );
    expect(confirm).toBeDisabled();
    expect(api.deleteCompany).not.toHaveBeenCalled();

    await user.type(
      within(dialog).getByLabelText(/type acme skills university to confirm/i),
      ' Skills University',
    );
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(api.deleteCompany).toHaveBeenCalledWith('c-acme'));
  });
});

// ---------------------------------------------------------------------------
// Feature flags + audit tabs
// ---------------------------------------------------------------------------

describe('PlatformOwnerConsole - feature flags tab', () => {
  it('toggling a flag persists the new value', async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /feature flags/i }));
    await user.click(await screen.findByRole('switch', { name: /toggle coding round/i }));

    await waitFor(() => expect(api.setFeatureFlag).toHaveBeenCalledWith('coding_round', true));
  });

  it('says the flags could not load, with a retry, instead of blaming a migration (AD-F19)', async () => {
    api.listFeatureFlags.mockRejectedValue(
      new ApiError('You do not have permission to do that.', 403),
    );
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /feature flags/i }));
    expect(await screen.findByText('Could not load the feature flags.')).toBeInTheDocument();
    expect(screen.queryByText(/run the latest migration/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('locks a flag while its update is in flight so clicks cannot race (AD-F19)', async () => {
    let finish: (v: FeatureFlag) => void = () => undefined;
    api.setFeatureFlag.mockImplementation(
      () => new Promise<FeatureFlag>((resolve) => (finish = resolve)),
    );
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /feature flags/i }));
    const toggle = await screen.findByRole('switch', { name: /toggle coding round/i });
    await user.click(toggle);
    await waitFor(() => expect(toggle).toBeDisabled());
    await user.click(toggle);
    expect(api.setFeatureFlag).toHaveBeenCalledTimes(1);

    finish({ ...FLAGS[0], enabled: true });
    await waitFor(() => expect(toggle).toBeEnabled());
  });
});

describe('PlatformOwnerConsole - DPDP audit tab', () => {
  it('renders each audit event with its actor and kind', async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /audit log/i }));

    expect(
      await screen.findByText(/withdrew interview_voice_recording consent/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/consent revoked/i)).toBeInTheDocument();
    expect(screen.getByText(/candidate@example\.com/)).toBeInTheDocument();
  });

  it('shows a failed load as an error with a retry, never "No audit events yet" (AD-F4)', async () => {
    api.listAuditLog.mockRejectedValue(new ApiError('Server error', 500, undefined, false));
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /audit log/i }));
    expect(await screen.findByText('Could not load the audit log.')).toBeInTheDocument();
    expect(screen.queryByText(/no audit events yet/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows admin actions as sentences with the person who acted (AD-F3)', async () => {
    api.listAuditLog.mockResolvedValue([
      {
        ts: '2026-08-07T09:00:00.000Z',
        kind: 'admin_action',
        action: 'create_hr_manager',
        actor: 'Asha Rao',
        summary: 'Added HR manager Hema HR',
      },
      {
        ts: '2026-08-07T08:00:00.000Z',
        kind: 'consent_granted',
        actor: 'candidate',
        summary: 'interview',
      },
    ]);
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /audit log/i }));
    expect(await screen.findByText('Added HR manager Hema HR')).toBeInTheDocument();
    expect(screen.getByText(/Asha Rao/)).toBeInTheDocument();
    expect(screen.getByText(/· Candidate/)).toBeInTheDocument();
    expect(screen.queryByText('create_hr_manager')).not.toBeInTheDocument();
  });

  it('says so explicitly when there is nothing to audit', async () => {
    api.listAuditLog.mockResolvedValue([]);
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('tab', { name: /audit log/i }));
    expect(await screen.findByText(/no audit events yet/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Role scoping
// ---------------------------------------------------------------------------

describe('PlatformOwnerConsole - a wrong-role user sees nothing', () => {
  // The server re-checks every one of these endpoints, so this is a UX guard,
  // not the security boundary. It still matters: routing a company super admin
  // into the platform console shows them a broken page and a create form for
  // tenants they can never create.
  it.each([['super_admin'], ['hr_manager'], ['admin'], ['candidate']])(
    'redirects a %s away from the platform console',
    async (role) => {
      renderGuarded([role]);

      // Behaviour changed deliberately: a denied user lands on THEIR OWN home,
      // not a shared /dashboard fallback their scoped nav does not link to. For
      // a candidate that home genuinely is /dashboard, hence the branch.
      const expected = homePathFor([role]) === '/dashboard' ? 'dashboard page' : 'home page';
      expect(await screen.findByText(expected)).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /platform owner/i })).not.toBeInTheDocument();
      // Not merely hidden — the console never mounted, so it never fetched.
      expect(api.listCompanies).not.toHaveBeenCalled();
    },
  );

  it('lets a platform_owner through to the console', async () => {
    renderGuarded(['platform_owner', 'admin']);

    expect(await screen.findByRole('heading', { name: /platform owner/i })).toBeInTheDocument();
    await waitFor(() => expect(api.listCompanies).toHaveBeenCalled());
  });
});

// ---------------------------------------------------------------------------
// Audit fixes AD-F5, AD-F8, AD-F24
// ---------------------------------------------------------------------------

describe('PlatformOwnerConsole - only a 404 means "no super admin" (AD-F5)', () => {
  it('shows an error with a retry, not the Add button, when the lookup fails', async () => {
    api.getCompanyAdmin.mockRejectedValue(new ApiError('Server error', 500, undefined, false));
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select acme/i }));
    expect(
      await screen.findByText("Could not load this company's super admin."),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add super admin/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/has no super admin yet/i)).not.toBeInTheDocument();

    api.getCompanyAdmin.mockResolvedValue(ACME_ADMIN);
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
  });

  it('offers to add one when the server says there is none', async () => {
    api.getCompanyAdmin.mockRejectedValue(
      new ApiError('This company has no super admin yet.', 404),
    );
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select nova polytechnic/i }));
    expect(await screen.findByRole('button', { name: /^add super admin$/i })).toBeInTheDocument();
  });
});

describe('PlatformOwnerConsole - the set-password email (AD-F8)', () => {
  it('warns when the new super admin was created but the email was not queued', async () => {
    api.getCompanyAdmin.mockRejectedValue(
      new ApiError('This company has no super admin yet.', 404),
    );
    api.createCompanyAdmin.mockResolvedValue({ ...ACME_ADMIN, email_queued: false });
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select nova polytechnic/i }));
    await user.click(await screen.findByRole('button', { name: /^add super admin$/i }));
    await user.type(await screen.findByLabelText(/^email$/i), 'boss@nova.edu');
    await user.type(screen.getByLabelText(/full name/i), 'Nova Boss');
    await user.click(screen.getByRole('button', { name: /create super admin/i }));

    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
    expect(String(toastWarning.mock.calls[0]?.[0])).toMatch(/could not be sent/i);
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('re-sends the link to a super admin who has not signed in yet', async () => {
    api.getCompanyAdmin.mockResolvedValue({ ...ACME_ADMIN, must_change_password: true });
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select acme/i }));
    await user.click(
      await screen.findByRole('button', { name: /resend the set-password link to asha rao/i }),
    );
    await waitFor(() => expect(api.resendCompanyAdminInvite).toHaveBeenCalledWith('c-acme'));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it('offers no resend once the super admin is active', async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(await screen.findByRole('button', { name: /select acme/i }));
    await screen.findByText('Asha Rao');
    expect(
      screen.queryByRole('button', { name: /resend the set-password link/i }),
    ).not.toBeInTheDocument();
  });
});

describe('PlatformOwnerConsole - stat tiles never fake a number (AD-F24)', () => {
  it('shows the counts as unavailable, not 0 and "all covered", when the stats fail', async () => {
    api.getPlatformStats.mockRejectedValue(new ApiError('Server error', 500, undefined, false));
    renderConsole();

    await screen.findByText('Acme Skills University');
    expect((await screen.findAllByText('Count unavailable')).length).toBe(4);
    expect(screen.queryByText(/all companies covered/i)).not.toBeInTheDocument();
  });

  it('pluralises the companies without a super admin', async () => {
    api.getPlatformStats.mockResolvedValue({ ...STATS, companies: 4, super_admins: 2 });
    renderConsole();

    expect(await screen.findByText('2 companies without one')).toBeInTheDocument();
  });
});
