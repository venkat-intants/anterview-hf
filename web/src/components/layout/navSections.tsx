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
  AlertTriangle,
  BarChart2,
  Bookmark,
  BookText,
  Briefcase,
  Building2,
  ClipboardCheck,
  ClipboardList,
  Gauge,
  Kanban,
  FileCheck2,
  FileSearch,
  FileText,
  Handshake,
  History,
  LayoutDashboard,
  Library,
  ListChecks,
  Search,
  ShieldCheck,
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

export const PRIVILEGED_ROLES = ['platform_owner', 'super_admin', 'admin', 'hr_manager', 'interviewer'];

/** True when the user holds no privileged role (plain candidate).
 *
 * A guest session is NOT one. Redeeming an interview invitation makes a
 * `guest_candidate` token the app's session, and that token may not read the
 * account's own pages — the server refuses it (`dependencies.reject_role`)
 * because it is minted from a link, not from a sign-in. Without this the
 * sidebar offered a guest "My applications" and "Resume" mid-interview, and
 * the authorization decision arrived as a 403 where an absent menu item
 * belongs.
 */
export function isCandidateOnly(roles: string[]): boolean {
  if (roles.includes('guest_candidate')) return false;
  return !roles.some((r) => PRIVILEGED_ROLES.includes(r));
}

const CANDIDATE_NAV: NavItem[] = [
  { to: '/dashboard', labelKey: 'nav.dashboard', icon: <LayoutDashboard className={ICON} aria-hidden="true" /> },
  { to: '/jobs', labelKey: 'nav.jobs', icon: <Briefcase className={ICON} aria-hidden="true" /> },
  // A literal label, not a key: the other three predate i18n coverage for this
  // section and adding an untranslated key would render the key itself.
  { to: '/applications', label: 'My applications', icon: <FileCheck2 className={ICON} aria-hidden="true" /> },
  { to: '/history', labelKey: 'nav.history', icon: <History className={ICON} aria-hidden="true" /> },
  { to: '/resume', labelKey: 'nav.resume', icon: <FileText className={ICON} aria-hidden="true" /> },
];

const HR_NAV: NavItem[] = [
  { to: '/hr', label: 'Hiring', icon: <Users className={ICON} aria-hidden="true" /> },
  { to: '/hr/requisitions', label: 'Openings', icon: <Kanban className={ICON} aria-hidden="true" /> },
  { to: '/hr/applicants', label: 'Applicants', icon: <FileSearch className={ICON} aria-hidden="true" /> },
  { to: '/hr/exams', label: 'Exams', icon: <ClipboardList className={ICON} aria-hidden="true" /> },
  // PH4-D1 — reusable question banks, shared across every exam.
  { to: '/hr/question-banks', label: 'Question banks', icon: <Library className={ICON} aria-hidden="true" /> },
  // PH4-D1 — this company's own bank-question review queue (D4-2's two-person
  // gate). Without this entry the route was reachable only by typing the URL
  // — undiscoverable, which defeats a review gate nobody can find.
  {
    to: '/hr/question-banks/reviews',
    label: 'Question reviews',
    icon: <ShieldCheck className={ICON} aria-hidden="true" />,
  },
  { to: '/hr/interviews', label: 'Interviews', icon: <Video className={ICON} aria-hidden="true" /> },
  // PH4 Wave 3 (O5) — panel workload, availability and calibration.
  { to: '/hr/panel', label: 'Interview panel', icon: <Gauge className={ICON} aria-hidden="true" /> },
  // PH4-O1 — every application against its stage SLA.
  { to: '/hr/stages-at-risk', label: 'Stages at risk', icon: <AlertTriangle className={ICON} aria-hidden="true" /> },
  { to: '/hr/pipeline', label: 'Pipeline', icon: <TrendingUp className={ICON} aria-hidden="true" /> },
  // PH4 Wave 4 (A3/A4) — offers, and the reusable templates behind them.
  { to: '/hr/offers', label: 'Offers', icon: <Handshake className={ICON} aria-hidden="true" /> },
  { to: '/hr/offer-templates', label: 'Offer templates', icon: <FileText className={ICON} aria-hidden="true" /> },
  // PH5-E2 — the company's document library (policies, handbooks, process
  // notes) the staff copilot searches. Filed here, beside the other reference
  // material (question banks, offer templates) rather than in the pipeline
  // sequence above, because nothing here is a hiring stage. Without this entry
  // the screen was reachable only by typing the URL, so "authorised HR users
  // can upload documents" was not true of the product.
  { to: '/hr/library', label: 'Documents', icon: <BookText className={ICON} aria-hidden="true" /> },
  // PH5-E3 — talent pools (HR-curated lists for future openings) and the
  // rediscovery search behind them. `hr_manager` only: a pool and a
  // rediscovery result both name a candidate, and `candidate_pii` is
  // `{hr_manager}` alone (CLAUDE.md) — a company super_admin is deliberately
  // not a superset of HR, so neither entry is offered to that console. Filed
  // beside Documents on the same reasoning Documents itself records: without
  // a nav entry, "authorised HR users can create and manage talent pools" is
  // not true of the product.
  { to: '/hr/pools', label: 'Talent pools', icon: <Bookmark className={ICON} aria-hidden="true" /> },
  { to: '/hr/rediscovery', label: 'Rediscovery', icon: <Search className={ICON} aria-hidden="true" /> },
  { to: '/hr/analytics', label: 'Analytics', icon: <BarChart2 className={ICON} aria-hidden="true" /> },
  // D4-1: an HR manager can ALSO be assigned as an interviewer. Kept as an item
  // in THIS section (not a new one) so idsFor(['hr_manager']) still resolves to
  // exactly ['hr'] — see navRoleScoping.test.ts. The route itself lives outside
  // /hr (InterviewerRoute admits hr_manager too), which is the one deliberate
  // exception the "every item stays under its section's prefix" test allows.
  { to: '/interviewer', label: 'My interviews', icon: <ClipboardCheck className={ICON} aria-hidden="true" /> },
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

// super_admin — a company's super admin: its HR managers and interviewers.
const SUPER_NAV: NavItem[] = [
  { to: '/superadmin/board', label: 'Hiring board', icon: <Kanban className={ICON} aria-hidden="true" /> },
  // PH3-B2. Second, not last: an opening waiting on approval is blocking
  // somebody's hiring, and burying it under "Team" would make the queue
  // a thing you remember to check rather than a thing you see.
  { to: '/superadmin/approvals', label: 'Approvals', icon: <ClipboardCheck className={ICON} aria-hidden="true" /> },
  // Renamed from "HR Managers": the console at this route now also manages
  // interviewers, so the nav label describes the page rather than one section
  // of it.
  { to: '/superadmin', label: 'Team', icon: <Users className={ICON} aria-hidden="true" /> },
  { to: '/superadmin/decision-reasons', label: 'Decision reasons', icon: <ListChecks className={ICON} aria-hidden="true" /> },
  // PH4-O6: workflow versions waiting for this super admin's approval before
  // HR can publish them (D4-2).
  { to: '/superadmin/workflow-reviews', label: 'Workflow reviews', icon: <FileCheck2 className={ICON} aria-hidden="true" /> },
  // PH4-D1 — the mirror of HR's bank-question review queue (D4-2), so a
  // single-HR company is never blocked on a second HR approver.
  { to: '/superadmin/question-reviews', label: 'Question reviews', icon: <ShieldCheck className={ICON} aria-hidden="true" /> },
  // PH4-A3: offers waiting for this super admin's approval (D4-2).
  { to: '/superadmin/offer-approvals', label: 'Offer approvals', icon: <Handshake className={ICON} aria-hidden="true" /> },
  // PH5-E2 — the same document library as HR's, at this console's own path
  // (/superadmin/library). Its own entry rather than a link into /hr/library:
  // HRRoute admits only hr_manager, so an /hr link from here would bounce.
  { to: '/superadmin/library', label: 'Documents', icon: <BookText className={ICON} aria-hidden="true" /> },
];

// interviewer — company staff who see ONLY interviews assigned to them (D4-1).
const INTERVIEWER_NAV: NavItem[] = [
  { to: '/interviewer', label: 'My interviews', icon: <ClipboardCheck className={ICON} aria-hidden="true" /> },
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
  // A pure interviewer (not also an hr_manager) has no other section — this is
  // their whole console. Hidden when the account is ALSO an HR manager: HR_NAV
  // already carries "My interviews", and showing both would list it twice.
  {
    id: 'interviewer',
    label: 'Interviews',
    items: INTERVIEWER_NAV,
    visibleTo: (roles: string[]) => roles.includes('interviewer') && !roles.includes('hr_manager'),
  },
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
// Order matters: first match wins, so a platform owner who also holds `admin`
// lands on /platform, not /admin.
//
// It is NOT the same order as ROLE_PRIORITY in AppShell, which this comment used
// to claim it mirrored. The two answer different questions and diverge on
// exactly one pair. ROLE_PRIORITY ranks roles for the DISPLAY LABEL and puts
// `admin` above `hr_manager`, so a user holding both is shown "Platform Admin".
// This table puts `hr_manager` first, so that same user lands on /hr.
//
// That divergence is deliberate. Per CLAUDE.md, `admin` is the analytics
// dashboard role and sits OUTSIDE the platform_owner → super_admin → hr_manager
// hierarchy — it is granted alongside a real role, not instead of one. So for
// someone who runs hiring and also has analytics, the hiring console is the
// place to open, while "Platform Admin" is still the most senior thing to call
// them. Both nav sections render either way, so nothing is unreachable.
//
// navRoleScoping.test.ts pins the pair explicitly; change one and it fails.
const HOME_BY_ROLE: ReadonlyArray<readonly [string, string]> = [
  ['platform_owner', '/platform'],
  ['super_admin', '/superadmin'],
  ['hr_manager', '/hr'],
  // After hr_manager: an HR manager who is ALSO an interviewer still lands on
  // /hr, their primary console. A pure interviewer has no earlier match.
  ['interviewer', '/interviewer'],
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
