// Tests for the consolidated route guards (FE-1).
//
// The five guards are UX guards, not the security boundary — but getting a role
// set wrong silently misroutes a whole console, and that is exactly the class of
// bug a refactor into one shared RoleRoute could introduce. These tests pin the
// permitted/denied role set of each guard as OBSERVABLE ROUTING, so a wrong role
// literal fails here rather than in a user's browser.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ComponentType } from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { AuthUser } from '../types/auth';

// The guards read the session straight from useAuth; stubbing the context module
// is the only seam that drives every branch without a real login round-trip.
const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => mockUseAuth() as unknown,
}));

import ProtectedRoute from '../components/ProtectedRoute';
import AdminRoute from '../components/AdminRoute';
import HRRoute from '../components/HRRoute';
import SuperAdminRoute from '../components/SuperAdminRoute';
import PlatformOwnerRoute from '../components/PlatformOwnerRoute';

const GUARDED_TEXT = 'guarded content';
const LOGIN_TEXT = 'login page';
const DASHBOARD_TEXT = 'dashboard page';

function makeUser(roles: string[]): AuthUser {
  return {
    user_id: '11111111-1111-1111-1111-111111111111',
    full_name: 'Test User',
    email: 'test@example.com',
    roles,
  };
}

function setSession(state: {
  isAuthenticated: boolean;
  isInitializing: boolean;
  user: AuthUser | null;
}): void {
  mockUseAuth.mockReturnValue(state);
}

/** Mount a guard exactly the way App.tsx does — as a layout route with children. */
function renderGuard(Guard: ComponentType): void {
  render(
    <MemoryRouter initialEntries={['/guarded']}>
      <Routes>
        <Route element={<Guard />}>
          <Route path="/guarded" element={<div>{GUARDED_TEXT}</div>} />
        </Route>
        <Route path="/login" element={<div>{LOGIN_TEXT}</div>} />
        <Route path="/dashboard" element={<div>{DASHBOARD_TEXT}</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

// The exact role each guard admitted BEFORE the RoleRoute consolidation.
// `denied` lists the other hierarchy roles: the guards check literal membership,
// so an account holding only `platform_owner` is NOT admitted by AdminRoute —
// platform owners are granted `admin` at provisioning time, not by the guard.
const ROLE_GUARDS: ReadonlyArray<{
  name: string;
  Guard: ComponentType;
  allowed: string;
  denied: readonly string[];
}> = [
  {
    name: 'AdminRoute',
    Guard: AdminRoute,
    allowed: 'admin',
    denied: ['platform_owner', 'super_admin', 'hr_manager', 'candidate'],
  },
  {
    name: 'HRRoute',
    Guard: HRRoute,
    allowed: 'hr_manager',
    denied: ['platform_owner', 'super_admin', 'admin', 'candidate'],
  },
  {
    name: 'SuperAdminRoute',
    Guard: SuperAdminRoute,
    allowed: 'super_admin',
    denied: ['platform_owner', 'hr_manager', 'admin', 'candidate'],
  },
  {
    name: 'PlatformOwnerRoute',
    Guard: PlatformOwnerRoute,
    allowed: 'platform_owner',
    denied: ['super_admin', 'hr_manager', 'admin', 'candidate'],
  },
];

const ALL_GUARDS: ReadonlyArray<{ name: string; Guard: ComponentType }> = [
  { name: 'ProtectedRoute', Guard: ProtectedRoute },
  ...ROLE_GUARDS.map(({ name, Guard }) => ({ name, Guard })),
];

beforeEach(() => {
  mockUseAuth.mockReset();
});

describe('route guards — session probe still running', () => {
  it.each(ALL_GUARDS)('$name shows the spinner and does not redirect', ({ Guard }) => {
    setSession({ isAuthenticated: false, isInitializing: true, user: null });
    renderGuard(Guard);

    expect(screen.getByRole('status', { name: 'Checking session' })).toBeInTheDocument();
    expect(screen.queryByText(LOGIN_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(GUARDED_TEXT)).not.toBeInTheDocument();
  });
});

describe('route guards — unauthenticated', () => {
  it.each(ALL_GUARDS)('$name redirects to /login', ({ Guard }) => {
    setSession({ isAuthenticated: false, isInitializing: false, user: null });
    renderGuard(Guard);

    expect(screen.getByText(LOGIN_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(GUARDED_TEXT)).not.toBeInTheDocument();
  });
});

describe('ProtectedRoute — authentication only, no role requirement', () => {
  it('admits a candidate with no special roles', () => {
    setSession({ isAuthenticated: true, isInitializing: false, user: makeUser(['candidate']) });
    renderGuard(ProtectedRoute);

    expect(screen.getByText(GUARDED_TEXT)).toBeInTheDocument();
  });

  it('admits a session whose user has an empty roles array', () => {
    setSession({ isAuthenticated: true, isInitializing: false, user: makeUser([]) });
    renderGuard(ProtectedRoute);

    expect(screen.getByText(GUARDED_TEXT)).toBeInTheDocument();
  });
});

describe('role guards — permitted role set', () => {
  it.each(ROLE_GUARDS)('$name admits a user holding $allowed', ({ Guard, allowed }) => {
    setSession({ isAuthenticated: true, isInitializing: false, user: makeUser([allowed]) });
    renderGuard(Guard);

    expect(screen.getByText(GUARDED_TEXT)).toBeInTheDocument();
  });

  it.each(ROLE_GUARDS)(
    '$name admits a user holding $allowed alongside other roles',
    ({ Guard, allowed }) => {
      setSession({
        isAuthenticated: true,
        isInitializing: false,
        user: makeUser(['candidate', allowed]),
      });
      renderGuard(Guard);

      expect(screen.getByText(GUARDED_TEXT)).toBeInTheDocument();
    },
  );
});

describe('role guards — denied role set', () => {
  for (const { name, Guard, denied } of ROLE_GUARDS) {
    it.each(denied)(`${name} redirects a %s to /dashboard`, (role) => {
      setSession({ isAuthenticated: true, isInitializing: false, user: makeUser([role]) });
      renderGuard(Guard);

      expect(screen.getByText(DASHBOARD_TEXT)).toBeInTheDocument();
      expect(screen.queryByText(GUARDED_TEXT)).not.toBeInTheDocument();
    });
  }

  // Fail-closed: an authenticated session whose profile fetch has not landed
  // has no roles to check, and must not be waved into a privileged console.
  it.each(ROLE_GUARDS)('$name redirects when the session has no user profile', ({ Guard }) => {
    setSession({ isAuthenticated: true, isInitializing: false, user: null });
    renderGuard(Guard);

    expect(screen.getByText(DASHBOARD_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(GUARDED_TEXT)).not.toBeInTheDocument();
  });
});
