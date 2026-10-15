// Careers — one company's open roles. NO LOGIN.
//
// The front door. Until this page existed, a role was reachable only by a UUID
// somebody sent you; now a stranger can arrive, filter, and pick something
// worth reading. Clicking a card goes to the apply page, which is already the
// detail view.
//
// Three things shape the design.
//
// A CARD IS A GLANCE. No job description — the spec is right that a board is
// for deciding whether to open a role, not for reading it. Title, where, what
// kind, a few skills, when it went up.
//
// FILTERS DEGRADE TO NOTHING. Most openings predate the posting fields, so a
// company may have no departments and no locations recorded at all. Each
// dropdown renders only when the server reports real values for it — an empty
// "Department" select reads as broken, an absent one reads as a board that
// does not sort by department.
//
// THE URL IS THE STATE. Filters live in the query string, so a filtered board
// can be shared, bookmarked and reloaded. It also means the back button walks
// filter changes, which is what people expect from a search page.

import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getCareersBoard, type BoardQuery, type JobCard } from '@/api/careers';
import { ApiError } from '@/api/client';
import { Briefcase, MapPin, Search, X } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { PublicFrame, PublicNotice } from '@/components/PublicFrame';
import { EmptyState } from '@/design/components/page';

// Employment types are a closed server vocabulary, so they are looked up by
// key. An unknown value falls back to the raw string rather than rendering a
// missing-key placeholder at a candidate.
function employmentLabel(t: TFunction, value: string): string {
  const key = `careers.employment.${value}`;
  const label = t(key);
  return label === key ? value : label;
}

function Shell({ children, company }: { children: React.ReactNode; company?: string }) {
  return (
    <PublicFrame company={company} width="wide">
      {children}
    </PublicFrame>
  );
}

/** "Hyderabad · Full-time · Engineering", skipping whatever is missing. */
function metaLine(t: TFunction, job: JobCard): string {
  const employment = job.employment_type
    ? employmentLabel(t, job.employment_type)
    : null;
  return [job.location, employment, job.department].filter(Boolean).join(' · ');
}

/** "4–8 years", "4+ years", "Up to 8 years", or nothing. */
function experienceLine(t: TFunction, job: JobCard): string | null {
  const { experience_min_years: lo, experience_max_years: hi } = job;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null) {
    return lo === hi
      ? t('careers.yearsExact', { years: lo })
      : t('careers.yearsRange', { lo, hi });
  }
  return lo != null
    ? t('careers.yearsMin', { lo })
    : t('careers.yearsMax', { hi });
}

function salaryLine(t: TFunction, job: JobCard): string | null {
  const { salary_min: lo, salary_max: hi } = job;
  if (lo == null && hi == null) return null;
  const currency = job.salary_currency ?? '';
  const money = (n: number) => `${currency} ${n.toLocaleString()}`.trim();
  if (lo != null && hi != null) {
    return t('careers.salaryRange', { lo: money(lo), hi: money(hi) });
  }
  return lo != null
    ? t('careers.salaryFrom', { value: money(lo) })
    : t('careers.salaryUpTo', { value: money(hi as number) });
}

/** "2 days ago" — recency is what a candidate reads, not a date. */
function postedLine(t: TFunction, iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return t('careers.postedToday');
  if (days === 1) return t('careers.postedYesterday');
  if (days < 30) return t('careers.postedDaysAgo', { count: days });
  return t('careers.postedOn', { date: new Date(iso).toLocaleDateString() });
}

function Card({ job }: { job: JobCard }) {
  const { t } = useTranslation();
  const meta = metaLine(t, job);
  const experience = experienceLine(t, job);
  const salary = salaryLine(t, job);
  return (
    <Link
      // Tagged at source (PH3-B1). An application that arrives from the
      // company's own board is attributable without the recruiter having to
      // build a tracked link by hand, which is the attribution they would
      // otherwise never get — and the one they most often want.
      to={`/apply/${job.requisition_id}?src=careers_site`}
      className="block rounded-card border border-border bg-card p-5 shadow-card transition-shadow hover:shadow-elevated focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[17px] font-bold text-foreground">{job.title}</h2>
          {meta ? <p className="mt-1 text-[13.5px] text-[var(--ui-soft)]">{meta}</p> : null}
        </div>
        {salary ? (
          <span className="shrink-0 rounded-full border border-border bg-[var(--ui-inset)] px-3 py-1 text-[12.5px] font-medium text-foreground">
            {salary}
          </span>
        ) : null}
      </div>

      {job.skills.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {job.skills.map((skill) => (
            <span
              key={skill}
              className="rounded-full border border-border bg-[var(--ui-inset)] px-2.5 py-0.5 text-[12px] text-[var(--ui-soft)]"
            >
              {skill}
            </span>
          ))}
        </div>
      ) : null}

      <p className="mt-3 text-[12.5px] text-[var(--ui-soft)]">
        {t('careers.levelLine', { level: job.level })}
        {experience ? ` · ${experience}` : ''} · {postedLine(t, job.posted_at)}
      </p>
    </Link>
  );
}

const SELECT_CLASS =
  'h-11 max-w-full rounded-[12px] border border-input bg-card px-3 text-[14px] text-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20';

/**
 * Salary rungs for the filter, in rupees.
 *
 * Fixed steps rather than a slider or a free number: the board is public and
 * unauthenticated, the underlying column is an annual figure, and a candidate
 * choosing "at least 12L" is expressing a band rather than a precise number.
 */
const SALARY_STEPS = [
  { value: 300000, label: '₹3L+' },
  { value: 600000, label: '₹6L+' },
  { value: 1000000, label: '₹10L+' },
  { value: 1500000, label: '₹15L+' },
  { value: 2500000, label: '₹25L+' },
  { value: 4000000, label: '₹40L+' },
];

export default function Careers() {
  const { t } = useTranslation();
  const { companySlug = '' } = useParams<{ companySlug: string }>();
  const [params, setParams] = useSearchParams();

  const query: BoardQuery = {
    q: params.get('q') ?? undefined,
    department: params.get('department') ?? undefined,
    location: params.get('location') ?? undefined,
    employment_type: params.get('employment_type') ?? undefined,
    // Number('') is 0, which would filter on "pays at least nothing" and read
    // as an active filter. Only send it when a real figure was chosen.
    min_salary: params.get('min_salary') ? Number(params.get('min_salary')) : undefined,
    sort: params.get('sort') === 'relevance' ? 'relevance' : undefined,
    page: Number(params.get('page') ?? '1') || 1,
  };

  const board = useQuery({
    queryKey: ['careers', companySlug, params.toString()],
    queryFn: () => getCareersBoard(companySlug, query),
    retry: false,
    throwOnError: false,
    // Board results are stable for a browsing session; refetching on every
    // focus change would re-order nothing and cost a request each time.
    staleTime: 60 * 1000,
  });

  function update(key: string, value: string): void {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    // A filter change that leaves you on page 4 of a one-page result shows an
    // empty board with matches sitting behind it, so narrowing returns to page
    // one. Paging itself must obviously not reset the page — deleting it
    // unconditionally here made Next a no-op.
    if (key !== 'page') next.delete('page');
    setParams(next, { replace: true });
  }

  const setFilter = update;
  const goToPage = (page: number) => update('page', String(page));

  useEffect(() => {
    if (board.data) document.title = `Careers · ${board.data.company_name}`;
  }, [board.data]);

  if (board.isError) {
    const missing = board.error instanceof ApiError && board.error.status === 404;
    return (
      <Shell>
        <PublicNotice
          icon={<Briefcase size={22} aria-hidden="true" />}
          title={missing ? t('careers.notFoundTitle') : t('careers.errorTitle')}
          description={missing ? t('careers.notFoundDesc') : t('careers.errorDesc')}
          action={
            missing ? undefined : (
              <Button type="button" variant="outline" onClick={() => void board.refetch()}>
                {t('careers.retry')}
              </Button>
            )
          }
        />
      </Shell>
    );
  }

  const data = board.data;
  const filters = data?.filters;
  const activeCount = [
    'q',
    'department',
    'location',
    'employment_type',
    'min_salary',
  ].filter((k) => params.get(k)).length;

  return (
    <Shell company={data?.company_name}>
      <header className="mb-6">
        <h1 className="text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground sm:text-[32px]">
          {t('careers.openRoles')}
        </h1>
      </header>

      <div className="mb-5 flex flex-col gap-3">
        <label className="relative block">
          <span className="sr-only">{t('careers.searchLabel')}</span>
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--ui-soft)]"
            aria-hidden="true"
          />
          <input
            type="search"
            defaultValue={params.get('q') ?? ''}
            placeholder={t('careers.searchPlaceholder')}
            onKeyDown={(e) => {
              if (e.key === 'Enter') setFilter('q', e.currentTarget.value.trim());
            }}
            onBlur={(e) => setFilter('q', e.currentTarget.value.trim())}
            className="h-11 w-full rounded-full border border-input bg-card pl-10 pr-4 text-base text-foreground placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20 md:text-sm"
          />
        </label>

        {/* Each dropdown appears only when this company has real values for
            it. An empty select reads as broken; an absent one reads as a
            board that simply does not sort by that. */}
        <div className="flex flex-wrap items-center gap-2">
          {filters && filters.departments.length > 0 ? (
            <select
              aria-label={t('careers.departmentAria')}
              value={params.get('department') ?? ''}
              onChange={(e) => setFilter('department', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">{t('careers.allDepartments')}</option>
              {filters.departments.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          ) : null}

          {filters && filters.locations.length > 0 ? (
            <select
              aria-label={t('careers.locationAria')}
              value={params.get('location') ?? ''}
              onChange={(e) => setFilter('location', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">{t('careers.allLocations')}</option>
              {filters.locations.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          ) : null}

          {filters && filters.employment_types.length > 0 ? (
            <select
              aria-label={t('careers.employmentAria')}
              value={params.get('employment_type') ?? ''}
              onChange={(e) => setFilter('employment_type', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">{t('careers.anyType')}</option>
              {filters.employment_types.map((type) => (
                <option key={type} value={type}>
                  {employmentLabel(t, type)}
                </option>
              ))}
            </select>
          ) : null}

          {/* Salary is a band, not a free-text box: a candidate thinks in
              "at least X", and an open number field on a public page invites
              nonsense that returns nothing. */}
          <select
            aria-label={t('careers.salaryAria')}
            value={params.get('min_salary') ?? ''}
            onChange={(e) => setFilter('min_salary', e.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">{t('careers.anySalary')}</option>
            {SALARY_STEPS.map((s) => (
              <option key={s.value} value={String(s.value)}>
                {s.label}
              </option>
            ))}
          </select>

          {/* Relevance is offered only alongside a search term, because
              without one it has nothing to rank and would silently behave as
              newest while claiming otherwise. */}
          {params.get('q') ? (
            <select
              aria-label={t('careers.sortAria')}
              value={params.get('sort') ?? 'newest'}
              onChange={(e) =>
                setFilter('sort', e.target.value === 'relevance' ? 'relevance' : '')
              }
              className={SELECT_CLASS}
            >
              <option value="newest">{t('careers.newestFirst')}</option>
              <option value="relevance">{t('careers.mostRelevant')}</option>
            </select>
          ) : null}

          {activeCount > 0 ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
              className="h-11"
            >
              <X size={14} aria-hidden="true" />
              {t('careers.clearFilters')}
            </Button>
          ) : null}
        </div>
      </div>

      <p className="mb-3 text-[13.5px] text-muted-foreground" aria-live="polite">
        {board.isLoading
          ? t('careers.loadingRoles')
          : t('careers.positions', { count: data?.total ?? 0 })}
      </p>

      <div className="flex flex-col gap-3">
        {board.isLoading ? (
          <>
            <div className="h-[118px] animate-pulse rounded-card border border-border bg-[var(--ui-inset-soft)]" />
            <div className="h-[118px] animate-pulse rounded-card border border-border bg-[var(--ui-inset-soft)]" />
          </>
        ) : null}

        {!board.isLoading && data && data.items.length === 0 ? (
          <EmptyState
            icon={MapPin}
            title={activeCount > 0 ? t('careers.noMatchTitle') : t('careers.noRolesTitle')}
            description={
              activeCount > 0
                ? t('careers.noMatchDesc')
                : t('careers.noRolesDesc', { company: data.company_name })
            }
            action={
              activeCount > 0 ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setParams(new URLSearchParams(), { replace: true })}
                >
                  {t('careers.clearFilters')}
                </Button>
              ) : undefined
            }
          />
        ) : null}

        {data?.items.map((job) => (
          <Card key={job.requisition_id} job={job} />
        ))}
      </div>

      {data && data.total > data.per_page ? (
        <div className="mt-6 flex items-center justify-between">
          <Button
            type="button"
            variant="outline"
            className="h-11"
            disabled={data.page <= 1}
            onClick={() => goToPage(data.page - 1)}
          >
            {t('careers.previous')}
          </Button>
          <span className="text-[13px] text-muted-foreground">
            {t('careers.pageOf', {
              page: data.page,
              total: Math.ceil(data.total / data.per_page),
            })}
          </span>
          <Button
            type="button"
            variant="outline"
            className="h-11"
            disabled={data.page >= Math.ceil(data.total / data.per_page)}
            onClick={() => goToPage(data.page + 1)}
          >
            {t('careers.next')}
          </Button>
        </div>
      ) : null}
    </Shell>
  );
}
