// OpenRoles — real jobs a candidate can apply to, inside their own account.
//
// The half of the bridge that was missing. Applying already worked and the
// applications page already showed what happened next; what a signed-in
// candidate could not do was FIND anything. Their Jobs page listed practice
// roles for mock interviews, and real openings were reachable only by a link
// somebody sent them.
//
// THE DISTINCTION THIS COMPONENT HAS TO CARRY
//
// It sits above the practice catalogue on the same page, and the two mean
// completely different things: one applies you to a job at a company, the
// other starts a mock interview with nobody on the other end. Every label here
// exists to keep those apart — the heading says "real", the company is on
// every card, and the button says "View & apply" rather than anything that
// could be read as practice.
//
// Getting that wrong would be worse than not building this: a candidate who
// thinks they have applied, and has not, finds out by never hearing back.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { listOpenRoles, type OpenRole } from '@/api/applications';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusTag } from '@/design/components/primitives';
import { Building2, Check, ExternalLink } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import { formatMoney, uiLocale } from '@/lib/formatters';

const EMPLOYMENT_KEYS: Record<string, string> = {
  full_time: 'jobs.empFullTime',
  part_time: 'jobs.empPartTime',
  contract: 'jobs.empContract',
  internship: 'jobs.empInternship',
  temporary: 'jobs.empTemporary',
};

/** "Bengaluru · Full-time · Engineering", skipping whatever is missing. */
function metaLine(role: OpenRole, t: TFunction): string {
  const employment = role.employment_type
    ? EMPLOYMENT_KEYS[role.employment_type]
      ? t(EMPLOYMENT_KEYS[role.employment_type])
      : role.employment_type
    : null;
  return [role.location, employment, role.department].filter(Boolean).join(' · ');
}

function experienceLine(role: OpenRole, t: TFunction): string | null {
  const { experience_min_years: lo, experience_max_years: hi } = role;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null) {
    return lo === hi ? t('jobs.expSame', { n: lo }) : t('jobs.expRange', { lo, hi });
  }
  return lo != null ? t('jobs.expMin', { n: lo }) : t('jobs.expMax', { n: hi });
}

function salaryLine(role: OpenRole, t: TFunction, locale: string): string | null {
  const { salary_min: lo, salary_max: hi } = role;
  if (lo == null && hi == null) return null;
  // In the visitor's own locale, not the browser default (audit X18).
  const money = (n: number) => formatMoney(n, role.salary_currency, locale);
  if (lo != null && hi != null) return `${money(lo)} - ${money(hi)}`;
  return lo != null
    ? t('jobs.salaryFrom', { amount: money(lo) })
    : t('jobs.salaryUpTo', { amount: money(hi as number) });
}

const CHIP =
  'inline-flex items-center rounded-full bg-[var(--ui-inset-strong)] px-2.5 py-1 text-[12px] font-medium text-[var(--ui-soft)]';

function RoleCard({ role }: { role: OpenRole }) {
  const { t, i18n } = useTranslation();
  const meta = metaLine(role, t);
  const experience = experienceLine(role, t);
  const salary = salaryLine(role, t, uiLocale(i18n.language));

  return (
    <article className="rounded-card border border-border bg-card p-5 shadow-card transition-[box-shadow,border-color] hover:border-[var(--ui-line-strong)] hover:shadow-elevated">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[16px] font-bold tracking-[-0.2px] text-foreground">{role.title}</h3>
          <p className="mt-1 flex items-center gap-1.5 text-[13.5px] font-medium text-[var(--ui-soft)]">
            <Building2 size={14} aria-hidden="true" />
            {role.company_name}
          </p>
          {meta ? <p className="mt-0.5 text-[13px] text-muted-foreground">{meta}</p> : null}
        </div>
        {salary ? <span className={cn(CHIP, 'text-foreground')}>{salary}</span> : null}
      </div>

      {role.skills.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label={t('jobs.skillsAria')}>
          {role.skills.map((s) => (
            <li key={s} className={CHIP}>
              {s}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <span className="text-[13px] text-muted-foreground">
          {t('jobs.levelLine', { level: role.level })}
          {experience ? ` · ${experience}` : ''}
        </span>

        {/* Applied already: the card says so and offers their own record
            rather than a second application. Inviting one they cannot make
            would waste their time and look broken when it was refused. */}
        {role.already_applied ? (
          <span className="flex items-center gap-3">
            <StatusTag tone="forest">
              <Check size={12} aria-hidden="true" /> {t('jobs.applied')}
            </StatusTag>
            <Link
              to="/applications"
              className="rounded text-[13px] font-semibold text-[var(--ui-info)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {t('jobs.trackIt')}
            </Link>
          </span>
        ) : (
          <Link to={`/apply/${role.requisition_id}`} className={buttonVariants({ size: 'sm' })}>
            {t('jobs.viewApply')}
          </Link>
        )}
      </div>
    </article>
  );
}

export default function OpenRoles() {
  const { t } = useTranslation();
  const roles = useQuery({
    queryKey: ['open-roles'],
    queryFn: listOpenRoles,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const items = roles.data ?? [];

  // Nothing open is a real answer, and a section that renders an empty box on
  // a page that also has practice roles just adds confusion. It disappears.
  if (!roles.isLoading && !roles.isError && items.length === 0) return null;

  return (
    <section aria-labelledby="open-roles-heading" className="mb-10">
      <div className="mb-4">
        <h2
          id="open-roles-heading"
          className="text-[20px] font-bold tracking-[-0.3px] text-foreground"
        >
          {t('jobs.openRolesTitle')}
        </h2>
        {/* The load-bearing sentence on this page. Below it are practice
            interviews; these are applications to real companies. */}
        <p className="mt-1 text-[14px] text-muted-foreground">{t('jobs.openRolesDesc')}</p>
      </div>

      {roles.isError ? (
        <p role="alert" className="text-[14px] text-[var(--ui-danger)]">
          {t('jobs.openRolesError')}
        </p>
      ) : null}

      <div className="flex flex-col gap-3">
        {roles.isLoading ? (
          <div className="rounded-card border border-border bg-card p-5 shadow-card">
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="mt-3 h-4 w-1/3" />
            <Skeleton className="mt-4 h-8 w-28 rounded-full" />
            <span className="sr-only">{t('jobs.openRolesLoading')}</span>
          </div>
        ) : null}
        {items.map((role) => (
          <RoleCard key={role.requisition_id} role={role} />
        ))}
      </div>

      {items.length > 0 ? (
        <p className="mt-3 flex items-center gap-1.5 text-[13px] text-muted-foreground">
          <ExternalLink size={13} aria-hidden="true" />
          {t('jobs.openRolesPracticeNote')}
        </p>
      ) : null}
    </section>
  );
}
