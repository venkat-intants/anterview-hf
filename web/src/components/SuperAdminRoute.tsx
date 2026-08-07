// SuperAdminRoute — requires the 'super_admin' role (a company's super admin,
// one per company). Non-super-admins are redirected to /dashboard;
// unauthenticated to /login. (Platform-wide governance lives behind
// PlatformOwnerRoute.)
//
// Thin wrapper over RoleRoute, which holds the shared guard body.

import RoleRoute from './RoleRoute';

export default function SuperAdminRoute() {
  return <RoleRoute roles={['super_admin']} />;
}
