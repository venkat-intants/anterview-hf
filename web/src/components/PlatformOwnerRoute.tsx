// PlatformOwnerRoute — requires the 'platform_owner' role (the Intants core /
// "super super admin"). Non-owners are redirected to /dashboard; unauthenticated
// to /login.
//
// Thin wrapper over RoleRoute, which holds the shared guard body.

import RoleRoute from './RoleRoute';

export default function PlatformOwnerRoute() {
  return <RoleRoute roles={['platform_owner']} />;
}
