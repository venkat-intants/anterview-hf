// HRRoute — requires the 'hr_manager' role.
// Non-HR authenticated users are redirected to /dashboard; unauthenticated to /login.
//
// Thin wrapper over RoleRoute, which holds the shared guard body.

import RoleRoute from './RoleRoute';

export default function HRRoute() {
  return <RoleRoute roles={['hr_manager']} />;
}
