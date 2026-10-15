// "What happens next" on a finish screen, and the one account link under it.
// A guest session (minted from an interview link) must never be shown a link
// to a page the server will refuse them.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const auth = vi.hoisted(() => ({
  value: {
    isAuthenticated: false,
    isInitializing: false,
    user: null as { roles: string[] } | null,
  },
}));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth.value }));

import { PublicNext } from '../components/PublicNext';

function renderNext(props: { track?: boolean } = {}) {
  return render(
    <MemoryRouter>
      <PublicNext {...props}>The hiring team acts next.</PublicNext>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  auth.value = { isAuthenticated: false, isInitializing: false, user: null };
});

describe('PublicNext', () => {
  it('always says what happens next', () => {
    renderNext();
    expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument();
    expect(screen.getByText('The hiring team acts next.')).toBeInTheDocument();
  });

  it.each([
    [
      'nobody is signed in',
      { isAuthenticated: false, isInitializing: false, user: null },
      '/login',
    ],
    [
      'an account is signed in',
      { isAuthenticated: true, isInitializing: false, user: { roles: ['candidate'] } },
      '/applications',
    ],
  ])('links to the right place when %s', (_who, session, href) => {
    auth.value = session;
    renderNext();
    expect(screen.getByRole('link', { name: 'Track this in My applications' })).toHaveAttribute(
      'href',
      href,
    );
  });

  it.each([
    [
      'a guest session',
      { isAuthenticated: true, isInitializing: false, user: { roles: ['guest_candidate'] } },
    ],
    ['a session still being checked', { isAuthenticated: false, isInitializing: true, user: null }],
  ])('draws no account link for %s', (_who, session) => {
    auth.value = session;
    const { container } = renderNext();
    expect(container.querySelector('a')).toBeNull();
  });

  it('draws no link when the page opts out', () => {
    const { container } = renderNext({ track: false });
    expect(container.querySelector('a')).toBeNull();
  });
});
