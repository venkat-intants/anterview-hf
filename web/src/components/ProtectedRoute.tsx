// ProtectedRoute — redirects unauthenticated users to /login.
//
// While the AuthProvider is running its silent-refresh probe (isInitializing),
// a loading spinner is shown instead of redirecting. This prevents a page
// refresh from flashing the login screen before the refresh cookie is checked.
//
// Thin wrapper over RoleRoute (no `roles` ⇒ authentication-only gate), which
// holds the one copy of the logic all five route guards share.

import RoleRoute from './RoleRoute';

export default function ProtectedRoute() {
  return <RoleRoute />;
}
