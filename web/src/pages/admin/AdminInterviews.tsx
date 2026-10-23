// AdminInterviews — filterable, sortable, paginated admin interview list.
// Route: /admin/interviews (inside AdminRoute + AppShell)
//
// Layout: PageHeader (Export CSV) → the shared Toolbar of filters → a "Sessions"
// card holding a horizontally scrollable table with a sticky header.
// Behavior: listInterviews query, debounced search, status/language/sort filters,
// exportInterviewsCsv, PER_PAGE=20 pagination, row → /admin/interviews/:id
// (click or Enter/Space), skeleton / empty / error states.

import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Search,
  Download,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  ArrowRight,
} from '@/design/components/icons';
import { listInterviews, exportInterviewsCsv } from '@/api/admin';
import type { InterviewListItem, InterviewFilters } from '@/api/admin';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { QueryErrorPanel } from '@/components/QueryState';
import { formatDate, formatDuration, formatNumber, statusProps, languageLabel, formatScore } from '@/lib/formatters';
import { SegTabs, StatusTag, Avatar } from '@/design/components/primitives';
import { EmptyState, PageHeader, SectionCard, Toolbar, Page } from '@/design/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// ── Constants ──────────────────────────────────────────────────────────────────

const PER_PAGE = 20;

const COLUMNS = ['Candidate', 'Email', 'Role', 'Status', 'Language', 'Score', 'Date', 'Duration'];

function scoreColorInline(score: number): string {
  const pct = score * 10;
  if (pct >= 85) return 'var(--ui-ok)';
  if (pct >= 70) return 'var(--ui-info)';
  if (pct >= 55) return 'var(--ui-warn)';
  return 'var(--ui-danger)';
}

function statusTone(status: string): 'forest' | 'electric' | 'amber' | 'ember' | 'neutral' {
  switch (status) {
    case 'completed':
      return 'forest';
    case 'in_progress':
      return 'electric';
    case 'abandoned':
      return 'amber';
    case 'failed':
      return 'ember';
    default:
      return 'neutral';
  }
}

function initialsOf(name: string | null, email: string): string {
  if (name) {
    return name
      .split(' ')
      .map((w) => w[0] ?? '')
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

const GRADIENTS = [
  'linear-gradient(135deg,var(--accent),var(--ui-lavender))',
  'linear-gradient(135deg,var(--ui-ok),var(--accent))',
  'linear-gradient(135deg,var(--ui-role-platform),var(--ui-lavender))',
  'linear-gradient(135deg,var(--ui-warn),var(--ui-role-platform))',
  'linear-gradient(135deg,var(--ui-info),var(--ui-ok))',
  'linear-gradient(135deg,var(--ui-lavender),var(--accent))',
];

function gradientFor(id: string): string {
  const seed = id.charCodeAt(0) + id.charCodeAt(id.length - 1);
  return GRADIENTS[Math.abs(seed) % GRADIENTS.length];
}

// ── Status tabs (SegTabs) ──────────────────────────────────────────────────────

const STATUS_TABS = [
  { key: '__all__', label: 'All' },
  { key: 'in_progress', label: 'Live' },
  { key: 'completed', label: 'Completed' },
  { key: 'abandoned', label: 'Abandoned' },
  { key: 'failed', label: 'Failed' },
];

// ── Native <select> in the pill style ──────────────────────────────────────────

interface PillSelectProps {
  value: string;
  onChange: (v: string) => void;
  'aria-label': string;
  className?: string;
  children: ReactNode;
}

function PillSelect({
  value,
  onChange,
  'aria-label': ariaLabel,
  className,
  children,
}: PillSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={ariaLabel}
      className={cn(
        'h-10 cursor-pointer rounded-full border border-input bg-card px-4 text-[13.5px] text-foreground',
        'transition-colors hover:border-[var(--ui-line-strong)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        className,
      )}
    >
      {children}
    </select>
  );
}

// ── Toolbar ────────────────────────────────────────────────────────────────────

interface FilterBarProps {
  filters: InterviewFilters;
  onFiltersChange: (f: Partial<InterviewFilters>) => void;
}

function FilterBar({ filters, onFiltersChange }: FilterBarProps) {
  const [localQ, setLocalQ] = useState(filters.q ?? '');

  // Debounce 400 ms
  useEffect(() => {
    const id = setTimeout(() => {
      const next = localQ.trim() || undefined;
      if (next !== filters.q) {
        onFiltersChange({ q: next, page: 1 });
      }
    }, 400);
    return () => clearTimeout(id);
  }, [localQ, filters.q, onFiltersChange]);

  const sortValue = `${filters.sort_by ?? 'created_at'}_${String(filters.sort_desc !== false)}`;

  function handleSortChange(v: string) {
    const i = v.lastIndexOf('_');
    const by = v.slice(0, i) as 'created_at' | 'composite_score';
    const descStr = v.slice(i + 1);
    onFiltersChange({ sort_by: by, sort_desc: descStr === 'true', page: 1 });
  }

  return (
    <Toolbar
      trailing={
        <PillSelect value={sortValue} onChange={handleSortChange} aria-label="Sort order">
          <option value="created_at_true">Newest first</option>
          <option value="created_at_false">Oldest first</option>
          <option value="composite_score_true">Score (high → low)</option>
          <option value="composite_score_false">Score (low → high)</option>
        </PillSelect>
      }
    >
      <div className="flex h-10 min-w-[200px] flex-1 items-center gap-2 rounded-full border border-input bg-card px-4 transition-colors focus-within:border-[var(--accent)] hover:border-[var(--ui-line-strong)]">
        <Search size={15} className="shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
        <input
          type="search"
          value={localQ}
          onChange={(e) => setLocalQ(e.target.value)}
          placeholder="Search candidate…"
          aria-label="Search by candidate name or email"
          data-testid="filter-search"
          className="min-w-0 flex-1 bg-transparent text-[13.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none"
        />
      </div>

      <div className="max-w-full overflow-x-auto">
        <SegTabs
          tabs={STATUS_TABS}
          active={filters.status ?? '__all__'}
          onChange={(key) =>
            onFiltersChange({ status: key === '__all__' ? undefined : key, page: 1 })
          }
        />
      </div>

      <PillSelect
        value={filters.language ?? '__all__'}
        onChange={(v) => onFiltersChange({ language: v === '__all__' ? undefined : v, page: 1 })}
        aria-label="Filter by language"
      >
        <option value="__all__">All languages</option>
        <option value="en">English</option>
        <option value="hi">Hindi</option>
        <option value="te">Telugu</option>
      </PillSelect>
    </Toolbar>
  );
}

// ── Loading skeleton rows ──────────────────────────────────────────────────────

function LoadingRows() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
        <tr key={i} aria-hidden="true" className="border-b border-border last:border-0">
          {COLUMNS.map((c) => (
            <td key={c} className="px-4 py-3.5">
              <Skeleton className="h-4 w-full min-w-[56px]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────────

function NoInterviews({ hasFilters }: { hasFilters: boolean }) {
  return (
    <div data-testid="interviews-empty-state">
      <SectionCard title="Sessions">
        <EmptyState
          size="page"
          icon={ClipboardList}
          title={hasFilters ? 'No interviews match your filters' : 'No interviews yet'}
          description={
            hasFilters
              ? 'Try adjusting your search or filters.'
              : 'Interview sessions will appear here once candidates start.'
          }
        />
      </SectionCard>
    </div>
  );
}

// ── Interview row ──────────────────────────────────────────────────────────────

interface InterviewRowProps {
  item: InterviewListItem;
  onClick: (sessionId: string) => void;
}

function InterviewRow({ item, onClick }: InterviewRowProps) {
  const { label } = statusProps(item.status);
  const tone = statusTone(item.status);

  return (
    <tr
      className="cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-[var(--ui-inset-soft)] focus-visible:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]"
      tabIndex={0}
      onClick={() => onClick(item.session_id)}
      data-testid={`interview-row-${item.session_id}`}
      aria-label={`Interview for ${item.candidate_name ?? item.candidate_email}`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick(item.session_id);
        }
      }}
    >
      {/* Name + avatar */}
      <td className="px-4 py-3">
        <div className="flex min-w-[170px] items-center gap-3">
          <Avatar
            initials={initialsOf(item.candidate_name, item.candidate_email)}
            gradient={gradientFor(item.session_id)}
            size={32}
          />
          <div className="min-w-0">
            <div className="truncate text-[14px] font-medium text-foreground">
              {item.candidate_name ?? <span className="text-muted-foreground">-</span>}
            </div>
            <div className="truncate font-mono text-[12px] text-muted-foreground">
              {item.session_id.slice(0, 8)}…
            </div>
          </div>
        </div>
      </td>

      <td className="max-w-[220px] truncate px-4 py-3 text-[13px] text-muted-foreground">
        {item.candidate_email}
      </td>

      <td className="max-w-[200px] truncate px-4 py-3 text-[13.5px] text-[var(--ui-soft)]">
        {item.job_title ?? <span className="text-muted-foreground">-</span>}
      </td>

      <td className="px-4 py-3">
        <StatusTag tone={tone} dot={item.status === 'in_progress'}>
          {label}
        </StatusTag>
      </td>

      <td className="whitespace-nowrap px-4 py-3 text-[13px] text-muted-foreground">
        {languageLabel(item.language)}
      </td>

      <td
        className="px-4 py-3 text-[14px] font-semibold tabular-nums"
        style={{
          color:
            item.composite_score === null
              ? 'var(--muted-foreground)'
              : scoreColorInline(item.composite_score),
        }}
      >
        {formatScore('interview', item.composite_score)}
      </td>

      <td className="whitespace-nowrap px-4 py-3 text-[13px] text-muted-foreground">
        {formatDate(item.created_at)}
      </td>

      <td className="px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <span className="whitespace-nowrap text-[13px] text-muted-foreground">
            {formatDuration(item.duration_seconds)}
          </span>
          <ArrowRight size={15} className="flex-none text-muted-foreground" aria-hidden="true" />
        </div>
      </td>
    </tr>
  );
}

// ── Pagination ─────────────────────────────────────────────────────────────────

interface PaginationBarProps {
  page: number;
  totalPages: number;
  total: number;
  perPage: number;
  onPrev: () => void;
  onNext: () => void;
}

function PaginationBar({ page, totalPages, total, perPage, onPrev, onNext }: PaginationBarProps) {
  const start = (page - 1) * perPage + 1;
  const end = Math.min(page * perPage, total);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2" aria-label="Pagination">
      <span className="text-[13px] text-muted-foreground tabular-nums">
        {total > 0 ? `${start}-${end} of ${formatNumber(total)}` : '0 results'}
      </span>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onPrev}
          disabled={page <= 1}
          aria-label="Previous page"
          className="gap-1.5"
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          Previous
        </Button>
        <span className="px-1 text-[13px] text-muted-foreground tabular-nums">
          {page} / {totalPages}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onNext}
          disabled={page >= totalPages}
          aria-label="Next page"
          className="gap-1.5"
        >
          Next
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

// ── Filters in the URL ─────────────────────────────────────────────────────────
//
// The filters, search and page live in the query string (audit AD-F26), so
// opening a session and coming back restores the list exactly as it was, and a
// filtered view can be shared as a link. Defaults are left out of the URL.

const TEXT_KEYS = ['q', 'status', 'language', 'date_from', 'date_to'] as const;
const NUMBER_KEYS = ['min_score', 'max_score'] as const;

function filtersFromParams(params: URLSearchParams): InterviewFilters {
  const out: InterviewFilters = {
    page: 1,
    per_page: PER_PAGE,
    sort_by: 'created_at',
    sort_desc: true,
  };
  const page = Number(params.get('page'));
  if (Number.isInteger(page) && page > 1) out.page = page;
  for (const key of TEXT_KEYS) {
    const v = params.get(key);
    if (v) out[key] = v;
  }
  for (const key of NUMBER_KEYS) {
    const raw = params.get(key);
    const v = raw === null || raw === '' ? NaN : Number(raw);
    if (Number.isFinite(v)) out[key] = v;
  }
  if (params.get('sort_by') === 'composite_score') out.sort_by = 'composite_score';
  if (params.get('sort_desc') === 'false') out.sort_desc = false;
  return out;
}

function paramsFromFilters(f: InterviewFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.page && f.page > 1) p.set('page', String(f.page));
  for (const key of TEXT_KEYS) {
    const v = f[key];
    if (v) p.set(key, v);
  }
  for (const key of NUMBER_KEYS) {
    const v = f[key];
    if (v !== undefined) p.set(key, String(v));
  }
  if (f.sort_by && f.sort_by !== 'created_at') p.set('sort_by', f.sort_by);
  if (f.sort_desc === false) p.set('sort_desc', 'false');
  return p;
}

// ── AdminInterviews page ───────────────────────────────────────────────────────

export default function AdminInterviews() {
  const navigate = useNavigate();
  const [exporting, setExporting] = useState(false);

  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(searchParams), [searchParams]);

  const updateFilters = useCallback(
    (patch: Partial<InterviewFilters>) => {
      // Replace, not push: each keystroke or filter change is not a history
      // entry, so Back from a session lands on the list in one step.
      setSearchParams((prev) => paramsFromFilters({ ...filtersFromParams(prev), ...patch }), {
        replace: true,
      });
    },
    [setSearchParams],
  );

  const interviewsQuery = useQuery({
    queryKey: ['admin', 'interviews', filters],
    queryFn: () => listInterviews(filters),
    staleTime: 60 * 1000,
    retry: false,
    throwOnError: false,
  });
  const { data, isLoading, isError, error } = interviewsQuery;

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      await exportInterviewsCsv(filters);
      toast.success('CSV download started');
    } catch (err) {
      toast.error(errorMessage(err, 'Could not export the interviews.'));
    } finally {
      setExporting(false);
    }
  }, [filters]);

  const handleRowClick = useCallback(
    (sessionId: string) => {
      void navigate(`/admin/interviews/${sessionId}`);
    },
    [navigate],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const page = filters.page ?? 1;
  const perPage = data?.per_page ?? PER_PAGE;
  const totalPages = Math.max(1, Math.ceil(total / perPage));

  const hasFilters = Boolean(
    filters.q ||
    filters.status ||
    filters.language ||
    filters.min_score !== undefined ||
    filters.max_score !== undefined ||
    filters.date_from ||
    filters.date_to,
  );

  return (
    <Page width="default">
      <PageHeader
        className="mb-0"
        eyebrow="Platform"
        title="Interviews"
        description="All candidate interview sessions. Click a row to view full detail."
        actions={
          <Button
            type="button"
            variant="outline"
            onClick={() => void handleExport()}
            disabled={exporting}
            aria-label="Export interviews as CSV"
            data-testid="export-csv-btn"
            className="gap-2"
          >
            <Download size={14} aria-hidden="true" />
            {exporting ? 'Exporting…' : 'Export CSV'}
          </Button>
        }
      />

      <FilterBar filters={filters} onFiltersChange={updateFilters} />

      {isError && !isLoading ? (
        <SectionCard title="Sessions">
          <QueryErrorPanel
            title="Could not load the interviews."
            error={error}
            onRetry={() => void interviewsQuery.refetch()}
            retrying={interviewsQuery.isFetching}
          />
        </SectionCard>
      ) : !isLoading && items.length === 0 ? (
        <NoInterviews hasFilters={hasFilters} />
      ) : (
        <SectionCard
          title="Sessions"
          description={
            isLoading
              ? 'Loading…'
              : `${formatNumber(total)} interview${total !== 1 ? 's' : ''} · ${items.length} shown`
          }
        >
          <div
            className="max-h-[70vh] overflow-auto rounded-[14px] border border-border"
            aria-busy={isLoading}
          >
            <table className="w-full min-w-[960px] border-collapse text-left">
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <th
                      key={c}
                      scope="col"
                      className="sticky top-0 z-10 border-b border-border bg-card px-4 py-3 text-[12px] font-semibold uppercase tracking-[0.5px] text-muted-foreground"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <LoadingRows />
                ) : (
                  items.map((item) => (
                    <InterviewRow key={item.session_id} item={item} onClick={handleRowClick} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {!isLoading && totalPages > 1 && (
            <div className="mt-4">
              <PaginationBar
                page={page}
                totalPages={totalPages}
                total={total}
                perPage={perPage}
                onPrev={() => updateFilters({ page: Math.max(1, page - 1) })}
                onNext={() => updateFilters({ page: Math.min(totalPages, page + 1) })}
              />
            </div>
          )}
        </SectionCard>
      )}
    </Page>
  );
}
