// InterviewSessionRoute — guard for the live interview page (/interview/:sessionId).
//
// A logged-in user (or a guest who just redeemed the magic link) passes straight
// through. A magic-link guest who RELOADED the page — losing the in-memory token —
// is transparently RESUMED via the httpOnly resume cookie (POST /interview-invite/
// resume); only if that fails do we show a calm "re-open your link" message.
//
// We NEVER bounce an account-less applicant to /login. (Regular ProtectedRoute is
// still used everywhere else.)

import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { resumeInterview } from '../api/publicInterview';

type Phase = 'checking' | 'ready' | 'unresumable';

function DarkFullScreen({ children }: { children: React.ReactNode }) {
  return (
    <div className="dark dark-root min-h-screen bg-background text-foreground">
      <main className="flex min-h-screen items-center justify-center px-6">{children}</main>
    </div>
  );
}

export default function InterviewSessionRoute() {
  const { isAuthenticated, isInitializing, setAuth } = useAuth();
  const [phase, setPhase] = useState<Phase>('checking');

  useEffect(() => {
    // Wait for the silent-refresh probe to settle before deciding.
    if (isInitializing) return;
    if (isAuthenticated) {
      setPhase('ready');
      return;
    }
    // Not authenticated and not initializing → a guest who reloaded mid-interview.
    // Try to resume from the httpOnly cookie (no token, no login).
    let cancelled = false;
    void (async () => {
      try {
        const r = await resumeInterview();
        if (cancelled) return;
        setAuth(r.access_token, {
          user_id: r.user_id,
          full_name: r.full_name,
          email: r.email ?? '',
          roles: r.roles,
          must_change_password: false,
        });
        setPhase('ready');
      } catch {
        if (!cancelled) setPhase('unresumable');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isInitializing, isAuthenticated, setAuth]);

  if (isInitializing || phase === 'checking') {
    return (
      <DarkFullScreen>
        <div
          className="h-10 w-10 animate-spin rounded-full border-4 border-[var(--accent)] border-t-transparent"
          role="status"
          aria-label="Resuming your interview"
        />
      </DarkFullScreen>
    );
  }

  if (isAuthenticated && phase === 'ready') {
    return <Outlet />;
  }

  // Could not resume — calm re-open-link message (never a login wall).
  return (
    <DarkFullScreen>
      <div className="w-full max-w-[440px] rounded-[20px] border border-white/10 bg-white/[0.03] p-8 text-center shadow-[0_24px_80px_rgba(0,0,0,0.5)]">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.14)] text-[22px]">
          🔗
        </div>
        <h1 className="text-[20px] font-semibold tracking-[-0.4px] text-white">
          Re-open your interview link
        </h1>
        <p className="mt-2 text-[14px] leading-relaxed text-[#9aa0a6]">
          Your session timed out on this device. Open the interview link from your
          email again to pick up where you left off — your invite is still valid
          until it expires. No account or sign-in needed.
        </p>
        <p className="mt-4 text-[12.5px] text-[#70757c]">
          Need help?{' '}
          <a href="mailto:support@intants.com" className="text-[#60a5fa] hover:underline">
            support@intants.com
          </a>
        </p>
      </div>
    </DarkFullScreen>
  );
}
