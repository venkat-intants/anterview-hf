// App — route definitions with React.lazy code-splitting.
// Authenticated routes are wrapped in AppShell (layout route).
// Public routes render without the shell.
import { Suspense, lazy } from 'react';
import { Routes, Route, Outlet, Navigate, useLocation } from 'react-router-dom';
import ProtectedRoute from './components/ProtectedRoute';
import AdminRoute from './components/AdminRoute';
import PlatformOwnerRoute from './components/PlatformOwnerRoute';
import SuperAdminRoute from './components/SuperAdminRoute';
import HRRoute from './components/HRRoute';
import InterviewSessionRoute from './components/InterviewSessionRoute';
import ErrorBoundary from './components/ErrorBoundary';
import AppShell from './components/layout/AppShell';
import { useAuth } from './context/AuthContext';

// ── Public pages ──────────────────────────────────────────────────────────────
const Landing = lazy(() => import('./pages/Landing'));
const Login = lazy(() => import('./pages/Login'));
const Register = lazy(() => import('./pages/Register'));
const ForgotPassword = lazy(() => import('./pages/ForgotPassword'));
const ResetPassword = lazy(() => import('./pages/ResetPassword'));
const VerifyEmail = lazy(() => import('./pages/VerifyEmail'));
const GoogleCallback = lazy(() => import('./pages/GoogleCallback'));
const NotFound = lazy(() => import('./pages/NotFound'));
// Public applicant exam-taking page (magic-link, no login).
const PublicExam = lazy(() => import('./pages/PublicExam'));
// Public applicant interview landing (magic-link, no login).
const InterviewInvite = lazy(() => import('./pages/InterviewInvite'));

// ── Authenticated shell pages ─────────────────────────────────────────────────
const Dashboard = lazy(() => import('./pages/Dashboard'));
const JobsList = lazy(() => import('./pages/JobsList'));
const StartInterview = lazy(() => import('./pages/StartInterview'));
const History = lazy(() => import('./pages/History'));
const Resume = lazy(() => import('./pages/Resume'));
const Profile = lazy(() => import('./pages/Profile'));
const ProfileView = lazy(() => import('./pages/ProfileView'));

// ── Interview / scorecard (no shell — full-screen experience) ─────────────────
const Interview = lazy(() => import('./pages/Interview'));
const InterviewComplete = lazy(() => import('./pages/InterviewComplete'));
const Scorecard = lazy(() => import('./pages/Scorecard'));

// ── Admin pages (inside AdminRoute + AppShell) ────────────────────────────────
const AdminJobJd = lazy(() => import('./pages/AdminJobJd'));
const AdminOverview = lazy(() => import('./pages/admin/AdminOverview'));
const AdminInterviews = lazy(() => import('./pages/admin/AdminInterviews'));
const AdminInterviewDetail = lazy(() => import('./pages/admin/AdminInterviewDetail'));
const AdminAnalytics = lazy(() => import('./pages/admin/AdminAnalytics'));

// ── HR workflow pages ─────────────────────────────────────────────────────────
const ChangePassword = lazy(() => import('./pages/ChangePassword'));
const PlatformOwnerConsole = lazy(() => import('./pages/superadmin/PlatformOwnerConsole'));
const CompanyAdminConsole = lazy(() => import('./pages/superadmin/CompanyAdminConsole'));
const HRConsole = lazy(() => import('./pages/hr/HRConsole'));
const Applicants = lazy(() => import('./pages/hr/Applicants'));
const Exams = lazy(() => import('./pages/hr/Exams'));
const ExamEditor = lazy(() => import('./pages/hr/ExamEditor'));
const ExamResults = lazy(() => import('./pages/hr/ExamResults'));
const ExamAttemptDetail = lazy(() => import('./pages/hr/ExamAttemptDetail'));
const HRInterviews = lazy(() => import('./pages/hr/HRInterviews'));
const HRPipeline = lazy(() => import('./pages/hr/HRPipeline'));
const HRAnalyticsPage = lazy(() =>
  import('./pages/hr/HRAnalytics').then((m) => ({ default: m.HRAnalyticsPage })),
);

function PageLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div
        className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent"
        role="status"
        aria-label="Loading page"
      />
    </div>
  );
}

/** Layout wrapper: renders <AppShell> around whichever child route matches.
 *  Enforces the bootstrap-password reset: an account with must_change_password
 *  is bounced to /change-password before it can reach any shell page. */
function ShellLayout() {
  const { user } = useAuth();
  const location = useLocation();
  if (user?.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />;
  }
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          {/* Public routes — no shell */}
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          {/* Public email-flow pages — token (when used) in the URL #fragment */}
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/auth/google/callback" element={<GoogleCallback />} />
          {/* Public applicant exam — magic-link token in the URL #fragment */}
          <Route path="/exam" element={<PublicExam />} />
          {/* Public applicant interview landing — magic-link token in the URL #fragment */}
          <Route path="/interview-invite" element={<InterviewInvite />} />

          {/* Authenticated routes rendered INSIDE AppShell */}
          <Route element={<ProtectedRoute />}>
            <Route element={<ShellLayout />}>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/jobs" element={<JobsList />} />
              <Route path="/start" element={<StartInterview />} />
              <Route path="/history" element={<History />} />
              <Route path="/resume" element={<Resume />} />
              <Route path="/profile" element={<Profile />} />
              {/* View another user's profile — HR/admin only (enforced server-side) */}
              <Route path="/u/:userId" element={<ProfileView />} />
            </Route>

            <Route path="/interview/:sessionId/complete" element={<InterviewComplete />} />
            <Route path="/scorecard/:scorecardId" element={<Scorecard />} />

            {/* Forced bootstrap-password reset — standalone, no shell */}
            <Route path="/change-password" element={<ChangePassword />} />
          </Route>

          {/* Live interview — its own guard: a logged-in user passes through; a
              magic-link guest who RELOADED resumes via the httpOnly cookie (no
              login). The dark video-immersion theme is scoped via `.dark`. */}
          <Route element={<InterviewSessionRoute />}>
            <Route
              path="/interview/:sessionId"
              element={
                <div className="dark dark-root min-h-screen bg-background text-foreground">
                  <Interview />
                </div>
              }
            />
          </Route>

          {/* Platform-owner console — manage companies + their super admins */}
          <Route element={<PlatformOwnerRoute />}>
            <Route element={<ShellLayout />}>
              <Route path="/platform" element={<PlatformOwnerConsole />} />
            </Route>
          </Route>

          {/* Company super-admin console — manage the company's HR managers */}
          <Route element={<SuperAdminRoute />}>
            <Route element={<ShellLayout />}>
              <Route path="/superadmin" element={<CompanyAdminConsole />} />
            </Route>
          </Route>

          {/* HR manager console */}
          <Route element={<HRRoute />}>
            <Route element={<ShellLayout />}>
              <Route path="/hr" element={<HRConsole />} />
              <Route path="/hr/applicants" element={<Applicants />} />
              <Route path="/hr/exams" element={<Exams />} />
              <Route path="/hr/exams/:examId" element={<ExamEditor />} />
              <Route path="/hr/exams/:examId/results" element={<ExamResults />} />
              <Route
                path="/hr/exams/:examId/attempts/:attemptId"
                element={<ExamAttemptDetail />}
              />
              <Route path="/hr/interviews" element={<HRInterviews />} />
              <Route path="/hr/pipeline" element={<HRPipeline />} />
              <Route path="/hr/analytics" element={<HRAnalyticsPage />} />
            </Route>
          </Route>

          {/* Admin-only routes — inside AppShell for consistent navigation */}
          <Route element={<AdminRoute />}>
            <Route element={<ShellLayout />}>
              <Route path="/admin/overview" element={<AdminOverview />} />
              <Route path="/admin/interviews" element={<AdminInterviews />} />
              <Route path="/admin/interviews/:sessionId" element={<AdminInterviewDetail />} />
              <Route path="/admin/analytics" element={<AdminAnalytics />} />
              <Route path="/admin/jd" element={<AdminJobJd />} />
            </Route>
          </Route>

          {/* 404 catch-all */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
