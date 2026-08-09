// Role-scoped navigation table — the single source of "who sees which nav".
//
// Lives in its own module rather than inside AppShell.tsx for two reasons. The
// mechanical one: exporting constants and helpers from a component file breaks
// React Fast Refresh (react-refresh/only-export-components), and this repo lints
// with --max-warnings 0. The better one: this is data plus a pure function, it
// is what the role-scoping test asserts against, and AppShell.tsx was already a
// 739-line file doing layout, chrome, a user menu and nav assembly at once.
//
// WHAT THIS IS NOT: a security boundary. Authorisation is enforced server-side
// on every endpoint, and client-side by the route guards in web/src/components
// (see RoleRoute.tsx). Hiding a link protects nothing and is not claimed to.
//
// What it IS: least-privilege presentation. CANDIDATE_NAV used to render
// unconditionally for every authenticated user, so HR managers, super admins and
// platform owners were all shown the candidate's own tools — "Resume" (upload
// your CV), "History" (your interview history), "Jobs" (browse and apply). Those
// routes sit behind ProtectedRoute (authentication only), so staff could reach
// them; they were simply empty by construction for anyone who is not a candidate.

import {
  BarChart2,
  Briefcase,
  Building2,
  ClipboardList,
  FileSearch,
  FileText,
  History,
  LayoutDashboard,
  TrendingUp,
  Upload,
  Users,
  Video,
} from 'lucide-react';

export interface NavItem {
  to: string;
  /** i18n key (resolved via t) OR a literal label when labelKey is absent */
  labelKey?: string;
  label?: string;
  icon: React.ReactNode;
}

export interface NavSection {
  /** Stable id — used as the React key and by the role-scoping test. */
  id: string;
  /** Section heading. Absent for the primary group, which renders unlabelled. */
  label?: string;
  items: NavItem[];
  /** Given the user's roles, may this section be rendered? */
  visibleTo: (roles: string[]) => boolean;
}

const ICON = 'h-[18px] w-[18px]';

export const PRIVILEGED_ROLES = ['platform_owner', 'super_admin', 'admin', 'hr_manager'];

/** True when the user holds no privileged role (plain candidate). */
export function isCandidateOnly(roles: string[]): boolean {
  return !roles.some((r) => PRIVILEGED_ROLES.includes(r));
}

const CANDIDATE_NAV: NavItem[] = [
  { to: '/dashboard', labelKey: 'nav.dashboard', icon: <LayoutDashboard className={ICON} aria-hidden="true" /> },
  { to: '/jobs', labelKey: 'nav.jobs', icon: <Briefcase className={ICON} aria-hidden="true" /> },
  { to: '/history', labelKey: 'nav.history', icon: <History className={ICON} aria-hidden="true" /> },
  { to: '/resume', labelKey: 'nav.resume', icon: <FileText className={ICON} aria-hidden="true" /> },
];

const HR_NAV: NavItem[] = [
  { to: '/hr', label: 'Hiring', icon: <Users className={ICON} aria-hidden="true" /> },
  { to: '/hr/applicants', label: 'Applicants', icon: <FileSearch className={ICON} aria-hidden="true" /> },
  { to: '/hr/exams', label: 'Exams', icon: <ClipboardList className={ICON} aria-hidden="true" /> },
  { to: '/hr/interviews', label: 'Interviews', icon: <Video className={ICON} aria-hidden="true" /> },
  { to: '/hr/pipeline', label: 'Pipeline', icon: <TrendingUp className={ICON} aria-hidden="true" /> },
  { to: '/hr/analytics', label: 'Analytics', icon: <BarChart2 className={ICON} aria-hidden="true" /> },
];

const ADMIN_NAV: NavItem[] = [
  { to: '/admin/overview', labelKey: 'nav.adminOverview', icon: <BarChart2 className={ICON} aria-hidden="true" /> },
  { to: '/admin/interviews', labelKey: 'nav.adminInterviews', icon: <ClipboardList className={ICON} aria-hidden="true" /> },
  { to: '/admin/analytics', labelKey: 'nav.adminAnalytics', icon: <TrendingUp className={ICON} aria-hidden="true" /> },
  { to: '/admin/jd', labelKey: 'nav.adminJd', icon: <Upload className={ICON} aria-hidden="true" /> },
];

// platform_owner — the Intants core: companies + their super admins.
const PLATFORM_NAV: NavItem[] = [
  { to: '/platform', label: 'Companies', icon: <Building2 className={ICON} aria-hidden="true" /> },
];

// super_admin — a company's super admin: its HR managers.
const SUPER_NAV: NavItem[] = [
  { to: '/superadmin', label: 'HR Managers', icon: <Users className={ICON} aria-hidden="true" /> },
];

const hasRole = (role: string) => (roles: string[]): boolean => roles.includes(role);

// One table, so "who sees what" is a single readable list rather than five
// inline `roles.includes(...)` guards scattered through JSX. Adding a section
// means adding a row here AND a row to navRoleScoping.test.ts, which asserts the
// complement for every role — so a section added without a visibleTo predicate
// fails the suite instead of quietly appearing for everyone, which is exactly
// how the original bug shipped.
//
// WHY isCandidateOnly RATHER THAN roles.includes('candidate'): staff accounts may
// also carry the candidate role, so testing for its presence would reproduce the
// bug. The predicate already existed and already gated the candidate promo block,
// so reusing it keeps one definition of "is this person a candidate?".
//
// Staff are not stranded by hiding the candidate group: Login.tsx already routes
// by role (platform_owner → /platform, super_admin → /superadmin,
// hr_manager → /hr), so staff land on their own console and never needed it.
export const NAV_SECTIONS: NavSection[] = [
  { id: 'candidate', items: CANDIDATE_NAV, visibleTo: isCandidateOnly },
  { id: 'hr', label: 'Hiring', items: HR_NAV, visibleTo: hasRole('hr_manager') },
  { id: 'admin', label: 'Admin', items: ADMIN_NAV, visibleTo: hasRole('admin') },
  { id: 'platform', label: 'Platform', items: PLATFORM_NAV, visibleTo: hasRole('platform_owner') },
  { id: 'company', label: 'Company', items: SUPER_NAV, visibleTo: hasRole('super_admin') },
];

/** The nav sections a given role set may see, in render order. */
export function visibleNavSections(roles: string[]): NavSection[] {
  return NAV_SECTIONS.filter((section) => section.visibleTo(roles));
}

// ── Where does this user call home? ───────────────────────────────────────────
//
// One answer, because there were three and they disagreed. Login.tsx branched
// on platform_owner / super_admin / hr_manager and fell through to /dashboard;
// RoleRoute.tsx bounced every rejected user to /dashboard; the AppShell brand
// wordmark linked to /dashboard unconditionally. None of them handled `admin`.
//
// That gap became a real defect the moment the candidate nav was scoped: `admin`
// is a privileged role, so an admin-only account (the seeded
// admin.demo@demo.intants.com holds exactly ['admin']) lost the candidate
// section AND still landed on /dashboard — a page with no nav entry matching it.
// Signed in, on a candidate's home, with a sidebar offering only /admin/*.
//
// Order matters and mirrors ROLE_PRIORITY in AppShell: most-privileged wins, so
// a platform owner who also holds `admin` lands on /platform, not /admin.
const HOME_BY_ROLE: ReadonlyArray<readonly [string, string]> = [
  ['platform_owner', '/platform'],
  ['super_admin', '/superadmin'],
  ['hr_manager', '/hr'],
  ['admin', '/admin/overview'],
];

/**
 * The landing route for a role set — where login sends them, where a denied
 * route returns them, and where the brand wordmark points.
 *
 * Candidates (and users still being provisioned) get /dashboard, which is
 * genuinely their home rather than a fallback.
 */
export function homePathFor(roles: string[]): string {
  for (const [role, path] of HOME_BY_ROLE) {
    if (roles.includes(role)) return path;
  }
  return '/dashboard';
}
