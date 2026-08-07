// AdminRoute — extends ProtectedRoute to also require the 'admin' role.
// Non-admin authenticated users are redirected to /dashboard.
// While isInitializing, shows a loading spinner (no premature redirect).
//
// Thin wrapper over RoleRoute, which holds the shared guard body.

import RoleRoute from './RoleRoute';

export default function AdminRoute() {
  return <RoleRoute roles={['admin']} />;
}
