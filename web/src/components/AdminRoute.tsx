// AdminRoute — extends ProtectedRoute to also require the 'admin' role.
// Non-admin authenticated users are redirected to /dashboard.
// While isInitializing, shows a loading spinner (no premature redirect).

import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function AdminRoute() {
  const { isAuthenticated, isInitializing, user } = useAuth();

  if (isInitializing) {
    return (
      <main
        className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-50 to-violet-50"
        aria-label="Loading"
      >
        <div
          className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-600 border-t-transparent"
          role="status"
          aria-label="Checking session"
        />
      </main>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!user?.roles.includes('admin')) {
    return <Navigate to="/dashboard" replace />;
  }

  return <Outlet />;
}
