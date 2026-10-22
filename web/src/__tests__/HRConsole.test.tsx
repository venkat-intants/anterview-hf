// Tests for the HR manager landing console (FE-2).
//
// First page an hr_manager lands on, and the entry point to every stage of the
// ATS → exam → interview workflow. Untested before this file.
//
// Note on the stat tiles: their big digits render through `AnimatedNumber`,
// whose count-up is gated on framer-motion's `useInView` and therefore on
// IntersectionObserver, which setup.ts stubs as a no-op in jsdom. The digits
// consequently never leave their first render, so the assertions below use the
// tile SUB-TEXT — plain interpolation of the same response — which is what
// actually distinguishes live data from a placeholder.

import { analyticsDefaults } from './analyticsFixture';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { HrAnalytics } from '../api/hr';
import type { NotificationList } from '../api/notifications';
import type { Requisition } from '../api/requisitions';

const ANALYTICS: HrAnalytics = {
  funnel: {
    total_applicants: 48,
    shortlisted: 12,
    exam_taken: 10,
    exam_passed: 7,
    interview_invited: 6,
    interview_completed: 4,
    hired: 1,
    rejected: 9,
  },
  averages: { avg_ats: 71.6, avg_exam_percent: 63.2, avg_interview_composite: 7.42 },
  ...analyticsDefaults(),
};

const NOTIFS: NotificationList = {
  unread_count: 1,
  items: [
    {
      id: 'n-1',
      kind: 'applicant_scored',
      title: 'Bhavya Nair scored 82',
      body: 'ATS screening complete',
      link: '/hr/applicants',
      read: false,
      created_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    },
  ],
};

const getMe = vi.fn();
vi.mock('../api/auth', () => ({ getMe: (...a: unknown[]) => getMe(...a) as unknown }));

const getHrAnalytics = vi.fn();
vi.mock('../api/hr', () => ({
  getHrAnalytics: (...a: unknown[]) => getHrAnalytics(...a) as unknown,
}));

const listNotifications = vi.fn();
vi.mock('../api/notifications', () => ({
  listNotifications: (...a: unknown[]) => listNotifications(...a) as unknown,
}));

// PH4-O1 — the "Stages at risk" widget.
const getSlaBoard = vi.fn();
vi.mock('../api/stageSla', () => ({
  getSlaBoard: (...a: unknown[]) => getSlaBoard(...a) as unknown,
}));

// "Needs you today" counts and "Openings in setup" read these.
const getAttention = vi.fn();
vi.mock('../api/attention', () => ({
  getAttention: (...a: unknown[]) => getAttention(...a) as unknown,
}));

const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => mockUseAuth() as unknown }));

import HRConsole from '../pages/hr/HRConsole';

function renderConsole() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HRConsole />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// "Recent activity" is a record, so it sits closed under a one-line summary;
// opening it is part of reading it.
async function openActivity() {
  const toggle = await screen.findByRole('button', { name: /Recent activity/ });
  if (toggle.getAttribute('aria-expanded') !== 'true') await userEvent.click(toggle);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockUseAuth.mockReturnValue({
    isAuthenticated: true,
    isInitializing: false,
    user: { user_id: 'u-hr', full_name: 'Bhavya Nair', email: 'hr@acme.edu', roles: ['hr_manager'] },
  });
  getMe.mockResolvedValue({
    user_id: 'u-hr',
    full_name: 'Bhavya Nair',
    email: 'hr@acme.edu',
    roles: ['hr_manager'],
    has_resume: false,
    company_id: 'c-acme',
    company_name: 'Acme Skills University',
    company_slug: 'acme-skills-university',
  });
  getHrAnalytics.mockResolvedValue(ANALYTICS);
  listNotifications.mockResolvedValue(NOTIFS);
  getSlaBoard.mockResolvedValue([]);
  getAttention.mockResolvedValue({ generated_at: new Date().toISOString(), total: 0, items: [] });
  listRequisitions.mockResolvedValue([]);
});

describe('HRConsole - the public careers link', () => {
  it('shows the board address, which appeared nowhere in this console before', async () => {
    // THE TEST THAT WOULD HAVE FAILED BEFORE THIS CHANGE, and the reason the card
    // exists. Candidates apply with no login at /careers/<slug>; the address was on
    // exactly one screen — the PLATFORM OWNER's company table, as a bare slug with
    // no link and no copy button — so the company's own HR manager could not
    // discover their own front door. Same bug class navRoleScoping.test.ts already
    // fails the build for on the document library.
    renderConsole();

    expect(
      await screen.findByText(`${window.location.origin}/careers/acme-skills-university`),
    ).toBeInTheDocument();
  });

  it('offers a copy button rather than text to select by hand', async () => {
    renderConsole();

    expect(
      await screen.findByRole('button', { name: /copy the careers page link/i }),
    ).toBeInTheDocument();
  });

  it('shows no careers card for an account with no company', async () => {
    // A platform owner has company_id NULL, so there is no board to link to. An
    // empty card would be worse than no card.
    getMe.mockResolvedValue({
      user_id: 'u-owner',
      full_name: 'Support',
      email: 'support@intants.com',
      roles: ['platform_owner'],
      has_resume: false,
    });
    renderConsole();

    // Wait for the page to settle on something else first, so this is not merely
    // asserting against a tree that has not rendered yet.
    await screen.findByRole('heading', { name: /welcome, support/i });

    expect(screen.queryByTestId('careers-link-card')).toBeNull();
  });
});

describe('HRConsole - greeting', () => {
  it('greets the signed-in manager by name once the profile loads', async () => {
    renderConsole();
    expect(
      await screen.findByRole('heading', { name: /welcome, bhavya nair/i }),
    ).toBeInTheDocument();
  });

  it('shows a neutral title rather than a half-rendered greeting while loading', () => {
    getMe.mockImplementation(() => new Promise(() => undefined));
    renderConsole();
    expect(screen.getByRole('heading', { name: /^hiring$/i })).toBeInTheDocument();
  });
});

describe('HRConsole - funnel strip', () => {
  it('labels every stage of the ATS → exam → interview funnel', async () => {
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    for (const label of ['Applicants', 'Shortlisted', 'Exam passed', 'Interviewed', 'Hired']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('derives the tile sub-text from the live averages', async () => {
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    expect(screen.getByText('avg ATS 72/100')).toBeInTheDocument(); // 71.6 rounded
    expect(screen.getByText('63/100 avg')).toBeInTheDocument(); // 63.2 rounded
    expect(screen.getByText('7.4/10 avg')).toBeInTheDocument(); // 7.42 to 1dp
  });

  it('omits the sub-text entirely when an average is null, rather than printing NaN', async () => {
    getHrAnalytics.mockResolvedValue({
      ...ANALYTICS,
      averages: { avg_ats: null, avg_exam_percent: null, avg_interview_composite: null },
  ...analyticsDefaults(),
    });
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    expect(screen.queryByText(/avg ATS/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });
});

describe('HRConsole - activity feed', () => {
  it('renders each notification as an activity entry with a relative time', async () => {
    renderConsole();

    await openActivity();
    expect(await screen.findByText('Bhavya Nair scored 82')).toBeInTheDocument();
    expect(screen.getByText(/ATS screening complete/)).toBeInTheDocument();
    expect(screen.getByText('3h ago')).toBeInTheDocument();
  });

  it('says the feed is empty instead of rendering a blank card', async () => {
    listNotifications.mockResolvedValue({ items: [], unread_count: 0 });
    renderConsole();

    await openActivity();
    expect(await screen.findByText(/no recent activity yet/i)).toBeInTheDocument();
  });

  it('survives a notifications endpoint failure without taking the page down', async () => {
    listNotifications.mockRejectedValue(new Error('503'));
    renderConsole();

    // The console is the HR landing page — a degraded feed must not block the
    // funnel or the quick actions.
    expect(await screen.findByRole('heading', { name: /welcome/i })).toBeInTheDocument();
    await openActivity();
    expect(await screen.findByText(/no recent activity yet/i)).toBeInTheDocument();
  });
});

describe('HRConsole - stages at risk (PH4-O1)', () => {
  it('says nothing is overdue when the board is clear', async () => {
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    expect(await screen.findByText('Nothing overdue or due soon.')).toBeInTheDocument();
  });

  it('offers each at-risk stage as a way into that candidate, and the full list', async () => {
    getSlaBoard.mockResolvedValue([
      {
        enrolment_id: 'en-1', applicant_id: 'ap-1', requisition_id: 'req-9',
        opening_title: 'Backend Engineer',
        full_name: 'Chetan Iyer', stage: 'Fundamentals', owner_user_id: 'u-hr-1',
        owner_name: 'Priya HR', open_exceptions: 0, state: 'overdue', sla_hours: 24,
        entered_at: '2026-09-01T00:00:00.000Z', due_at: '2026-09-02T00:00:00.000Z',
        hours_remaining: -10,
      },
    ]);
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    expect(
      await screen.findByRole('button', { name: /Chetan Iyer - Fundamentals.*overdue/s }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View the full list' })).toHaveAttribute(
      'href',
      '/hr/stages-at-risk',
    );
    expect(getSlaBoard).toHaveBeenCalledWith({ state: ['overdue', 'due_soon'] });
  });
});

describe('HRConsole - shortcuts', () => {
  it('links each shortcut to its console route', async () => {
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    // "Review applicants" is offered twice — the promo-banner CTA and the
    // quick-actions panel — so assert EVERY link with that name agrees on the
    // destination rather than picking one arbitrarily.
    const hrefsOf = (name: RegExp) =>
      screen.getAllByRole('link', { name }).map((el) => el.getAttribute('href'));

    expect(hrefsOf(/open hiring pipeline/i)).toEqual(['/hr/pipeline']);
    expect(new Set(hrefsOf(/review applicants/i))).toEqual(new Set(['/hr/applicants']));
    expect(hrefsOf(/view analytics/i)).toEqual(['/hr/analytics']);
    expect(hrefsOf(/^exams$/i)).toEqual(['/hr/exams']);
  });
});

// ---------------------------------------------------------------------------
// The activity feed and the Attention panel must not say the same thing
// ---------------------------------------------------------------------------
// 9 of 17 real notifications were `system`, and 8 repeated a finding the
// Attention panel shows directly above. `system` is written by exactly one
// place (agents/watch_runner.py), so excluding it is precise.
const WATCHER_FINDING = {
  id: 'n-sys',
  kind: 'system',
  title: "'Backend Engineer' funnel is losing almost everyone",
  body: '31 applicants, 0 interviewed (0%).',
  link: '/hr/analytics',
  read: false,
  created_at: new Date(Date.now() - 3_600_000).toISOString(),
};

describe('HRConsole activity feed - events, not conditions', () => {
  it('does not repeat a watcher finding the Attention panel owns', async () => {
    listNotifications.mockResolvedValue({
      unread_count: 2,
      items: [WATCHER_FINDING, ...NOTIFS.items],
    });
    renderConsole();

    await openActivity();
    expect(await screen.findByText('Bhavya Nair scored 82')).toBeTruthy();
    expect(
      screen.queryByText("'Backend Engineer' funnel is losing almost everyone"),
    ).toBeNull();
  });

  it('still shows kinds it has never seen', async () => {
    // Denylist, not allowlist: a new event kind must appear unregistered.
    listNotifications.mockResolvedValue({
      unread_count: 1,
      items: [{ ...WATCHER_FINDING, id: 'n-new', kind: 'some_future_kind', title: 'Something new happened' }],
    });
    renderConsole();

    await openActivity();
    expect(await screen.findByText('Something new happened')).toBeTruthy();
  });

  it('says nothing is happening when only watcher findings remain', async () => {
    listNotifications.mockResolvedValue({ unread_count: 1, items: [WATCHER_FINDING] });
    renderConsole();

    await openActivity();
    expect(await screen.findByText('No recent activity yet.')).toBeTruthy();
  });
});

describe('HRConsole activity feed - does not overwrite the bell', () => {
  it("leaves the bell's 30-item list in the cache when it fetches its own 8", async () => {
    // The feed and the bell used to share the exact key ['notifications'] with
    // different limits, so whichever fetched last owned the cache and the bell
    // could open showing only the feed's eight.
    const bell: NotificationList = {
      unread_count: 0,
      items: Array.from({ length: 30 }, (_, i) => ({
        ...NOTIFS.items[0],
        id: `bell-${i}`,
        title: `Bell item ${i}`,
      })),
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['notifications'], bell);

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <HRConsole />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await openActivity();
    expect(await screen.findByText('Bhavya Nair scored 82')).toBeTruthy();
    expect(listNotifications).toHaveBeenCalledWith(8);
    expect(client.getQueryData<NotificationList>(['notifications'])?.items).toHaveLength(30);
  });
});

// ---------------------------------------------------------------------------
// UX round 3: one raised "Needs you today" region, openings in setup, one stat group
// ---------------------------------------------------------------------------

function opening(over: Partial<Requisition>): Requisition {
  return {
    id: 'req-1',
    title: 'Backend Engineer',
    level: 'L2',
    status: 'open',
    approval_status: 'draft',
    public_apply_enabled: false,
    ...over,
  } as Requisition;
}

describe('HRConsole - needs you today', () => {
  it('shows attention and stages at risk together, each with its real count', async () => {
    getAttention.mockResolvedValue({
      generated_at: new Date().toISOString(),
      total: 2,
      items: [
        {
          watcher: 'w', severity: 'warning', title: 'Seven applicants stalled', body: 'b',
          link: '/hr/pipeline', dedupe_key: 'a', citations: [], citation_state: 'unread',
        },
        {
          watcher: 'w', severity: 'info', title: 'A funnel is thin', body: 'b',
          link: null, dedupe_key: 'b', citations: [], citation_state: 'unread',
        },
      ],
    });
    getSlaBoard.mockResolvedValue([
      {
        enrolment_id: 'en-1', applicant_id: 'ap-1', requisition_id: 'req-9',
        opening_title: 'Backend Engineer', full_name: 'Chetan Iyer', stage: 'Fundamentals',
        owner_user_id: 'u-1', owner_name: 'Priya HR', open_exceptions: 0, state: 'overdue',
        sla_hours: 24, entered_at: '2026-09-01T00:00:00.000Z', due_at: '2026-09-02T00:00:00.000Z',
        hours_remaining: -10,
      },
    ]);
    renderConsole();

    const region = await screen.findByTestId('needs-you-today');
    expect(await within(region).findByRole('heading', { name: /attention required\s*2/i })).toBeInTheDocument();
    expect(await within(region).findByRole('heading', { name: /stages at risk\s*1/i })).toBeInTheDocument();
    // Neither list is hidden behind a tab.
    expect(within(region).queryByRole('tab')).not.toBeInTheDocument();
    expect(within(region).getByText('Seven applicants stalled')).toBeInTheDocument();
    expect(within(region).getByRole('button', { name: /Chetan Iyer/ })).toBeInTheDocument();
  });

  it('shows no count when the check failed, rather than a 0', async () => {
    getAttention.mockRejectedValue(new Error('503'));
    renderConsole();

    const region = await screen.findByTestId('needs-you-today');
    expect(await within(region).findByText(/could not check for issues/i)).toBeInTheDocument();
    expect(within(region).getByRole('heading', { name: /^attention required$/i })).toBeInTheDocument();
  });
});

describe('HRConsole - openings in setup', () => {
  it('lists only unclosed openings that are not yet approved and live, each to its setup tab', async () => {
    listRequisitions.mockResolvedValue([
      opening({ id: 'req-draft', title: 'Backend Engineer', approval_status: 'draft' }),
      opening({
        id: 'req-pending', title: 'Data Analyst', approval_status: 'pending_approval',
        submitted_by_name: 'Asha R',
      }),
      opening({ id: 'req-approved', title: 'Designer', approval_status: 'approved', public_apply_enabled: false }),
      // Approved and accepting applications: finished setting up.
      opening({ id: 'req-live', title: 'Live Role', approval_status: 'approved', public_apply_enabled: true }),
      // Closed: not setup, whatever its approval says.
      opening({ id: 'req-closed', title: 'Closed Role', status: 'closed', approval_status: 'draft' }),
    ]);
    renderConsole();

    const section = await screen.findByTestId('openings-in-setup');
    expect(await within(section).findByRole('link', { name: /Backend Engineer/ })).toHaveAttribute(
      'href',
      '/hr/requisitions/req-draft?tab=setup',
    );
    expect(within(section).getByRole('link', { name: /Data Analyst/ })).toHaveAttribute(
      'href',
      '/hr/requisitions/req-pending?tab=setup',
    );
    expect(within(section).getByRole('link', { name: /Designer/ })).toHaveAttribute(
      'href',
      '/hr/requisitions/req-approved?tab=setup',
    );
    expect(within(section).queryByText('Live Role')).not.toBeInTheDocument();
    expect(within(section).queryByText('Closed Role')).not.toBeInTheDocument();

    // The real next stage, in words, from each opening's own fields.
    expect(within(section).getByText(/waiting for approval \(submitted by Asha R\)/)).toBeInTheDocument();
    expect(within(section).getByText(/not submitted for approval yet/)).toBeInTheDocument();
    expect(within(section).getByText(/approved, but not accepting applications yet/)).toBeInTheDocument();
    expect(within(section).getByRole('heading', { name: /openings in setup\s*3/i })).toBeInTheDocument();
  });

  it('says so when nothing is waiting', async () => {
    renderConsole();
    const section = await screen.findByTestId('openings-in-setup');
    expect(await within(section).findByText('No opening is waiting on setup.')).toBeInTheDocument();
  });

  it('shows an error rather than an empty list when openings cannot be fetched', async () => {
    listRequisitions.mockRejectedValue(new Error('503'));
    renderConsole();
    const section = await screen.findByTestId('openings-in-setup');
    expect(await within(section).findByRole('alert')).toHaveTextContent(/could not load your openings/i);
    expect(within(section).queryByText('No opening is waiting on setup.')).not.toBeInTheDocument();
  });
});

describe('HRConsole - pipeline stat group', () => {
  it('makes every cell a link to its list', async () => {
    renderConsole();
    await screen.findByText('avg ATS 72/100');

    const group = screen.getByRole('region', { name: /pipeline at a glance/i });
    const hrefOf = (name: RegExp) => within(group).getByRole('link', { name }).getAttribute('href');
    expect(hrefOf(/^Open roles/)).toBe('/hr/requisitions');
    expect(hrefOf(/^Applicants/)).toBe('/hr/applicants');
    expect(hrefOf(/^Shortlisted/)).toBe('/hr/applicants?status=shortlisted');
    expect(hrefOf(/^Exam passed/)).toBe('/hr/pipeline?stage=exam_passed');
    expect(hrefOf(/^Interviewed/)).toBe('/hr/interviews');
    expect(hrefOf(/^Hired/)).toBe('/hr/pipeline');
  });

  it('shows no stat group before the funnel has loaded', async () => {
    getHrAnalytics.mockImplementation(() => new Promise(() => undefined));
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });
    expect(screen.queryByText('Shortlisted')).not.toBeInTheDocument();
  });
});

describe('HRConsole - header actions say where they go (HR-M10, HR-L9)', () => {
  it('offers resume upload and the exams list, not an invite flow that does not exist', async () => {
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });
    expect(screen.getByRole('link', { name: /upload resumes/i })).toHaveAttribute(
      'href',
      '/hr/applicants',
    );
    expect(screen.getByRole('link', { name: 'Exams' })).toHaveAttribute('href', '/hr/exams');
    expect(screen.queryByText(/invite applicant/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/create exam/i)).not.toBeInTheDocument();
  });
});
