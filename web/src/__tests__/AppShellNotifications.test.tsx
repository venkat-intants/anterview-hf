// The shell's notifications bell and skip link (audit AD-F18, AD-F33).
//
// A failed notifications load used to read "You're all caught up", the one
// wrong answer because it is also the good news; "Mark all read" could be
// clicked again while the first request was in flight; and there was no way to
// skip the long staff sidebar from the keyboard.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import { useEffect } from 'react';

const listNotifications = vi.fn();
const markAllNotificationsRead = vi.fn();
vi.mock('../api/notifications', () => ({
  listNotifications: (...a: unknown[]) => listNotifications(...a) as unknown,
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: (...a: unknown[]) => markAllNotificationsRead(...a) as unknown,
}));
vi.mock('../components/agent/CopilotLauncher', () => ({ default: () => null }));

import AppShell from '../components/layout/AppShell';
import { AuthProvider, useAuth } from '../context/AuthContext';
import i18n from '../lib/i18n';

function Session({ children }: { children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('t', {
      user_id: '11111111-1111-1111-1111-111111111111',
      full_name: 'Asha Rao',
      email: 'asha@example.com',
      roles: ['super_admin'],
    });
  }, [setAuth]);
  return <>{children}</>;
}

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/superadmin']}>
          <AuthProvider>
            <Session>
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

const UNREAD = {
  items: [
    {
      id: 'n-1',
      kind: 'requisition_approval_requested',
      title: 'Platform Engineer needs your approval',
      body: null,
      link: '/superadmin/approvals',
      read: false,
      created_at: new Date().toISOString(),
    },
  ],
  unread_count: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  listNotifications.mockResolvedValue(UNREAD);
});

describe('AppShell - notifications bell', () => {
  it('says the notifications failed to load instead of "all caught up"', async () => {
    listNotifications.mockRejectedValue(new Error('503'));
    const user = userEvent.setup();
    renderShell();

    await waitFor(() => expect(listNotifications).toHaveBeenCalled());
    await user.click(await screen.findByRole('button', { name: 'Notifications' }));
    expect(await screen.findByText('Could not load notifications.')).toBeInTheDocument();
    expect(screen.queryByText(/all caught up/i)).not.toBeInTheDocument();

    listNotifications.mockResolvedValue(UNREAD);
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Platform Engineer needs your approval')).toBeInTheDocument();
  });

  it('locks "Mark all read" while the request is in flight', async () => {
    markAllNotificationsRead.mockImplementation(() => new Promise(() => undefined));
    const user = userEvent.setup();
    renderShell();

    await user.click(await screen.findByRole('button', { name: 'Notifications' }));
    const markAll = await screen.findByRole('button', { name: 'Mark all read' });
    await user.click(markAll);
    await waitFor(() => expect(markAll).toBeDisabled());
    await user.click(markAll);
    expect(markAllNotificationsRead).toHaveBeenCalledTimes(1);
  });
});

describe('AppShell - skip link', () => {
  it('is the first focusable element and targets the main region', async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByText('page');

    await user.tab();
    const skip = screen.getByRole('link', { name: 'Skip to main content' });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute('href', '#main-content');
    expect(document.getElementById('main-content')?.tagName).toBe('MAIN');
  });
});
