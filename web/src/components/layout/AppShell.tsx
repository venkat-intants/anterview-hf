// AppShell — authenticated layout shell used by all in-app pages.
// Layout: fixed LEFT SIDEBAR (role-aware nav + brand + user menu) on lg+,
// a slim top bar (mobile menu + language + live notifications), and a
// <main> content area. Collapses to a drawer on mobile.
//
// Visual language: obsidian sidebar, Signal-Blue (var(--accent)) active treatment,
// glass surfaces. All in-app authenticated routes render as children.

import { useState } from 'react';
import { Link, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import { AuroraBackground } from '@/components/fx';
import {
  LayoutDashboard,
  Briefcase,
  History,
  FileText,
  LogOut,
  Menu,
  BarChart2,
  ClipboardList,
  TrendingUp,
  Upload,
  Building2,
  Users,
  FileSearch,
  Video,
  Bell,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Search,
  Plus,
  User,
} from 'lucide-react';
import { logout } from '@/api/auth';
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from '@/api/notifications';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { cn } from '@/lib/utils';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import ThemeToggle from '@/components/ThemeToggle';

// The interview-language localStorage key (separate concern from UI language)
const INTERVIEW_LANGUAGE_KEY = 'intants:interview-language';
const SIDEBAR_W = 256;
const SIDEBAR_COLLAPSED_W = 72;
// Persisted collapse preference (desktop only; the mobile drawer is unaffected).
const SIDEBAR_COLLAPSED_KEY = 'intants:sidebar-collapsed';

// ── Role label helper ─────────────────────────────────────────────────────────

const ROLE_PRIORITY = ['platform_owner', 'super_admin', 'admin', 'hr_manager'] as const;

/** Returns the most-privileged display label for the user's role set. */
function getRoleLabel(roles: string[]): string {
  for (const r of ROLE_PRIORITY) {
    if (roles.includes(r)) {
      switch (r) {
        case 'platform_owner': return 'Platform Owner';
        case 'super_admin': return 'Super Admin';
        case 'admin': return 'Platform Admin';
        case 'hr_manager': return 'HR Manager';
      }
    }
  }
  return 'Candidate';
}

/** Accent color for the role label, matching the design. */
function getRoleAccent(roles: string[]): string {
  if (roles.includes('platform_owner')) return '#f0a6c8';
  if (roles.includes('super_admin')) return '#c89ce8';
  if (roles.includes('admin')) return '#60a5fa';
  if (roles.includes('hr_manager')) return '#27c93f';
  return '#70757c';
}

const PRIVILEGED_ROLES = ['platform_owner', 'super_admin', 'admin', 'hr_manager'];

/** True when the user holds no privileged role (plain candidate). */
function isCandidateOnly(roles: string[]): boolean {
  return !roles.some((r) => PRIVILEGED_ROLES.includes(r));
}

/** Derive initials from a display name for the avatar fallback */
function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || parts[0] === '') return 'U';
  if (parts.length === 1) return (parts[0]?.[0] ?? 'U').toUpperCase();
  return ((parts[0]?.[0] ?? '') + (parts[parts.length - 1]?.[0] ?? '')).toUpperCase();
}

// ── Nav item definitions ──────────────────────────────────────────────────────

interface NavItem {
  to: string;
  /** i18n key (resolved via t) OR a literal label when labelKey is absent */
  labelKey?: string;
  label?: string;
  icon: React.ReactNode;
}

const ICON = 'h-[18px] w-[18px]';

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

// ── Sidebar nav link (vertical) ───────────────────────────────────────────────

function SideNavLink({
  item,
  onNavigate,
  collapsed = false,
}: {
  item: NavItem;
  onNavigate?: () => void;
  collapsed?: boolean;
}) {
  const { t } = useTranslation();
  const label = item.labelKey ? t(item.labelKey) : item.label;
  return (
    <NavLink
      to={item.to}
      end={item.to === '/hr'}
      onClick={onNavigate}
      // When collapsed the label is hidden, so expose it as a tooltip + a11y name.
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-3 rounded-[10px] py-2 text-[13.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          collapsed ? 'justify-center px-0' : 'px-3',
          isActive
            ? 'bg-[rgba(var(--accent-rgb),0.14)] text-white'
            : 'text-[#888b91] hover:bg-white/[0.04] hover:text-white',
        )
      }
    >
      {item.icon}
      {!collapsed && <span className="truncate">{label}</span>}
    </NavLink>
  );
}

function NavSectionLabel({
  children,
  collapsed = false,
}: {
  children: React.ReactNode;
  collapsed?: boolean;
}) {
  // Collapsed: a thin divider keeps the visual grouping without the text label.
  if (collapsed) return <div className="mx-3 my-2 h-px bg-white/[0.06]" aria-hidden="true" />;
  return (
    <p className="px-3 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-[1.2px] text-[#5a5f66]">
      {children}
    </p>
  );
}

// ── Sidebar footer user menu (opens upward) ───────────────────────────────────

function SidebarUser({
  onNavigate,
  collapsed = false,
}: {
  onNavigate?: () => void;
  collapsed?: boolean;
}) {
  const { t } = useTranslation();
  const { user, clearAuth } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const logoutMutation = useMutation({
    mutationFn: () => logout(),
    onSettled: () => {
      queryClient.clear();
      clearAuth();
      void navigate('/login', { replace: true });
    },
    onError: () => toast.error(t('error.generic')),
  });

  const displayName = user?.full_name ?? 'User';
  const roleLabel = getRoleLabel(user?.roles ?? []);

  return (
    <div className="relative border-t border-white/[0.06] p-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={collapsed ? displayName : t('nav.userMenu')}
        title={collapsed ? displayName : undefined}
        className={cn(
          'flex w-full items-center rounded-[12px] py-2 text-left hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          collapsed ? 'justify-center px-0' : 'gap-2.5 px-2',
        )}
      >
        <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-[linear-gradient(135deg,var(--accent),#a887dc)] text-[13px] font-semibold text-white">
          {getInitials(displayName)}
        </span>
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-semibold text-white">{displayName}</span>
              <span className="block truncate text-[11px] text-[#70757c]">{roleLabel}</span>
            </span>
            <ChevronDown size={14} aria-hidden="true" className="flex-none text-[#70757c]" />
          </>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className={cn(
              'absolute bottom-[calc(100%-4px)] z-40 overflow-hidden rounded-[12px] border border-white/[0.1] bg-[#0f0f10] p-1 shadow-2xl',
              collapsed ? 'left-2 w-56' : 'left-3 right-3',
            )}
          >
            <Link
              to="/profile"
              role="menuitem"
              onClick={() => { setOpen(false); onNavigate?.(); }}
              className="flex items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px] text-[#b8babf] hover:bg-white/[0.06] hover:text-white"
            >
              <User size={15} aria-hidden="true" /> Profile
            </Link>
            <Link
              to="/resume"
              role="menuitem"
              onClick={() => { setOpen(false); onNavigate?.(); }}
              className="flex items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px] text-[#b8babf] hover:bg-white/[0.06] hover:text-white"
            >
              <FileText size={15} aria-hidden="true" /> {t('nav.resume')}
            </Link>
            <Link
              to="/history"
              role="menuitem"
              onClick={() => { setOpen(false); onNavigate?.(); }}
              className="flex items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px] text-[#b8babf] hover:bg-white/[0.06] hover:text-white"
            >
              <History size={15} aria-hidden="true" /> {t('nav.history')}
            </Link>
            <div className="my-1 h-px bg-white/[0.06]" role="separator" />
            <button
              type="button"
              role="menuitem"
              onClick={() => { setOpen(false); logoutMutation.mutate(); }}
              disabled={logoutMutation.isPending}
              className="flex w-full items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px] text-[#e6714f] hover:bg-white/[0.04] disabled:opacity-50"
            >
              <LogOut size={15} aria-hidden="true" /> {logoutMutation.isPending ? '…' : t('nav.logout')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ── Sidebar content (brand + nav + user) ──────────────────────────────────────

function SidebarContent({
  onNavigate,
  collapsed = false,
  onToggle,
}: {
  onNavigate?: () => void;
  collapsed?: boolean;
  onToggle?: () => void;
}) {
  const { user } = useAuth();
  const roles = user?.roles ?? [];
  const roleLabel = getRoleLabel(roles);
  const accent = getRoleAccent(roles);
  const candidateOnly = isCandidateOnly(roles);

  return (
    <div className="flex h-full flex-col">
      {/* Brand + collapse toggle (desktop) */}
      <div
        className={cn(
          'flex px-3 py-5',
          collapsed ? 'flex-col items-center gap-3' : 'items-center gap-2.5',
        )}
      >
        <Link
          to="/dashboard"
          onClick={onNavigate}
          className="flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          aria-label="Anterview"
        >
          <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[linear-gradient(135deg,#112d72,#a887dc)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]">
            <span className="h-2.5 w-2.5 rounded-full bg-white" aria-hidden="true" />
          </span>
          {!collapsed && (
            <span className="flex flex-col leading-tight">
              <span className="text-[15px] font-semibold tracking-[-0.4px] text-white">Anterview</span>
              {!candidateOnly && (
                <span
                  className="text-[10px] font-semibold uppercase tracking-[1.2px]"
                  style={{ color: accent }}
                >
                  {roleLabel}
                </span>
              )}
            </span>
          )}
        </Link>
        {/* Collapse/expand control — desktop only (the mobile drawer omits onToggle). */}
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={cn(
              // Distinctive glass control: rounded square, inner highlight, and an
              // accent-tinted glow ring on hover (Signal-Blue design language).
              'group hidden h-8 w-8 flex-none items-center justify-center rounded-[10px]',
              'border border-white/10 bg-white/[0.05] text-[#b8babf]',
              'shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-all duration-200',
              'hover:border-[rgba(var(--accent-rgb),0.55)] hover:bg-[rgba(var(--accent-rgb),0.16)] hover:text-white',
              'hover:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.12),0_6px_16px_-4px_rgba(var(--accent-rgb),0.45)]',
              'active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] lg:inline-flex',
              !collapsed && 'ml-auto',
            )}
          >
            {collapsed ? (
              <ChevronRight
                size={16}
                aria-hidden="true"
                className="transition-transform duration-200 group-hover:translate-x-0.5"
              />
            ) : (
              <ChevronLeft
                size={16}
                aria-hidden="true"
                className="transition-transform duration-200 group-hover:-translate-x-0.5"
              />
            )}
          </button>
        )}
      </div>

      {/* Nav */}
      <nav aria-label="Primary" className="flex-1 space-y-5 overflow-y-auto px-3 py-2">
        <div className="space-y-1">
          {CANDIDATE_NAV.map((item) => (
            <SideNavLink key={item.to} item={item} onNavigate={onNavigate} collapsed={collapsed} />
          ))}
        </div>

        {roles.includes('hr_manager') && (
          <div className="space-y-1">
            <NavSectionLabel collapsed={collapsed}>Hiring</NavSectionLabel>
            {HR_NAV.map((item) => (
              <SideNavLink key={item.to} item={item} onNavigate={onNavigate} collapsed={collapsed} />
            ))}
          </div>
        )}

        {roles.includes('admin') && (
          <div className="space-y-1">
            <NavSectionLabel collapsed={collapsed}>Admin</NavSectionLabel>
            {ADMIN_NAV.map((item) => (
              <SideNavLink key={item.to} item={item} onNavigate={onNavigate} collapsed={collapsed} />
            ))}
          </div>
        )}

        {roles.includes('platform_owner') && (
          <div className="space-y-1">
            <NavSectionLabel collapsed={collapsed}>Platform</NavSectionLabel>
            {PLATFORM_NAV.map((item) => (
              <SideNavLink key={item.to} item={item} onNavigate={onNavigate} collapsed={collapsed} />
            ))}
          </div>
        )}

        {roles.includes('super_admin') && (
          <div className="space-y-1">
            <NavSectionLabel collapsed={collapsed}>Company</NavSectionLabel>
            {SUPER_NAV.map((item) => (
              <SideNavLink key={item.to} item={item} onNavigate={onNavigate} collapsed={collapsed} />
            ))}
          </div>
        )}
      </nav>

      {/* Candidate "ready to practice" promo (design) — hidden when collapsed */}
      {candidateOnly && !collapsed && (
        <div className="px-3 pb-2">
          <div className="rounded-[16px] border border-[rgba(var(--accent-rgb),0.25)] bg-[linear-gradient(160deg,#001b33,#030719)] p-4">
            <p className="text-[13px] font-semibold text-white">Ready to practice?</p>
            <p className="mt-1 text-[12px] leading-snug text-[#888b91]">
              Your next mock interview is one tap away.
            </p>
            <Link
              to="/start"
              onClick={onNavigate}
              className="mt-3 flex w-full items-center justify-center rounded-[10px] bg-white px-3 py-2 text-[13px] font-semibold text-black transition-colors hover:bg-[#eaeaea] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              Start interview
            </Link>
          </div>
        </div>
      )}

      <SidebarUser onNavigate={onNavigate} collapsed={collapsed} />
    </div>
  );
}

// ── Mobile sidebar (drawer) ───────────────────────────────────────────────────

function MobileSidebar() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden text-[#888b91] hover:text-white"
          aria-label={t('nav.openMenu')}
        >
          <Menu className="h-5 w-5" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-[256px] border-white/[0.06] bg-[#0b0b0c] p-0 text-white">
        <SheetTitle className="sr-only">{t('app.name')}</SheetTitle>
        <SidebarContent onNavigate={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
  );
}

// ── Notifications bell (live — backed by data_gateway /notifications) ──────────

/** Compact relative-time label, e.g. "5m", "3h", "2d". */
function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return days < 7 ? `${days}d` : new Date(iso).toLocaleDateString();
}

function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // retry:false + graceful defaults so a missing endpoint/table never breaks the shell.
  const { data } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications(30),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });

  const items = data?.items ?? [];
  const unread = data?.unread_count ?? 0;

  const readMutation = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  });
  const readAllMutation = useMutation({
    mutationFn: () => markAllNotificationsRead(),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  });

  function openItem(n: NotificationItem) {
    if (!n.read) readMutation.mutate(n.id);
    setOpen(false);
    if (n.link) void navigate(n.link);
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Notifications"
        className="relative rounded-[10px] border border-white/[0.1] bg-white/[0.04] p-2 text-[#b8babf] transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <Bell size={16} aria-hidden="true" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent)] px-1 text-[10px] font-semibold leading-none text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute right-0 top-[calc(100%+8px)] z-40 w-80 overflow-hidden rounded-[12px] border border-white/[0.1] bg-[#0f0f10] shadow-2xl"
          >
            <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
              <span className="text-[13px] font-semibold text-white">Notifications</span>
              {unread > 0 && (
                <button
                  type="button"
                  onClick={() => readAllMutation.mutate()}
                  className="rounded text-[11.5px] text-[#60a5fa] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  Mark all read
                </button>
              )}
            </div>
            <div className="max-h-80 overflow-y-auto">
              {items.length === 0 ? (
                <p className="px-4 py-8 text-center text-[12.5px] text-[#70757c]">
                  You&apos;re all caught up.
                </p>
              ) : (
                items.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    role="menuitem"
                    onClick={() => openItem(n)}
                    className={cn(
                      'flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-white/[0.04] focus-visible:bg-white/[0.04] focus-visible:outline-none',
                      !n.read && 'bg-[rgba(var(--accent-rgb),0.05)]',
                    )}
                  >
                    <span
                      className={cn(
                        'mt-1.5 h-1.5 w-1.5 flex-none rounded-full',
                        n.read ? 'bg-transparent' : 'bg-[var(--accent)]',
                      )}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium text-white">{n.title}</span>
                      {n.body && (
                        <span className="mt-0.5 block text-[12px] leading-snug text-[#888b91]">
                          {n.body}
                        </span>
                      )}
                      <span className="mt-1 block text-[10.5px] text-[#5a5f66]">
                        {relTime(n.created_at)}
                      </span>
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── Top bar (slim) ────────────────────────────────────────────────────────────

function TopBar() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const candidateOnly = isCandidateOnly(user?.roles ?? []);
  const [q, setQ] = useState('');

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-white/[0.06] bg-black/50 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
      <MobileSidebar />

      {/* Search — navigates to Jobs on submit (closest live target) */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void navigate('/jobs');
        }}
        className="hidden w-[300px] items-center gap-2 rounded-[10px] border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-[#70757c] focus-within:border-[var(--accent)] md:flex"
      >
        <Search size={15} aria-hidden="true" />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search roles, history…"
          aria-label="Search"
          className="w-full bg-transparent text-[13px] text-white placeholder:text-[#5a5f66] focus:outline-none"
        />
      </form>

      <div className="ml-auto flex items-center gap-2">
        <ThemeToggle />
        <LanguageSwitcher />
        <NotificationsBell />
        {candidateOnly && (
          <Link
            to="/start"
            className="inline-flex items-center gap-1.5 rounded-[10px] bg-white px-3.5 py-2 text-[13px] font-semibold text-black transition-colors hover:bg-[#eaeaea] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <Plus size={15} aria-hidden="true" /> New interview
          </Link>
        )}
      </div>
    </header>
  );
}

// ── AppShell ─────────────────────────────────────────────────────────────────

interface AppShellProps {
  children: React.ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  // Ensure the interview-language key exists so other pages can read it
  if (!localStorage.getItem(INTERVIEW_LANGUAGE_KEY)) {
    localStorage.setItem(INTERVIEW_LANGUAGE_KEY, 'en');
  }

  const location = useLocation();

  // Desktop sidebar collapse — persisted so it sticks across reloads/navigation.
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1',
  );
  const toggleSidebar = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? '1' : '0');
      return next;
    });
  };

  return (
    <div className="relative min-h-screen bg-black text-white">
      <AuroraBackground />

      {/* Desktop fixed sidebar (collapsible to an icon rail) */}
      <aside
        className="fixed inset-y-0 left-0 z-40 hidden border-r border-white/[0.06] bg-black/40 backdrop-blur-xl transition-[width] duration-200 lg:block"
        style={{ width: collapsed ? SIDEBAR_COLLAPSED_W : SIDEBAR_W }}
        aria-label="Sidebar"
      >
        <SidebarContent collapsed={collapsed} onToggle={toggleSidebar} />
      </aside>

      {/* Content column (offset by the sidebar on lg+) */}
      <div
        className={cn(
          'relative z-10 transition-[padding] duration-200',
          collapsed ? 'lg:pl-[72px]' : 'lg:pl-[256px]',
        )}
      >
        <TopBar />
        <main className="w-full px-4 py-8 sm:px-6 lg:px-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
