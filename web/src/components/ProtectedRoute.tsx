// ProtectedRoute — redirects unauthenticated users to /login.
//
// While the AuthProvider is running its silent-refresh probe (isInitializing),
// a loading spinner is shown instead of redirecting. This prevents a page
// refresh from flashing the login screen before the refresh cookie is checked.

import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function ProtectedRoute() {
  const { isAuthenticated, isInitializing } = useAuth();

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

  return <Outlet />;
}
