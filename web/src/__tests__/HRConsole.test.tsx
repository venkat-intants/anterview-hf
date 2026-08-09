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

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { HrAnalytics } from '../api/hr';
import type { NotificationList } from '../api/notifications';

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
  });
  getHrAnalytics.mockResolvedValue(ANALYTICS);
  listNotifications.mockResolvedValue(NOTIFS);
});

describe('HRConsole — greeting', () => {
  it('greets the signed-in manager by name once the profile loads', async () => {
    renderConsole();
    expect(
      await screen.findByRole('heading', { name: /welcome, bhavya nair/i }),
    ).toBeInTheDocument();
  });

  it('shows a neutral title rather than a half-rendered greeting while loading', () => {
    getMe.mockImplementation(() => new Promise(() => undefined));
    renderConsole();
    expect(screen.getByRole('heading', { name: /^hr console$/i })).toBeInTheDocument();
  });
});

describe('HRConsole — funnel strip', () => {
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

    expect(screen.getByText('avg ATS 72')).toBeInTheDocument(); // 71.6 rounded
    expect(screen.getByText('63% avg')).toBeInTheDocument(); // 63.2 rounded
    expect(screen.getByText('7.4/10 avg')).toBeInTheDocument(); // 7.42 to 1dp
  });

  it('omits the sub-text entirely when an average is null, rather than printing NaN', async () => {
    getHrAnalytics.mockResolvedValue({
      ...ANALYTICS,
      averages: { avg_ats: null, avg_exam_percent: null, avg_interview_composite: null },
    });
    renderConsole();
    await screen.findByRole('heading', { name: /welcome/i });

    expect(screen.queryByText(/avg ATS/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });
});

describe('HRConsole — activity feed', () => {
  it('renders each notification as an activity entry with a relative time', async () => {
    renderConsole();

    expect(await screen.findByText('Bhavya Nair scored 82')).toBeInTheDocument();
    expect(screen.getByText(/ATS screening complete/)).toBeInTheDocument();
    expect(screen.getByText('3h ago')).toBeInTheDocument();
  });

  it('says the feed is empty instead of rendering a blank card', async () => {
    listNotifications.mockResolvedValue({ items: [], unread_count: 0 });
    renderConsole();

    expect(await screen.findByText(/no recent activity yet/i)).toBeInTheDocument();
  });

  it('survives a notifications endpoint failure without taking the page down', async () => {
    listNotifications.mockRejectedValue(new Error('503'));
    renderConsole();

    // The console is the HR landing page — a degraded feed must not block the
    // funnel or the quick actions.
    expect(await screen.findByRole('heading', { name: /welcome/i })).toBeInTheDocument();
    expect(await screen.findByText(/no recent activity yet/i)).toBeInTheDocument();
  });
});

describe('HRConsole — quick actions', () => {
  it('links each quick action to its console route', async () => {
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
    expect(hrefsOf(/create exam/i)).toEqual(['/hr/exams']);
  });
});
