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
  company_slug: 'acme-skills-university',
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

const INTERVIEWERS: HrManager[] = [
  {
    user_id: 'u-iv-1',
    email: 'iv1@acme.edu',
    full_name: 'Farah Khan',
    company_id: 'c-acme',
    must_change_password: false,
    created_at: '2026-08-10T10:00:00.000Z',
  },
];

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const api = {
  listMyHrManagers: vi.fn(),
  createMyHrManager: vi.fn(),
  deleteMyHrManager: vi.fn(),
  listMyInterviewers: vi.fn(),
  createMyInterviewer: vi.fn(),
  deleteMyInterviewer: vi.fn(),
  resendMyHrManagerInvite: vi.fn(),
  resendMyInterviewerInvite: vi.fn(),
};
vi.mock('../api/hr', () => ({
  listMyHrManagers: (...a: unknown[]) => api.listMyHrManagers(...a) as unknown,
  createMyHrManager: (...a: unknown[]) => api.createMyHrManager(...a) as unknown,
  deleteMyHrManager: (...a: unknown[]) => api.deleteMyHrManager(...a) as unknown,
  listMyInterviewers: (...a: unknown[]) => api.listMyInterviewers(...a) as unknown,
  createMyInterviewer: (...a: unknown[]) => api.createMyInterviewer(...a) as unknown,
  deleteMyInterviewer: (...a: unknown[]) => api.deleteMyInterviewer(...a) as unknown,
  resendMyHrManagerInvite: (...a: unknown[]) => api.resendMyHrManagerInvite(...a) as unknown,
  resendMyInterviewerInvite: (...a: unknown[]) => api.resendMyInterviewerInvite(...a) as unknown,
}));

// "Waiting on you": the four queue lengths. Same functions the queue pages use.
const queues = {
  listPendingApprovals: vi.fn(),
  listPendingWorkflowReviews: vi.fn(),
  listOfferApprovals: vi.fn(),
  listAdminQuestionReviews: vi.fn(),
};
vi.mock('../api/requisitions', () => ({
  listPendingApprovals: (...a: unknown[]) => queues.listPendingApprovals(...a) as unknown,
}));
vi.mock('../api/workflowReview', () => ({
  listPendingWorkflowReviews: (...a: unknown[]) =>
    queues.listPendingWorkflowReviews(...a) as unknown,
}));
vi.mock('../api/offers', () => ({
  listOfferApprovals: (...a: unknown[]) => queues.listOfferApprovals(...a) as unknown,
}));
vi.mock('../api/questionBanks', () => ({
  listAdminQuestionReviews: (...a: unknown[]) => queues.listAdminQuestionReviews(...a) as unknown,
}));

const getMe = vi.fn();
vi.mock('../api/auth', () => ({
  getMe: (...a: unknown[]) => getMe(...a) as unknown,
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

import CompanyAdminConsole from '../pages/superadmin/CompanyAdminConsole';
import SuperAdminRoute from '../components/SuperAdminRoute';

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
          {/* A denied user returns to their OWN console rather than the
              candidate dashboard, so each home target must be mountable here.
              See navSections.homePathFor.

              Each renders DISTINCT text, following RoleRoute.test.tsx. They all
              said "home page" before, which made the redirect assertion unable
              to tell /hr from /platform from /admin/overview — sending every
              denied role to any one of the three would have passed. */}
          <Route path="/dashboard" element={<div>dashboard page</div>} />
          <Route path="/hr" element={<div>hr page</div>} />
          <Route path="/platform" element={<div>platform page</div>} />
          <Route path="/admin/overview" element={<div>admin overview page</div>} />
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
  api.listMyInterviewers.mockResolvedValue(INTERVIEWERS);
  api.createMyInterviewer.mockResolvedValue(INTERVIEWERS[0]);
  api.deleteMyInterviewer.mockResolvedValue(undefined);
  api.resendMyHrManagerInvite.mockResolvedValue({ email_queued: true });
  api.resendMyInterviewerInvite.mockResolvedValue({ email_queued: true });
  queues.listPendingApprovals.mockResolvedValue([{}, {}, {}]);
  queues.listPendingWorkflowReviews.mockResolvedValue([{}]);
  queues.listOfferApprovals.mockResolvedValue([]);
  queues.listAdminQuestionReviews.mockResolvedValue([{}, {}]);
});

// ---------------------------------------------------------------------------

describe('CompanyAdminConsole - the public careers link', () => {
  it('shows the board address for its own company', async () => {
    // The slug is per-COMPANY, so this console is the link's other natural owner
    // beside /hr. It needs its own instance rather than a shared surface, because
    // HRRoute admits only hr_manager and SuperAdminRoute only super_admin — the same
    // reason /superadmin/library exists beside /hr/library.
    renderConsole();

    expect(
      await screen.findByText(`${window.location.origin}/careers/acme-skills-university`),
    ).toBeInTheDocument();
  });
});

describe('CompanyAdminConsole - scoping', () => {
  it("names the caller's own company from the session, not from a URL param", async () => {
    renderConsole();

    expect(await screen.findByRole('heading', { name: /^team$/i })).toBeInTheDocument();
    expect(
      await screen.findByText(/hr managers and interviewers for acme skills university/i),
    ).toBeInTheDocument();
  });

  it('falls back to a neutral label before /auth/me resolves', () => {
    getMe.mockImplementation(() => new Promise(() => undefined));
    renderConsole();

    expect(screen.getByText(/hr managers and interviewers for your company/i)).toBeInTheDocument();
  });

  it('lists the HR managers the server scoped to this company', async () => {
    renderConsole();

    const list = await screen.findByRole('list', { name: /hr managers/i });
    expect(within(list).getByText('Bhavya Nair')).toBeInTheDocument();
    expect(within(list).getByText('Chetan Iyer')).toBeInTheDocument();
    // The bootstrap state is visible, so an operator can see who has not yet
    // set a password rather than assuming the invite worked.
    expect(within(list).getByText(/pending first sign-in/i)).toBeInTheDocument();
    expect(within(list).getByText(/^active$/i)).toBeInTheDocument();
  });

  it('prompts rather than showing an empty box when there are no HR managers', async () => {
    api.listMyHrManagers.mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText(/no hr managers yet/i)).toBeInTheDocument();
  });
});

describe('CompanyAdminConsole - creating an HR manager', () => {
  it('posts only email and full name - the company comes from the session', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /^team$/i });
    // The fields do not exist until the dialog is opened from the page header.
    expect(screen.queryByLabelText(/^email$/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^new hr manager$/i }));
    await user.type(
      screen.getByLabelText(/^email$/i, { selector: '#hr-email' }),
      '  new.hr@acme.edu  ',
    );
    await user.type(
      screen.getByLabelText(/full name/i, { selector: '#hr-name' }),
      '  Deepa Menon  ',
    );
    await user.click(screen.getByRole('button', { name: /^create hr manager$/i }));

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
    // Success closes the dialog; the set-password hint stays on the page.
    await waitFor(() => expect(screen.queryByLabelText(/^email$/i)).not.toBeInTheDocument());
    expect(screen.getAllByText(/set your password.*link is emailed/i).length).toBe(2);
  });

  it('Cancel discards what was typed and sends nothing', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /^team$/i });
    await user.click(screen.getByRole('button', { name: /^new hr manager$/i }));
    await user.type(screen.getByLabelText(/^email$/i, { selector: '#hr-email' }), 'typed@acme.edu');
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByLabelText(/^email$/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^new hr manager$/i }));
    expect(screen.getByLabelText(/^email$/i, { selector: '#hr-email' })).toHaveValue('');
    expect(api.createMyHrManager).not.toHaveBeenCalled();
  });

  it('never ships a password - a set-password link is emailed instead', async () => {
    renderConsole();

    await screen.findByRole('heading', { name: /^team$/i });
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    // Once for HR managers, once for interviewers — both rosters say it.
    expect(screen.getAllByText(/set your password.*link is emailed/i).length).toBe(2);
  });

  it('refuses a half-filled form instead of creating a nameless account', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /^team$/i });
    await user.click(screen.getByRole('button', { name: /^new hr manager$/i }));
    await user.type(
      screen.getByLabelText(/^email$/i, { selector: '#hr-email' }),
      'only.email@acme.edu',
    );
    await user.click(screen.getByRole('button', { name: /^create hr manager$/i }));

    expect(api.createMyHrManager).not.toHaveBeenCalled();
    // The rule is unchanged (both fields required); it is now said beside the field.
    expect(await screen.findByText('Full name is required.')).toBeInTheDocument();
    expect(screen.queryByText('Email is required.')).not.toBeInTheDocument();
  });

  it('surfaces the server error rather than silently doing nothing', async () => {
    api.createMyHrManager.mockRejectedValue(new Error('Email already registered'));
    const user = userEvent.setup();
    renderConsole();

    await screen.findByRole('heading', { name: /^team$/i });
    await user.click(screen.getByRole('button', { name: /^new hr manager$/i }));
    await user.type(screen.getByLabelText(/^email$/i, { selector: '#hr-email' }), 'dupe@acme.edu');
    await user.type(screen.getByLabelText(/full name/i, { selector: '#hr-name' }), 'Dupe');
    await user.click(screen.getByRole('button', { name: /^create hr manager$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Email already registered'));
    // The dialog stays open with the values and the reason, so nothing is lost.
    expect(await screen.findByRole('alert')).toHaveTextContent('Email already registered');
    expect(screen.getByLabelText(/^email$/i, { selector: '#hr-email' })).toHaveValue(
      'dupe@acme.edu',
    );
  });
});

describe('CompanyAdminConsole - removing an HR manager', () => {
  it('needs a confirm click and removes the row that was clicked', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('button', { name: /remove chetan iyer/i }));
    expect(api.deleteMyHrManager).not.toHaveBeenCalled();

    // The confirm is a dialog that names the person (audit B4).
    await user.click(
      within(await screen.findByRole('dialog', { name: 'Remove Chetan Iyer?' })).getByRole(
        'button',
        { name: 'Remove' },
      ),
    );
    await waitFor(() => expect(api.deleteMyHrManager).toHaveBeenCalledWith('u-hr-2'));
  });

  it('names the person, and Cancel backs out without removing anyone', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Bhavya Nair');
    const trigger = screen.getByRole('button', { name: /remove chetan iyer/i });
    await user.click(trigger);
    const dialog = await screen.findByRole('dialog', { name: 'Remove Chetan Iyer?' });
    expect(dialog).toHaveTextContent(/loses access to/);

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.deleteMyHrManager).not.toHaveBeenCalled();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});

describe('CompanyAdminConsole - interviewers (D4-1)', () => {
  it('lists the interviewers the server scoped to this company, separately from HR managers', async () => {
    renderConsole();

    const list = await screen.findByRole('list', { name: /^interviewers$/i });
    expect(within(list).getByText('Farah Khan')).toBeInTheDocument();
    // The HR list is a different list — an interviewer must not show up there.
    const hrList = await screen.findByRole('list', { name: /^hr managers$/i });
    expect(within(hrList).queryByText('Farah Khan')).not.toBeInTheDocument();
  });

  it('prompts rather than showing an empty box when there are no interviewers', async () => {
    api.listMyInterviewers.mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText(/no interviewers yet/i)).toBeInTheDocument();
  });

  it('creates an interviewer from its own form, distinct from the HR one', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Farah Khan');
    await user.click(screen.getByRole('button', { name: /^new interviewer$/i }));
    await user.type(
      screen.getByLabelText(/^email$/i, { selector: '#interviewer-email' }),
      'new.iv@acme.edu',
    );
    await user.type(
      screen.getByLabelText(/full name/i, { selector: '#interviewer-name' }),
      'New Interviewer',
    );
    await user.click(screen.getByRole('button', { name: /^create interviewer$/i }));

    await waitFor(() =>
      expect(api.createMyInterviewer).toHaveBeenCalledWith({
        email: 'new.iv@acme.edu',
        full_name: 'New Interviewer',
      }),
    );
    expect(api.createMyHrManager).not.toHaveBeenCalled();
  });

  it('warns that removing an interviewer also withdraws their unsubmitted assignments', async () => {
    renderConsole();
    await screen.findByText('Farah Khan');
    expect(
      screen.getByText(
        /removing an interviewer is permanent and also withdraws their unsubmitted interview assignments/i,
      ),
    ).toBeInTheDocument();
  });

  it('needs a confirm click and removes the interviewer that was clicked', async () => {
    const user = userEvent.setup();
    renderConsole();

    await screen.findByText('Farah Khan');
    await user.click(screen.getByRole('button', { name: /remove farah khan/i }));
    expect(api.deleteMyInterviewer).not.toHaveBeenCalled();

    await user.click(
      within(await screen.findByRole('dialog', { name: 'Remove Farah Khan?' })).getByRole(
        'button',
        { name: 'Remove' },
      ),
    );
    await waitFor(() => expect(api.deleteMyInterviewer).toHaveBeenCalledWith('u-iv-1'));
  });
});

describe('CompanyAdminConsole - a wrong-role user sees nothing', () => {
  // Server-side scoping is the real boundary; this stops the UI offering an
  // HR-creation form to someone whose every submit would 403.
  // The destination is written out per role rather than derived from
  // homePathFor. Deriving it made the assertion a tautology in the direction
  // that matters: homePathFor is the function under test here, so a wrong
  // mapping in it produced a matching wrong expectation and the test agreed
  // with the bug. These literals are the ONLY statement in the suite that each
  // role's own console is where it lands.
  it.each([
    ['platform_owner', 'platform page'],
    ['hr_manager', 'hr page'],
    ['admin', 'admin overview page'],
    ['candidate', 'dashboard page'],
  ])('redirects a %s to their own console, not the super-admin one', async (role, landing) => {
    renderGuarded([role]);

    // Behaviour changed deliberately: a denied user lands on THEIR OWN home,
    // not a shared /dashboard fallback their scoped nav does not link to. For
    // a candidate that home genuinely is /dashboard.
    expect(await screen.findByText(landing)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /^team$/i })).not.toBeInTheDocument();
    expect(api.listMyHrManagers).not.toHaveBeenCalled();
  });

  it('lets a super_admin through to the console', async () => {
    renderGuarded(['super_admin']);

    expect(await screen.findByRole('heading', { name: /^team$/i })).toBeInTheDocument();
    await waitFor(() => expect(api.listMyHrManagers).toHaveBeenCalled());
  });
});

// ---------------------------------------------------------------------------
// UX round 3: the console leads with what is waiting on the super admin
// ---------------------------------------------------------------------------

describe('CompanyAdminConsole - waiting on you', () => {
  it('shows the real length of each queue, each cell linking to its queue', async () => {
    renderConsole();

    const group = await screen.findByTestId('waiting-on-you');
    const cell = async (name: RegExp) => within(group).findByRole('link', { name });
    const openings = await cell(/^Openings/);
    expect(openings).toHaveTextContent('3');
    expect(openings).toHaveAttribute('href', '/superadmin/approvals');
    const workflows = await cell(/^Workflows/);
    expect(workflows).toHaveTextContent('1');
    expect(workflows).toHaveAttribute('href', '/superadmin/workflow-reviews');
    // An empty queue is a real 0 — it is shown, not dashed.
    const offers = await cell(/^Offers/);
    expect(offers).toHaveTextContent('0');
    expect(offers).toHaveAttribute('href', '/superadmin/offer-approvals');
    const questions = await cell(/^Questions/);
    expect(questions).toHaveTextContent('2');
    expect(questions).toHaveAttribute('href', '/superadmin/question-reviews');
  });

  it('shows "-" for a queue whose count could not be fetched, never 0', async () => {
    queues.listOfferApprovals.mockRejectedValue(new Error('503'));
    renderConsole();

    const group = await screen.findByTestId('waiting-on-you');
    const offers = await within(group).findByRole('link', { name: /^Offers/ });
    await waitFor(() => expect(offers).toHaveTextContent('-'));
    expect(offers).not.toHaveTextContent('0');
    // The queues that did load keep their numbers.
    expect(within(group).getByRole('link', { name: /^Openings/ })).toHaveTextContent('3');
  });

  it('says so, instead of drawing nothing, when every queue failed', async () => {
    for (const q of Object.values(queues)) q.mockRejectedValue(new Error('503'));
    renderConsole();

    const group = await screen.findByTestId('waiting-on-you');
    expect(await within(group).findByRole('alert')).toHaveTextContent(
      /could not load the approval queues/i,
    );
    expect(within(group).queryByRole('link')).not.toBeInTheDocument();
  });

  it('offers Try again when every queue failed, and refetches all four', async () => {
    for (const q of Object.values(queues)) q.mockRejectedValue(new Error('503'));
    const user = userEvent.setup();
    renderConsole();

    const group = await screen.findByTestId('waiting-on-you');
    await within(group).findByRole('alert');
    queues.listPendingApprovals.mockResolvedValue([{}, {}, {}]);
    queues.listPendingWorkflowReviews.mockResolvedValue([{}]);
    queues.listOfferApprovals.mockResolvedValue([]);
    queues.listAdminQuestionReviews.mockResolvedValue([{}, {}]);
    await user.click(within(group).getByRole('button', { name: 'Try again' }));

    expect(await within(group).findByRole('link', { name: /^Openings/ })).toHaveTextContent('3');
    expect(within(group).getByRole('link', { name: /^Questions/ })).toHaveTextContent('2');
    expect(within(group).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('comes before the rosters', async () => {
    renderConsole();
    const group = await screen.findByTestId('waiting-on-you');
    const roster = await screen.findByRole('list', { name: /^hr managers$/i });
    expect(group.compareDocumentPosition(roster) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Audit fixes AD-F6, AD-F7, AD-F8, AD-F23
// ---------------------------------------------------------------------------

describe('CompanyAdminConsole - a failed roster is an error, not an empty team (AD-F6)', () => {
  it('shows an error with a retry instead of "No HR managers yet"', async () => {
    api.listMyHrManagers.mockRejectedValue(new Error('503'));
    const user = userEvent.setup();
    renderConsole();

    expect(await screen.findByText('Could not load HR managers.')).toBeInTheDocument();
    expect(screen.queryByText(/no hr managers yet/i)).not.toBeInTheDocument();

    api.listMyHrManagers.mockResolvedValue(HRS);
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Bhavya Nair')).toBeInTheDocument();
  });

  it('does the same for interviewers', async () => {
    api.listMyInterviewers.mockRejectedValue(new Error('503'));
    renderConsole();
    expect(await screen.findByText('Could not load interviewers.')).toBeInTheDocument();
    expect(screen.queryByText(/no interviewers yet/i)).not.toBeInTheDocument();
  });
});

describe('CompanyAdminConsole - removing an HR manager is described honestly (AD-F7)', () => {
  it('warns that it is permanent and withdraws their interview assignments', async () => {
    renderConsole();
    await screen.findByText('Bhavya Nair');
    expect(
      screen.getByText(
        /removing an hr manager is permanent and also withdraws their unsubmitted interview assignments/i,
      ),
    ).toBeInTheDocument();
  });
});

describe('CompanyAdminConsole - the set-password email (AD-F8)', () => {
  async function createHr(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole('heading', { name: /^team$/i });
    await user.click(screen.getByRole('button', { name: /^new hr manager$/i }));
    await user.type(screen.getByLabelText(/^email$/i, { selector: '#hr-email' }), 'n@acme.edu');
    await user.type(screen.getByLabelText(/full name/i, { selector: '#hr-name' }), 'N');
    await user.click(screen.getByRole('button', { name: /^create hr manager$/i }));
  }

  it('warns instead of claiming the email went when it was not queued', async () => {
    api.createMyHrManager.mockResolvedValue({ ...HRS[1], email_queued: false });
    const user = userEvent.setup();
    renderConsole();
    await createHr(user);

    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
    expect(String(toastWarning.mock.calls[0]?.[0])).toMatch(/could not be sent/i);
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('confirms the email only when the server queued it', async () => {
    api.createMyHrManager.mockResolvedValue({ ...HRS[1], email_queued: true });
    const user = userEvent.setup();
    renderConsole();
    await createHr(user);

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it('offers Resend link only on accounts still pending first login', async () => {
    const user = userEvent.setup();
    renderConsole();
    const list = await screen.findByRole('list', { name: /^hr managers$/i });

    // Chetan is pending; Bhavya is active and has no resend.
    expect(
      within(list).queryByRole('button', { name: /resend the set-password link to bhavya nair/i }),
    ).not.toBeInTheDocument();
    await user.click(
      within(list).getByRole('button', { name: /resend the set-password link to chetan iyer/i }),
    );
    await waitFor(() => expect(api.resendMyHrManagerInvite).toHaveBeenCalledWith('u-hr-2'));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it('says so when a resend could not be sent', async () => {
    api.resendMyHrManagerInvite.mockResolvedValue({ email_queued: false });
    const user = userEvent.setup();
    renderConsole();
    const list = await screen.findByRole('list', { name: /^hr managers$/i });
    await user.click(
      within(list).getByRole('button', { name: /resend the set-password link to chetan iyer/i }),
    );
    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
  });
});

describe('CompanyAdminConsole - one name for the page and one status label (AD-F23)', () => {
  it('is titled Team, like its nav entry, and says it covers interviewers too', async () => {
    renderConsole();
    expect(await screen.findByTestId('page-title')).toHaveTextContent('Team');
    expect(
      await screen.findByText(/hr managers and interviewers for acme skills university/i),
    ).toBeInTheDocument();
  });

  it('uses the same pending label on rows and in the summary', async () => {
    renderConsole();
    await screen.findByText('Chetan Iyer');
    expect(screen.getAllByText('Pending first sign-in').length).toBeGreaterThan(1);
    expect(screen.queryByText(/awaiting first login/i)).not.toBeInTheDocument();
  });
});
