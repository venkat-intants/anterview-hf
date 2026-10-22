// NotFound sends a signed-in person to THEIR OWN home (audit AD-F16).
//
// It used to send every signed-in user to /dashboard, the candidate home, so a
// staff member who hit a dead link landed on a page with no matching nav entry.
// Login, RoleRoute and the wordmark already use homePathFor for this reason.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => mockUseAuth() as unknown }));

import NotFound from '../pages/NotFound';

function homeLinkFor(roles: string[]): string | null {
  mockUseAuth.mockReturnValue({
    isAuthenticated: true,
    isInitializing: false,
    user: { user_id: 'u-1', full_name: 'Staff', email: 's@example.com', roles },
  });
  const { unmount } = render(
    <MemoryRouter>
      <NotFound />
    </MemoryRouter>,
  );
  const href = screen.getByRole('link', { name: /go to home/i }).getAttribute('href');
  unmount();
  return href;
}

describe('NotFound - the home link follows the role', () => {
  it.each([
    [['hr_manager'], '/hr'],
    [['super_admin'], '/superadmin'],
    [['platform_owner', 'admin'], '/platform'],
    [['interviewer'], '/interviewer'],
    [['admin'], '/admin/overview'],
    [['candidate'], '/dashboard'],
  ])('sends %j to %s', (roles, expected) => {
    expect(homeLinkFor(roles)).toBe(expected);
  });
});
