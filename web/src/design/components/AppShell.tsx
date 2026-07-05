import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '../lib/cn';
import { AuroraField } from './AuroraField';
import { Avatar } from './primitives';
import {
  LayoutGrid, Briefcase, Kanban, BarChart3, Users, FileText, Globe,
  Building2, Gauge, ListChecks, Bell, ChevronDown, LogOut, Settings, Clock,
  type LucideIcon,
} from './icons';

export type ShellRole = 'candidate' | 'hr' | 'admin' | 'superadmin';

interface NavLink {
  label: string;
  to: string;
  icon: LucideIcon;
}

const NAV: Record<ShellRole, NavLink[]> = {
  candidate: [
    { label: 'Dashboard', to: '/dashboard', icon: LayoutGrid },
    { label: 'Jobs', to: '/jobs', icon: Briefcase },
    { label: 'History', to: '/history', icon: Clock },
    { label: 'Resume', to: '/resume', icon: FileText },
  ],
  hr: [
    { label: 'Console', to: '/hr', icon: LayoutGrid },
    { label: 'Applicants', to: '/hr/applicants', icon: Users },
    { label: 'Exams', to: '/hr/exams', icon: ListChecks },
    { label: 'Pipeline', to: '/hr/pipeline', icon: Kanban },
    { label: 'Analytics', to: '/hr/analytics', icon: BarChart3 },
  ],
  admin: [
    { label: 'Overview', to: '/admin', icon: Gauge },
    { label: 'Interviews', to: '/admin/interviews', icon: FileText },
    { label: 'Analytics', to: '/admin/analytics', icon: BarChart3 },
    { label: 'Jobs & JD', to: '/admin/jobs', icon: Briefcase },
  ],
  superadmin: [{ label: 'Companies', to: '/super-admin', icon: Building2 }],
};

const ROLE_LABEL: Record<ShellRole, string> = {
  candidate: 'Candidate',
  hr: 'HR Manager',
  admin: 'Admin',
  superadmin: 'Super Admin',
};

const LANGS = ['EN', 'हिन्दी', 'తెలుగు'] as const;

interface AppShellProps {
  role: ShellRole;
  /** current route path, to highlight the active nav item */
  active?: string;
  /** logged-in user's name */
  userName?: string;
  children: ReactNode;
}

export function AppShell({ role, active, userName = 'Sneha Reddy', children }: AppShellProps): JSX.Element {
  const [lang, setLang] = useState<number>(0);
  const [langOpen, setLangOpen] = useState<boolean>(false);
  const [menuOpen, setMenuOpen] = useState<boolean>(false);
  const nav = NAV[role];
  const initials = userName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <div className="av-scroll relative min-h-screen bg-black font-sans text-white">
      <AuroraField subtle />

      <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-white/[0.06] bg-black/60 px-6 py-3 backdrop-blur-xl">
        <Link to="/" className="flex items-center gap-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded-lg">
          <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[linear-gradient(135deg,#112d72,#a887dc)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]">
            <span className="h-2.5 w-2.5 rounded-full bg-white" />
          </span>
          <span className="hidden text-[15px] font-semibold tracking-[-0.4px] sm:inline">Anterview</span>
          <span className="ml-1 hidden rounded-pill bg-white/[0.06] px-2 py-0.5 text-[10px] uppercase tracking-[1px] text-[#70757c] sm:inline">
            {ROLE_LABEL[role]}
          </span>
        </Link>

        <nav className="ml-4 hidden items-center gap-1 lg:flex" aria-label="Primary">
          {nav.map((item) => {
            const on = active === item.to;
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-current={on ? 'page' : undefined}
                className={cn(
                  'flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                  on ? 'bg-[rgba(var(--accent-rgb),0.14)] text-white' : 'text-[#888b91] hover:text-white',
                )}
              >
                <Icon size={16} aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {/* language switcher */}
          <div className="relative">
            <button
              onClick={() => { setLangOpen((v) => !v); setMenuOpen(false); }}
              aria-haspopup="menu"
              aria-expanded={langOpen}
              aria-label="Change language"
              className="flex items-center gap-1.5 rounded-[10px] border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-[13px] text-[#b8babf] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <Globe size={15} aria-hidden="true" />
              {LANGS[lang]}
              <ChevronDown size={13} aria-hidden="true" />
            </button>
            {langOpen ? (
              <div role="menu" className="absolute right-0 top-[calc(100%+8px)] z-40 w-32 overflow-hidden rounded-[12px] border border-white/[0.1] bg-[#0f0f10] p-1 shadow-2xl">
                {LANGS.map((l, i) => (
                  <button
                    key={l}
                    role="menuitemradio"
                    aria-checked={i === lang}
                    onClick={() => { setLang(i); setLangOpen(false); }}
                    className={cn('flex w-full items-center rounded-[8px] px-3 py-2 text-left text-[13px]', i === lang ? 'bg-white/[0.06] text-white' : 'text-[#b8babf] hover:bg-white/[0.04]')}
                  >
                    {l}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <button aria-label="Notifications" className="relative rounded-[10px] border border-white/[0.1] bg-white/[0.04] p-2 text-[#b8babf] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
            <Bell size={16} aria-hidden="true" />
            <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
          </button>

          {/* user menu */}
          <div className="relative">
            <button
              onClick={() => { setMenuOpen((v) => !v); setLangOpen(false); }}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="flex items-center gap-2 rounded-pill border border-white/[0.1] bg-white/[0.04] py-1 pl-1 pr-2.5 hover:border-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <Avatar initials={initials} size={28} />
              <span className="hidden text-[13px] font-medium md:inline">{userName}</span>
              <ChevronDown size={13} aria-hidden="true" />
            </button>
            {menuOpen ? (
              <div role="menu" className="absolute right-0 top-[calc(100%+8px)] z-40 w-44 overflow-hidden rounded-[12px] border border-white/[0.1] bg-[#0f0f10] p-1 shadow-2xl">
                <Link to="/change-password" role="menuitem" className="flex items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px] text-[#b8babf] hover:bg-white/[0.04] hover:text-white">
                  <Settings size={15} aria-hidden="true" /> Account
                </Link>
                <Link to="/login" role="menuitem" className="flex items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px] text-[#e6714f] hover:bg-white/[0.04]">
                  <LogOut size={15} aria-hidden="true" /> Sign out
                </Link>
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <main className="relative z-10">{children}</main>
    </div>
  );
}
