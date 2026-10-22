// AppShell — authenticated layout shell used by all in-app pages.
// Layout: fixed LEFT SIDEBAR (role-aware nav + brand + user menu) on lg+,
// a slim top bar (mobile menu + language + live notifications), and a
// <main> content area. Collapses to a drawer on mobile.
//
// Visual language: a solid card-coloured sidebar on the paper canvas, pill nav
// items, and the blue highlight tint (--ui-highlight) marking where you are. All
// in-app authenticated routes render as children.

import { useLayoutEffect, useRef, useState } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  History,
  FileText,
  LogOut,
  Menu,
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
import CopilotLauncher from '@/components/agent/CopilotLauncher';
import { toast } from '@/lib/toast';
import { useLiveRefresh } from '@/lib/liveRefresh';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { cn } from '@/lib/utils';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import ThemeToggle from '@/components/ThemeToggle';
import ModeSwitch from '@/components/ModeSwitch';
import {
  activeNavPath,
  homePathFor,
  isCandidateOnly,
  visibleNavSections,
  type NavItem,
} from './navSections';
import { appPath } from '@/lib/safeUrl';
import { readPref, writePref } from '@/lib/localPref';

// The interview-language localStorage key (separate concern from UI language)
const INTERVIEW_LANGUAGE_KEY = 'intants:interview-language';
const SIDEBAR_W = 264;
const SIDEBAR_COLLAPSED_W = 72;
// Persisted collapse preference (desktop only; the mobile drawer is unaffected).
const SIDEBAR_COLLAPSED_KEY = 'intants:sidebar-collapsed';

// ── Role label helper ─────────────────────────────────────────────────────────

const ROLE_PRIORITY = [
  'platform_owner',
  'super_admin',
  'admin',
  'hr_manager',
  'interviewer',
] as const;

/** Returns the most-privileged display label for the user's role set. */
function getRoleLabel(roles: string[], candidateLabel: string): string {
  for (const r of ROLE_PRIORITY) {
    if (roles.includes(r)) {
      switch (r) {
        case 'platform_owner':
          return 'Platform Owner';
        case 'super_admin':
          return 'Super Admin';
        case 'admin':
          return 'Platform Admin';
        case 'hr_manager':
          return 'HR Manager';
        case 'interviewer':
          return 'Interviewer';
      }
    }
  }
  return candidateLabel;
}

/** Accent color for the role label, matching the design. */
/**
 * The colour of the role label under the wordmark. Returns a CSS variable, not
 * a hex: these were pastels chosen against a black sidebar (#f0a6c8 for the
 * platform owner), and a pastel on a white sidebar is a label you have to lean
 * in to read. Each token carries a per-mode value — see --ui-role-* in
 * index.css — so the label keeps its identity and its contrast in both.
 */
function getRoleAccent(roles: string[]): string {
  if (roles.includes('platform_owner')) return 'var(--ui-role-platform)';
  if (roles.includes('super_admin')) return 'var(--ui-role-super)';
  if (roles.includes('admin')) return 'var(--ui-role-admin)';
  if (roles.includes('hr_manager')) return 'var(--ui-role-hr)';
  // No dedicated token exists for this role yet — reusing the HR green rather
  // than inventing one: an interviewer is company staff on the same tier as an
  // HR manager, just narrower in scope.
  if (roles.includes('interviewer')) return 'var(--ui-role-hr)';
  return 'var(--ui-faint)';
}

/** Derive initials from a display name for the avatar fallback */
function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || parts[0] === '') return 'U';
  if (parts.length === 1) return (parts[0]?.[0] ?? 'U').toUpperCase();
  return ((parts[0]?.[0] ?? '') + (parts[parts.length - 1]?.[0] ?? '')).toUpperCase();
}

// Nav item definitions, role scoping and isCandidateOnly live in
// ./navSections.tsx — see that file for why. Split out so this component
// module exports only components (react-refresh/only-export-components) and
// so the role-scoping test has a UI-free surface to assert against.

// ── Sidebar nav link (vertical) ───────────────────────────────────────────────

function SideNavLink({
  item,
  active,
  onNavigate,
  collapsed = false,
}: {
  item: NavItem;
  /** Decided once for the whole sidebar (activeNavPath), not per link. */
  active: boolean;
  onNavigate?: () => void;
  collapsed?: boolean;
}) {
  const { t } = useTranslation();
  const label = item.labelKey ? t(item.labelKey) : item.label;
  return (
    <Link
      to={item.to}
      aria-current={active ? 'page' : undefined}
      onClick={onNavigate}
      // When collapsed the label is hidden, so expose it as a tooltip + a11y name.
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      className={cn(
        'flex items-center gap-3 rounded-full py-2.5 text-[14px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        collapsed ? 'justify-center px-0' : 'px-3.5',
        // The highlight tint carries its own ink, each valued per mode.
        active
          ? 'bg-[var(--ui-highlight)] font-semibold text-[var(--ui-highlight-ink)]'
          : 'font-medium text-[var(--ui-soft)] hover:bg-[var(--ui-inset)] hover:text-foreground',
      )}
    >
      {item.icon}
      {!collapsed && <span className="truncate">{label}</span>}
    </Link>
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
  if (collapsed) return <div className="mx-3 my-2 h-px bg-[var(--ui-inset)]" aria-hidden="true" />;
  return (
    <p className="px-3.5 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--ui-faint)]">
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

  const logoutMutation = useMutation({
    mutationFn: () => logout(),
    onSettled: () => {
      queryClient.clear();
      clearAuth();
      void navigate('/login', { replace: true });
    },
    onError: () => toast.error(t('error.generic')),
  });

  const displayName = user?.full_name ?? t('shell.userFallback');
  const roles = user?.roles ?? [];
  const roleLabel = getRoleLabel(roles, t('shell.roleCandidate'));
  const candidateOnly = isCandidateOnly(roles);

  return (
    <div className="border-t border-border p-3">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={collapsed ? displayName : t('nav.userMenu')}
            title={collapsed ? displayName : undefined}
            className={cn(
              'group flex w-full items-center rounded-[14px] py-2 text-left transition-colors hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] data-[state=open]:bg-[var(--ui-inset)]',
              collapsed ? 'justify-center px-0' : 'gap-2.5 px-2',
            )}
          >
            <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-[image:var(--ui-grad-avatar)] text-[13px] font-semibold text-white">
              {getInitials(displayName)}
            </span>
            {!collapsed && (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-semibold text-foreground">
                    {displayName}
                  </span>
                  <span className="block truncate text-[12px] text-muted-foreground">
                    {roleLabel}
                  </span>
                </span>
                <ChevronDown
                  size={16}
                  aria-hidden="true"
                  className="flex-none text-[var(--ui-faint)] transition-transform duration-150 group-data-[state=open]:rotate-180"
                />
              </>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          sideOffset={6}
          className={collapsed ? 'w-56' : 'w-[var(--radix-dropdown-menu-trigger-width)]'}
        >
          <DropdownMenuItem asChild onSelect={() => onNavigate?.()}>
            <Link to="/profile">
              <User aria-hidden="true" /> {t('shell.profile')}
            </Link>
          </DropdownMenuItem>
          {/* Candidate-only, same rule as the candidate nav section above.
              These are the signed-in person's OWN CV and OWN interview
              history. An HR manager or platform owner has neither, so the
              links led to empty pages. This menu was the second place
              candidate items leaked to staff; the main nav was the first. */}
          {candidateOnly && (
            <>
              <DropdownMenuItem asChild onSelect={() => onNavigate?.()}>
                <Link to="/resume">
                  <FileText aria-hidden="true" /> {t('nav.resume')}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild onSelect={() => onNavigate?.()}>
                <Link to="/history">
                  <History aria-hidden="true" /> {t('nav.history')}
                </Link>
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            destructive
            disabled={logoutMutation.isPending}
            onSelect={() => logoutMutation.mutate()}
          >
            <LogOut aria-hidden="true" /> {logoutMutation.isPending ? '…' : t('nav.logout')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ── Sidebar content (brand + nav + user) ──────────────────────────────────────

// Where the sidebar's menu was scrolled to. Module scope on purpose: some menu
// items live under a different route group (HR → "My interviews" at
// /interviewer is under InterviewerRoute, not HRRoute), and each group mounts
// its own AppShell, so a click there rebuilt the sidebar and threw the menu
// back to the top (QA ANTHIRE-013). Kept per-surface so the desktop rail and
// the mobile drawer do not fight over one value.
const sidebarScroll: Record<string, number> = {};

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
  const { t } = useTranslation();
  const roles = user?.roles ?? [];
  const roleLabel = getRoleLabel(roles, t('shell.roleCandidate'));
  const accent = getRoleAccent(roles);
  const candidateOnly = isCandidateOnly(roles);
  const { pathname } = useLocation();
  const sections = visibleNavSections(roles);
  const activeTo = activeNavPath(
    pathname,
    sections.flatMap((section) => section.items.map((item) => item.to)),
  );
  const navRef = useRef<HTMLElement>(null);
  const scrollKey = onToggle ? 'rail' : 'drawer';
  useLayoutEffect(() => {
    // Before paint, so a remount never flashes the top of the menu first.
    if (navRef.current) navRef.current.scrollTop = sidebarScroll[scrollKey] ?? 0;
  }, [scrollKey]);

  return (
    <div className="flex h-full flex-col">
      {/* Brand + collapse toggle (desktop) */}
      <div
        className={cn(
          'flex px-4 py-5',
          collapsed ? 'flex-col items-center gap-3' : 'items-center gap-2.5',
        )}
      >
        <Link
          /* Role home, not /dashboard: clicking the wordmark was the last
             affordance dropping staff onto the candidate dashboard. */
          to={homePathFor(roles)}
          onClick={onNavigate}
          className="flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          aria-label="AntHire"
        >
          {/* The mark is the fixed brand surface in both modes: it is the
              brand, not a surface that follows the mode. */}
          <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[image:var(--ui-grad-brand)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.16)]">
            <span className="h-3 w-3 rounded-full bg-[var(--ui-brand-ink)]" aria-hidden="true" />
          </span>
          {!collapsed && (
            <span className="flex flex-col leading-tight">
              <span className="font-display text-[17px] font-bold tracking-[-0.4px] text-foreground">
                AntHire
              </span>
              {!candidateOnly && (
                <span
                  className="text-[10.5px] font-semibold uppercase tracking-[0.08em]"
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
            aria-label={collapsed ? t('shell.expandSidebar') : t('shell.collapseSidebar')}
            title={collapsed ? t('shell.expandSidebar') : t('shell.collapseSidebar')}
            className={cn(
              'group hidden h-8 w-8 flex-none items-center justify-center rounded-full',
              'text-muted-foreground transition-colors duration-200',
              'hover:bg-[var(--ui-inset-strong)] hover:text-foreground',
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
      <nav
        ref={navRef}
        aria-label="Primary"
        onScroll={(e) => {
          sidebarScroll[scrollKey] = e.currentTarget.scrollTop;
        }}
        className="flex-1 space-y-6 overflow-y-auto px-3 py-2"
      >
        {sections.map((section, index) => (
          <div key={section.id} className="space-y-1">
            {/* `index > 0`: collapsed, NavSectionLabel renders as a hairline
                divider whose job is separating one group from the previous one.
                Staff no longer render the unlabelled candidate group first, so
                without this their rail opened with a divider above nothing. */}
            {(section.labelKey || section.label) && index > 0 && (
              <NavSectionLabel collapsed={collapsed}>
                {section.labelKey ? t(section.labelKey) : section.label}
              </NavSectionLabel>
            )}
            {(section.labelKey || section.label) && index === 0 && !collapsed && (
              <NavSectionLabel collapsed={false}>
                {section.labelKey ? t(section.labelKey) : section.label}
              </NavSectionLabel>
            )}
            {section.items.map((item) => (
              <SideNavLink
                key={item.to}
                item={item}
                active={item.to === activeTo}
                onNavigate={onNavigate}
                collapsed={collapsed}
              />
            ))}
          </div>
        ))}
      </nav>

      {/* Candidate "ready to practice" promo (design) — hidden when collapsed */}
      {candidateOnly && !collapsed && (
        <div className="px-3 pb-2">
          {/* The highlight tint with its own ink; the button on it is the
              mode's primary, since the tint is light in one mode and dark in
              the other. */}
          <div className="rounded-[14px] bg-[var(--ui-highlight)] p-4 text-[var(--ui-highlight-ink)]">
            <p className="font-display text-[15px] font-bold">{t('shell.promoTitle')}</p>
            <p className="mt-1 text-[12.5px] leading-snug opacity-80">{t('shell.promoBody')}</p>
            <Link
              to="/start"
              onClick={onNavigate}
              className="mt-3 flex w-full items-center justify-center rounded-full bg-primary px-3 py-2 text-[13px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--ui-highlight)]"
            >
              {t('shell.promoCta')}
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
          className="lg:hidden text-muted-foreground hover:text-foreground"
          aria-label={t('nav.openMenu')}
        >
          <Menu className="h-5 w-5" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-[264px] border-border bg-card p-0 text-foreground">
        <SheetTitle className="sr-only">{t('app.name')}</SheetTitle>
        <SidebarContent onNavigate={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
  );
}

// ── Notifications bell (live — backed by data_gateway /notifications) ──────────

/** Compact relative-time label, e.g. "5m", "3h", "2d". */
function relTime(iso: string, justNow: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return justNow;
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return days < 7 ? `${days}d` : new Date(iso).toLocaleDateString();
}

function NotificationsBell() {
  const { t } = useTranslation();
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

  // A notification the bell has not seen before means the backend just changed
  // something — refresh the views that event affects (see lib/liveRefresh).
  useLiveRefresh(data?.items);

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
    // A notification link is a server string: only a same-app path is followed.
    const target = appPath(n.link);
    if (target) void navigate(target);
  }

  // A popover, not a menu: the panel holds a heading, a "mark all read" action and
  // a scrolling list. Radix gives it Escape-to-close, focus return and outside-click.
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t('shell.notifications')}
          className="relative flex h-9 w-9 items-center justify-center rounded-full border border-border bg-card text-[var(--ui-soft)] transition-colors hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] data-[state=open]:bg-[var(--ui-inset)]"
        >
          <Bell size={16} aria-hidden="true" />
          {unread > 0 && (
            <span className="absolute -right-1 -top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-primary px-1 text-[11px] font-bold leading-none text-primary-foreground ring-2 ring-background">
              {unread > 9 ? '9+' : unread}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={8} className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="text-[14px] font-bold text-foreground">{t('shell.notifications')}</span>
          {unread > 0 && (
            <button
              type="button"
              onClick={() => readAllMutation.mutate()}
              className="rounded text-[12.5px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {t('shell.markAllRead')}
            </button>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto p-1">
          {items.length === 0 ? (
            <p className="px-4 py-10 text-center text-[13.5px] text-muted-foreground">
              {t('shell.allCaughtUp')}
            </p>
          ) : (
            items.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => openItem(n)}
                className={cn(
                  'flex w-full items-start gap-3 rounded-[8px] px-3 py-2.5 text-left transition-colors hover:bg-[var(--ui-inset-strong)] focus-visible:bg-[var(--ui-inset-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                  !n.read && 'bg-[var(--ui-info-wash)]',
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
                  <span className="block text-[13px] font-medium text-foreground">{n.title}</span>
                  {n.body && (
                    <span className="mt-0.5 block text-[12px] leading-snug text-muted-foreground">
                      {n.body}
                    </span>
                  )}
                  <span className="mt-1 block text-[11.5px] text-[var(--ui-faint)]">
                    {relTime(n.created_at, t('shell.justNow'))}
                  </span>
                </span>
              </button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ── Top bar (slim) ────────────────────────────────────────────────────────────

function TopBar() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const candidateOnly = isCandidateOnly(user?.roles ?? []);
  const [q, setQ] = useState('');
  // The dashboard already has its one primary (the next-step strip), so a second
  // filled "New interview" beside it would be a second primary on the page.
  const onDashboard = useLocation().pathname === '/dashboard';

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
      <MobileSidebar />

      {/* Search — navigates to Jobs on submit (closest live target).
          Candidate-only: it searches the job board and the candidate's own
          history, so for staff it was a third route onto candidate pages
          after the nav and the user menu. */}
      {candidateOnly && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            // Carry the term: /jobs filters on ?q= (audit CA-M5).
            const term = q.trim();
            void navigate(term ? `/jobs?q=${encodeURIComponent(term)}` : '/jobs');
          }}
          className="hidden h-10 w-[340px] items-center gap-2.5 rounded-full border border-border bg-card px-4 text-[var(--ui-faint)] transition-[border-color,box-shadow] focus-within:border-[var(--accent)] focus-within:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.16)] md:flex"
        >
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('shell.searchPlaceholder')}
            aria-label={t('shell.searchLabel')}
            className="w-full bg-transparent text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none"
          />
        </form>
      )}

      <div className="ml-auto flex items-center gap-2">
        <ModeSwitch />
        {/* The accent picker is cosmetic, so it is the control that yields
            on a phone — the language switcher must not. */}
        <div className="hidden sm:block">
          <ThemeToggle />
        </div>
        <LanguageSwitcher staff={!candidateOnly} />
        <NotificationsBell />
        {candidateOnly && !onDashboard && (
          <Link
            to="/start"
            aria-label={t('shell.newInterview')}
            className="inline-flex h-9 items-center gap-1.5 rounded-full bg-primary px-4 text-[13.5px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <Plus size={15} aria-hidden="true" />
            <span className="hidden sm:inline">{t('shell.newInterview')}</span>
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
  // Guarded: blocked storage used to throw here and take the shell down
  // (audit CA-L18).
  if (!readPref(INTERVIEW_LANGUAGE_KEY)) {
    writePref(INTERVIEW_LANGUAGE_KEY, 'en');
  }

  const location = useLocation();
  // Used to refresh console data after an assistant proposal is committed.
  const queryClient = useQueryClient();

  // Desktop sidebar collapse — persisted so it sticks across reloads/navigation.
  const [collapsed, setCollapsed] = useState(
    () => readPref(SIDEBAR_COLLAPSED_KEY) === '1',
  );
  const toggleSidebar = () => {
    setCollapsed((prev) => {
      const next = !prev;
      writePref(SIDEBAR_COLLAPSED_KEY, next ? '1' : '0');
      return next;
    });
  };

  return (
    <div className="relative min-h-screen bg-background text-foreground">
      {/* Desktop fixed sidebar (collapsible to an icon rail) */}
      <aside
        className="fixed inset-y-0 left-0 z-40 hidden border-r border-border bg-card transition-[width] duration-200 lg:block"
        style={{ width: collapsed ? SIDEBAR_COLLAPSED_W : SIDEBAR_W }}
        aria-label="Sidebar"
      >
        <SidebarContent collapsed={collapsed} onToggle={toggleSidebar} />
      </aside>

      {/* Content column (offset by the sidebar on lg+) */}
      <div
        className={cn(
          'relative z-10 transition-[padding] duration-200',
          collapsed ? 'lg:pl-[72px]' : 'lg:pl-[264px]',
        )}
      >
        <TopBar />
        {/* pb-28 reserves a lane under the last card for the floating
            "Ask assistant" button (bottom-6, ~44px tall), so whatever sits at
            the bottom of a page can always be scrolled clear of it. */}
        <main className="mx-auto w-full max-w-[1440px] px-4 pb-28 pt-8 sm:px-6 lg:px-10">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      {/* Console assistant. One mount here covers every authenticated console;
          it self-hides for roles with no copilot (a plain candidate) and for
          deployments with no model key. Committing a proposal invalidates the
          whole query cache so the console reflects the change immediately —
          coarse, but a proposal commit is rare and the alternative is threading
          a refetch callback through every page. */}
      <CopilotLauncher onCommitted={() => void queryClient.invalidateQueries()} />
    </div>
  );
}
