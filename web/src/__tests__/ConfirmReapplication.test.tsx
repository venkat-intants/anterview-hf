// The page that turns a staged reapplication into a real one (PH3-B4b).
//
// It had no test at all, which is how `reapply_confirm` shipped twice as an
// unregistered token kind — the whole flow raised, the router swallowed it,
// and 111 backend tests stayed green because none of them touched it.
//
// What matters here is not that it renders. It is that the page only acts on a
// click, that it tells the four outcomes apart, and that it never hands a
// candidate an English server string or a link they cannot use.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const confirmReapplication = vi.fn();
vi.mock('../api/publicApply', () => ({
  confirmReapplication: (...a: unknown[]) => confirmReapplication(...a) as unknown,
}));

import ConfirmReapplication from '../pages/ConfirmReapplication';

function renderPage(hash = '#tok_abcdef0123456789') {
  window.location.hash = hash;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConfirmReapplication />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/reapply');
});

describe('confirming a reapplication', () => {
  it('does nothing until a person clicks', async () => {
    // Mail clients and security scanners fetch links in the background. A page
    // that confirmed on load would be confirmed by a scanner rather than by the
    // candidate — which is the whole thing this page exists to prevent.
    renderPage();
    await screen.findByRole('button', { name: /Confirm my application/i });
    expect(confirmReapplication).not.toHaveBeenCalled();
  });

  it('sends the token from the fragment, and takes it out of the address bar', async () => {
    confirmReapplication.mockResolvedValue({ applied: 1, message: 'ok' });
    const user = userEvent.setup();
    renderPage('#tok_abcdef0123456789');

    // Stripped on mount: the link is single-use and about to be spent, but a
    // token left on screen is a token that gets screenshotted.
    await waitFor(() => expect(window.location.hash).toBe(''));

    await user.click(await screen.findByRole('button', { name: /Confirm my application/i }));
    await waitFor(() => expect(confirmReapplication).toHaveBeenCalledWith('tok_abcdef0123456789'));
  });

  it('says the application is back with the hiring team when it applied one', async () => {
    confirmReapplication.mockResolvedValue({ applied: 1, message: 'server english' });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /Confirm my application/i }));

    expect(await screen.findByText(/with the hiring team again/i)).toBeInTheDocument();
    // Not the server's sentence: it is English-only, and this page is shown to
    // candidates who chose Hindi or Telugu.
    expect(screen.queryByText('server english')).not.toBeInTheDocument();
  });

  it('tells a second click that there was nothing left to do', async () => {
    confirmReapplication.mockResolvedValue({ applied: 0, message: 'server english' });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /Confirm my application/i }));

    expect(await screen.findByText(/already been confirmed/i)).toBeInTheDocument();
    // And no "view my applications" link: that route is behind auth and a
    // candidate arriving from an email has no session, so offering it is a
    // dead end dressed as a next step.
    expect(screen.queryByRole('link', { name: /applications/i })).not.toBeInTheDocument();
  });

  it('reports an expired link as expired, and anything else in the reader’s language', async () => {
    confirmReapplication.mockRejectedValue(
      new Error('This link is invalid or has expired. Apply again to get a new one.'),
    );
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /Confirm my application/i }));
    expect(await screen.findByText(/no longer valid/i)).toBeInTheDocument();

    vi.clearAllMocks();
    confirmReapplication.mockRejectedValue(new Error('Internal server error.'));
    renderPage();
    const buttons = await screen.findAllByRole('button', { name: /Confirm my application/i });
    await user.click(buttons[buttons.length - 1]);
    expect(await screen.findByText(/could not confirm that just now/i)).toBeInTheDocument();
  });

  it('asks for the emailed link when opened without one', async () => {
    renderPage('');
    expect(await screen.findByText(/needs the link from your email/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Confirm my application/i })).not.toBeInTheDocument();
  });
});
