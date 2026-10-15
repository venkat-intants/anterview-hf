// Shell fixes from the QA batch: exactly one active menu item (ANTHIRE-007/010),
// the notifications popup closing on an outside click (009), the staff
// language note (012) and the sidebar keeping its scroll position (013).
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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

describe('AppShell — one active menu item (QA ANTHIRE-007/010)', () => {
  const active = (): string[] =>
    Array.from(document.querySelectorAll('nav a[aria-current="page"]')).map(
      (a) => a.textContent?.trim() ?? '',
    );

  it.each([
    ['/hr/question-banks/reviews', ['hr_manager'], 'Question reviews'],
    ['/hr/question-banks', ['hr_manager'], 'Question banks'],
    ['/hr/question-banks/b-1', ['hr_manager'], 'Question banks'],
    ['/superadmin/approvals', ['super_admin'], 'Approvals'],
    ['/superadmin/workflow-reviews', ['super_admin'], 'Workflow reviews'],
    ['/superadmin', ['super_admin'], 'Team'],
    ['/hr/', ['hr_manager'], 'Hiring'],
  ])('%s highlights only %s', async (path, roles, label) => {
    renderShell(path, roles);
    await screen.findAllByText(label);
    const on = active();
    expect(on).toEqual([label]);
  });
});

describe('AppShell — notifications popup (QA ANTHIRE-009)', () => {
  it('closes when the person clicks the page outside it', async () => {
    const user = userEvent.setup();
    renderShell('/hr', ['hr_manager']);
    await user.click(await screen.findByRole('button', { name: 'Notifications' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await user.click(screen.getByText('page'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('AppShell — language switcher scope (QA ANTHIRE-012)', () => {
  it('tells staff the console stays in English', async () => {
    const user = userEvent.setup();
    renderShell('/hr', ['hr_manager']);
    await user.click(await screen.findByRole('button', { name: 'UI language' }));
    expect(await screen.findByText(/staff consoles are in english/i)).toBeInTheDocument();
  });

  it('says nothing of the kind to a candidate, whose pages are translated', async () => {
    const user = userEvent.setup();
    renderShell('/dashboard', ['candidate']);
    await user.click(await screen.findByRole('button', { name: 'UI language' }));
    await screen.findByRole('menuitemradio', { name: 'English' });
    expect(screen.queryByText(/staff consoles are in english/i)).not.toBeInTheDocument();
  });
});

describe('AppShell — sidebar keeps its scroll position (QA ANTHIRE-013)', () => {
  it('restores where the menu was scrolled when the shell is rebuilt', async () => {
    // HR -> "My interviews" crosses into another route group, which mounts a
    // fresh AppShell. Simulated here as unmount + mount.
    const first = renderShell('/hr', ['hr_manager']);
    await screen.findAllByText('My interviews');
    const nav = (): HTMLElement =>
      screen.getAllByRole('navigation', { name: 'Primary' })[0];
    nav().scrollTop = 420;
    fireEvent.scroll(nav());
    first.unmount();

    renderShell('/interviewer', ['hr_manager']);
    await screen.findAllByText('My interviews');
    expect(nav().scrollTop).toBe(420);
  });
});
