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
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getCareersBoard, type BoardQuery, type JobCard } from '@/api/careers';
import { ApiError } from '@/api/client';
import { Briefcase, MapPin, Search, X } from '@/design/components/icons';
import { cn } from '@/lib/utils';

const EMPLOYMENT_LABELS: Record<string, string> = {
  full_time: 'Full-time',
  part_time: 'Part-time',
  contract: 'Contract',
  internship: 'Internship',
  temporary: 'Temporary',
};

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#0a0a0b] px-4 py-10">
      <div className="mx-auto w-full max-w-[860px]">{children}</div>
    </div>
  );
}

/** "Hyderabad · Full-time · Engineering", skipping whatever is missing. */
function metaLine(job: JobCard): string {
  const employment = job.employment_type
    ? (EMPLOYMENT_LABELS[job.employment_type] ?? job.employment_type)
    : null;
  return [job.location, employment, job.department].filter(Boolean).join(' · ');
}

/** "4–8 years", "4+ years", "Up to 8 years", or nothing. */
function experienceLine(job: JobCard): string | null {
  const { experience_min_years: lo, experience_max_years: hi } = job;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null) return lo === hi ? `${lo} years` : `${lo}–${hi} years`;
  return lo != null ? `${lo}+ years` : `Up to ${hi} years`;
}

function salaryLine(job: JobCard): string | null {
  const { salary_min: lo, salary_max: hi } = job;
  if (lo == null && hi == null) return null;
  const currency = job.salary_currency ?? '';
  const money = (n: number) => `${currency} ${n.toLocaleString()}`.trim();
  if (lo != null && hi != null) return `${money(lo)} – ${money(hi)}`;
  return lo != null ? `From ${money(lo)}` : `Up to ${money(hi as number)}`;
}

/** "2 days ago" — recency is what a candidate reads, not a date. */
function postedLine(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return 'Posted today';
  if (days === 1) return 'Posted yesterday';
  if (days < 30) return `Posted ${days} days ago`;
  return `Posted ${new Date(iso).toLocaleDateString()}`;
}

function Card({ job }: { job: JobCard }) {
  const meta = metaLine(job);
  const experience = experienceLine(job);
  const salary = salaryLine(job);
  return (
    <Link
      to={`/apply/${job.requisition_id}`}
      className="block rounded-[16px] border border-white/[0.08] bg-[#0f0f10] p-5 transition-colors hover:border-white/[0.16] focus:outline-none focus-visible:border-[var(--accent)]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold text-white">{job.title}</h2>
          {meta ? <p className="mt-1 text-[13.5px] text-[#b8babf]">{meta}</p> : null}
        </div>
        {salary ? (
          <span className="shrink-0 rounded-[10px] border border-white/[0.1] bg-white/[0.04] px-2.5 py-1 text-[12.5px] font-medium text-white">
            {salary}
          </span>
        ) : null}
      </div>

      {job.skills.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {job.skills.map((skill) => (
            <span
              key={skill}
              className="rounded-full border border-white/[0.1] bg-white/[0.04] px-2.5 py-0.5 text-[12px] text-[#d5d7da]"
            >
              {skill}
            </span>
          ))}
        </div>
      ) : null}

      <p className="mt-3 text-[12.5px] text-[#70757c]">
        {job.level} level
        {experience ? ` · ${experience}` : ''} · {postedLine(job.posted_at)}
      </p>
    </Link>
  );
}

const SELECT_CLASS =
  'rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13px] text-white focus:border-[var(--accent)] focus:outline-none';

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
        <div className="rounded-[20px] border border-white/[0.08] bg-[#0f0f10] p-8 text-center">
          <Briefcase className="mx-auto h-8 w-8 text-[#5a5f66]" aria-hidden="true" />
          <h1 className="mt-4 text-[20px] font-semibold text-white">
            {missing ? 'No careers page here' : 'Could not load these roles'}
          </h1>
          <p className="mx-auto mt-2 max-w-[48ch] text-[13.5px] leading-relaxed text-[#888b91]">
            {missing
              ? 'Check the address with whoever shared it — this company does not have a careers page at this link.'
              : 'Something went wrong on our side. Refresh the page to try again.'}
          </p>
        </div>
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
    <Shell>
      <header className="mb-6">
        <div className="text-[12.5px] uppercase tracking-[1.2px] text-[#70757c]">
          {data?.company_name ?? ' '}
        </div>
        <h1 className="mt-1 text-[30px] font-semibold tracking-[-0.9px] text-white">
          Open roles
        </h1>
      </header>

      <div className="mb-5 flex flex-col gap-3">
        <label className="relative block">
          <span className="sr-only">Search roles</span>
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#5a5f66]"
            aria-hidden="true"
          />
          <input
            type="search"
            defaultValue={params.get('q') ?? ''}
            placeholder="Search by title or skill"
            onKeyDown={(e) => {
              if (e.key === 'Enter') setFilter('q', e.currentTarget.value.trim());
            }}
            onBlur={(e) => setFilter('q', e.currentTarget.value.trim())}
            className="w-full rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] py-2.5 pl-9 pr-3 text-[14px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>

        {/* Each dropdown appears only when this company has real values for
            it. An empty select reads as broken; an absent one reads as a
            board that simply does not sort by that. */}
        <div className="flex flex-wrap items-center gap-2">
          {filters && filters.departments.length > 0 ? (
            <select
              aria-label="Department"
              value={params.get('department') ?? ''}
              onChange={(e) => setFilter('department', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">All departments</option>
              {filters.departments.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          ) : null}

          {filters && filters.locations.length > 0 ? (
            <select
              aria-label="Location"
              value={params.get('location') ?? ''}
              onChange={(e) => setFilter('location', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">All locations</option>
              {filters.locations.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          ) : null}

          {filters && filters.employment_types.length > 0 ? (
            <select
              aria-label="Employment type"
              value={params.get('employment_type') ?? ''}
              onChange={(e) => setFilter('employment_type', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">Any type</option>
              {filters.employment_types.map((t) => (
                <option key={t} value={t}>
                  {EMPLOYMENT_LABELS[t] ?? t}
                </option>
              ))}
            </select>
          ) : null}

          {/* Salary is a band, not a free-text box: a candidate thinks in
              "at least X", and an open number field on a public page invites
              nonsense that returns nothing. */}
          <select
            aria-label="Minimum salary"
            value={params.get('min_salary') ?? ''}
            onChange={(e) => setFilter('min_salary', e.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">Any salary</option>
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
              aria-label="Sort by"
              value={params.get('sort') ?? 'newest'}
              onChange={(e) =>
                setFilter('sort', e.target.value === 'relevance' ? 'relevance' : '')
              }
              className={SELECT_CLASS}
            >
              <option value="newest">Newest first</option>
              <option value="relevance">Most relevant</option>
            </select>
          ) : null}

          {activeCount > 0 ? (
            <button
              type="button"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
              className="inline-flex items-center gap-1 rounded-[10px] border border-white/[0.1] px-2.5 py-2 text-[12.5px] text-[#b8babf] hover:text-white focus:outline-none focus-visible:border-[var(--accent)]"
            >
              <X size={13} aria-hidden="true" />
              Clear filters
            </button>
          ) : null}
        </div>
      </div>

      <p className="mb-3 text-[13px] text-[#888b91]" aria-live="polite">
        {board.isLoading
          ? 'Loading roles…'
          : `${data?.total ?? 0} open ${data?.total === 1 ? 'position' : 'positions'}`}
      </p>

      <div className="flex flex-col gap-3">
        {board.isLoading ? (
          <>
            <div className="h-[118px] animate-pulse rounded-[16px] border border-white/[0.08] bg-[#0f0f10]" />
            <div className="h-[118px] animate-pulse rounded-[16px] border border-white/[0.08] bg-[#0f0f10]" />
          </>
        ) : null}

        {!board.isLoading && data && data.items.length === 0 ? (
          <div className="rounded-[16px] border border-white/[0.08] bg-[#0f0f10] p-8 text-center">
            <MapPin className="mx-auto h-7 w-7 text-[#5a5f66]" aria-hidden="true" />
            <h2 className="mt-3 text-[16px] font-semibold text-white">
              {activeCount > 0 ? 'No roles match those filters' : 'No open roles right now'}
            </h2>
            <p className="mx-auto mt-2 max-w-[44ch] text-[13.5px] leading-relaxed text-[#888b91]">
              {activeCount > 0
                ? 'Try widening your search — clearing a filter usually brings more back.'
                : `${data.company_name} is not advertising anything at the moment. Check back later.`}
            </p>
          </div>
        ) : null}

        {data?.items.map((job) => (
          <Card key={job.requisition_id} job={job} />
        ))}
      </div>

      {data && data.total > data.per_page ? (
        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            disabled={data.page <= 1}
            onClick={() => goToPage(data.page - 1)}
            className={cn(
              'rounded-[10px] border border-white/[0.1] px-3 py-2 text-[13px] text-[#b8babf]',
              data.page <= 1 ? 'opacity-40' : 'hover:text-white',
            )}
          >
            Previous
          </button>
          <span className="text-[12.5px] text-[#70757c]">
            Page {data.page} of {Math.ceil(data.total / data.per_page)}
          </span>
          <button
            type="button"
            disabled={data.page >= Math.ceil(data.total / data.per_page)}
            onClick={() => goToPage(data.page + 1)}
            className={cn(
              'rounded-[10px] border border-white/[0.1] px-3 py-2 text-[13px] text-[#b8babf]',
              data.page >= Math.ceil(data.total / data.per_page)
                ? 'opacity-40'
                : 'hover:text-white',
            )}
          >
            Next
          </button>
        </div>
      ) : null}
    </Shell>
  );
}
