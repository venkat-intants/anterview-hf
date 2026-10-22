// The candidate's shell: its two nav groups, and one primary per page.
//
// The top bar's filled "Practice interview" button is hidden on /dashboard, where the
// page already has its one primary (the next-step strip). Everywhere else it is
// there, and staff never see it.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import { useEffect } from 'react';

import AppShell from '../components/layout/AppShell';
import { AuthProvider, useAuth } from '../context/AuthContext';
import i18n from '../lib/i18n';

// The shell mounts a notifications bell and a console assistant; neither is
// under test and both would otherwise try the network.
vi.mock('../api/notifications', () => ({
  listNotifications: vi.fn().mockResolvedValue({ items: [], unread: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}));
vi.mock('../components/agent/CopilotLauncher', () => ({ default: () => null }));

function Session({ roles, children }: { roles: string[]; children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('t', {
      user_id: '11111111-1111-1111-1111-111111111111',
      full_name: 'Asha Rao',
      email: 'asha@example.com',
      roles,
    });
  }, [setAuth, roles]);
  return <>{children}</>;
}

function renderShell(path: string, roles: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider>
            <Session roles={roles}>
              <AppShell>
                <p>page</p>
              </AppShell>
            </Session>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe('AppShell - candidate', () => {
  it('hides the top-bar New interview button on the dashboard', async () => {
    renderShell('/dashboard', ['candidate']);
    await screen.findByRole('navigation', { name: 'Primary' });
    expect(
      within(screen.getByRole('banner')).queryByRole('link', { name: 'Practice interview' }),
    ).not.toBeInTheDocument();
  });

  it('keeps it on every other candidate page', async () => {
    renderShell('/applications', ['candidate']);
    await screen.findByRole('navigation', { name: 'Primary' });
    const link = within(screen.getByRole('banner')).getByRole('link', { name: 'Practice interview' });
    expect(link).toHaveAttribute('href', '/start');
  });

  it('lists the nav in two groups: where they stand, then Practice', async () => {
    renderShell('/dashboard', ['candidate']);
    const nav = await screen.findByRole('navigation', { name: 'Primary' });
    const links = within(nav)
      .getAllByRole('link')
      .map((a) => [a.textContent, a.getAttribute('href')]);
    expect(links).toEqual([
      ['Dashboard', '/dashboard'],
      ['My applications', '/applications'],
      ['Jobs', '/jobs'],
      ['Practice interview', '/start'],
      ['History', '/history'],
      ['Resume', '/resume'],
    ]);
    // The second group is named; the first is not.
    expect(within(nav).getByText('Practice')).toBeInTheDocument();
  });

  it('names the nav in the reader’s language', async () => {
    await i18n.changeLanguage('hi');
    try {
      renderShell('/dashboard', ['candidate']);
      const nav = await screen.findByRole('navigation', { name: 'Primary' });
      expect(within(nav).getByRole('link', { name: 'मेरे आवेदन' })).toHaveAttribute(
        'href',
        '/applications',
      );
      expect(within(nav).getByText('अभ्यास')).toBeInTheDocument();
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it.each([
    {
      lang: 'hi',
      newInterview: 'अभ्यास इंटरव्यू',
      promoTitle: 'अभ्यास के लिए तैयार हैं?',
      promoCta: 'अभ्यास इंटरव्यू',
      search: 'नौकरियाँ खोजें',
      roleLabel: 'उम्मीदवार',
    },
    {
      lang: 'te',
      newInterview: 'సాధన ఇంటర్వ్యూ',
      promoTitle: 'సాధనకు సిద్ధమా?',
      promoCta: 'సాధన ఇంటర్వ్యూ',
      search: 'ఉద్యోగాలు వెతకండి',
      roleLabel: 'అభ్యర్థి',
    },
  ])('renders the top bar and promo in $lang', async (c) => {
    await i18n.changeLanguage(c.lang);
    try {
      renderShell('/applications', ['candidate']);
      await screen.findByRole('navigation', { name: 'Primary' });
      // One label for the /start action everywhere (audit CA-L12).
      expect(
        within(screen.getByRole('banner')).getByRole('link', { name: c.newInterview }),
      ).toHaveAttribute('href', '/start');
      expect(screen.getByText(c.promoTitle)).toBeInTheDocument();
      for (const link of screen.getAllByRole('link', { name: c.promoCta })) {
        expect(link).toHaveAttribute('href', '/start');
      }
      expect(screen.getByRole('searchbox', { name: c.search })).toBeInTheDocument();
      expect(screen.getAllByText(c.roleLabel).length).toBeGreaterThan(0);
      // None of the English literals this shell used to hard-code.
      expect(screen.queryByText('Ready to practice?')).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'Practice interview' })).not.toBeInTheDocument();
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it('shows staff none of it', async () => {
    renderShell('/hr', ['hr_manager']);
    await screen.findByRole('navigation', { name: 'Primary' });
    expect(screen.queryByRole('link', { name: 'Practice interview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'My applications' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Practice interview' })).not.toBeInTheDocument();
  });

  it('shows an invited guest no account links', async () => {
    renderShell('/dashboard', ['guest_candidate']);
    await screen.findByRole('navigation', { name: 'Primary' });
    expect(screen.queryByRole('link', { name: 'My applications' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Practice interview' })).not.toBeInTheDocument();
  });
});

function Where() {
  const loc = useLocation();
  return <p data-testid="where">{`${loc.pathname}${loc.search}`}</p>;
}

describe('AppShell - search and storage', () => {
  // Audit CA-M5: the search used to drop what was typed.
  it('carries the typed term to /jobs', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={['/applications']}>
            <AuthProvider>
              <Session roles={['candidate']}>
                <AppShell>
                  <Where />
                </AppShell>
              </Session>
            </AuthProvider>
          </MemoryRouter>
        </QueryClientProvider>
      </I18nextProvider>,
    );
    const box = await screen.findByRole('searchbox', { name: 'Search jobs' });
    await userEvent.type(box, 'data entry{Enter}');
    expect(screen.getByTestId('where')).toHaveTextContent('/jobs?q=data%20entry');
  });

  // Audit CA-L18: blocked storage threw at render and took the shell down.
  it('still renders when storage is blocked', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    try {
      renderShell('/applications', ['candidate']);
      expect(await screen.findByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });
});
