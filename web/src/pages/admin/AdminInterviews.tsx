// AdminInterviews — filterable, sortable, paginated admin interview list.
// Route: /admin/interviews (inside AdminRoute + AppShell)
//
// Sources merged:
//   A) Layout — design/screens/admin/AdminInterviews.tsx (page header, search pill,
//      SegTabs, GlassCard grid rows, Avatar + StatusTag treatment, ArrowRight chevron)
//   B) Behavior — current live AdminInterviews.tsx (listInterviews query, debounced
//      search, status/language/sort filters, exportInterviewsCsv, PER_PAGE=20
//      pagination, row→/admin/interviews/:id + keyboard nav, columns: Name/Email/
//      Role/Status/Language/Score/Date/Duration, all test-ids, empty/error/loading)
//
// No @/components/ui/* — replaced with native styled HTML throughout.

import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { motion, type Variants } from 'framer-motion';
import {
  Search,
  Download,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  ClipboardList,
  ArrowRight,
} from '@/design/components/icons';
import { listInterviews, exportInterviewsCsv } from '@/api/admin';
import type { InterviewListItem, InterviewFilters } from '@/api/admin';
import { toast } from '@/lib/toast';
import { formatDate, formatDuration, statusProps, languageLabel } from '@/lib/formatters';
import { GlassCard, SegTabs, StatusTag, Avatar } from '@/design/components/primitives';
import { cn } from '@/lib/utils';

// ── Constants ──────────────────────────────────────────────────────────────────

const PER_PAGE = 20;

function fmtScore(v: number | null): string {
  if (v === null) return '—';
  return v.toFixed(2);
}

function scoreColorInline(score: number): string {
  const pct = score * 10;
  if (pct >= 85) return '#27c93f';
  if (pct >= 70) return 'var(--accent)';
  if (pct >= 55) return '#ffb764';
  return '#e6714f';
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
  'linear-gradient(135deg,var(--accent),#a887dc)',
  'linear-gradient(135deg,#16c253,var(--accent))',
  'linear-gradient(135deg,#dd55e7,#a887dc)',
  'linear-gradient(135deg,#ffb764,#dd55e7)',
  'linear-gradient(135deg,#0fb7fa,#16c253)',
  'linear-gradient(135deg,#a887dc,var(--accent))',
];

function gradientFor(id: string): string {
  const seed = id.charCodeAt(0) + id.charCodeAt(id.length - 1);
  return GRADIENTS[Math.abs(seed) % GRADIENTS.length];
}

// ── Animation ──────────────────────────────────────────────────────────────────

const stagger: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
};

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] } },
};

// ── Status tabs (SegTabs) ──────────────────────────────────────────────────────

const STATUS_TABS = [
  { key: '__all__', label: 'All' },
  { key: 'in_progress', label: 'Live' },
  { key: 'completed', label: 'Completed' },
  { key: 'abandoned', label: 'Abandoned' },
  { key: 'failed', label: 'Failed' },
];

// ── Native <select> styled to the dark palette ─────────────────────────────────

interface DarkSelectProps {
  value: string;
  onChange: (v: string) => void;
  'aria-label': string;
  className?: string;
  children: ReactNode;
}

function DarkSelect({ value, onChange, 'aria-label': ariaLabel, className, children }: DarkSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={ariaLabel}
      className={cn(
        'rounded-[9999px] border border-white/[0.08] bg-[rgba(28,29,31,0.7)]',
        'px-3.5 py-2 text-[13px] text-[#b8babf] focus:outline-none',
        'focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        'appearance-none cursor-pointer',
        className,
      )}
    >
      {children}
    </select>
  );
}

// ── Filter bar ─────────────────────────────────────────────────────────────────

interface FilterBarProps {
  filters: InterviewFilters;
  onFiltersChange: (f: Partial<InterviewFilters>) => void;
  onExport: () => void;
  exporting: boolean;
}

function FilterBar({ filters, onFiltersChange, onExport, exporting }: FilterBarProps) {
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
    <div className="flex flex-wrap gap-3 items-end">
      {/* Search — design's dark pill input */}
      <div className="relative min-w-[180px] flex-1 flex items-center gap-2 rounded-[9999px] border border-white/[0.08] bg-[rgba(28,29,31,0.7)] px-3.5 py-2.5">
        <Search size={15} className="text-[#70757c] shrink-0" aria-hidden="true" />
        <input
          type="search"
          value={localQ}
          onChange={(e) => setLocalQ(e.target.value)}
          placeholder="Search candidate…"
          aria-label="Search by candidate name or email"
          data-testid="filter-search"
          className="min-w-0 flex-1 bg-transparent text-[13px] text-white placeholder:text-[#5a5f66] focus:outline-none"
        />
      </div>

      {/* Status — SegTabs (design pattern) */}
      <SegTabs
        tabs={STATUS_TABS}
        active={filters.status ?? '__all__'}
        onChange={(key) =>
          onFiltersChange({ status: key === '__all__' ? undefined : key, page: 1 })
        }
      />

      {/* Language filter */}
      <DarkSelect
        value={filters.language ?? '__all__'}
        onChange={(v) =>
          onFiltersChange({ language: v === '__all__' ? undefined : v, page: 1 })
        }
        aria-label="Filter by language"
        className="w-[130px]"
      >
        <option value="__all__">All languages</option>
        <option value="en">English</option>
        <option value="hi">Hindi</option>
        <option value="te">Telugu</option>
      </DarkSelect>

      {/* Sort */}
      <DarkSelect
        value={sortValue}
        onChange={handleSortChange}
        aria-label="Sort order"
        className="w-[160px]"
      >
        <option value="created_at_true">Newest first</option>
        <option value="created_at_false">Oldest first</option>
        <option value="composite_score_true">Score (high → low)</option>
        <option value="composite_score_false">Score (low → high)</option>
      </DarkSelect>

      {/* Export CSV */}
      <button
        type="button"
        onClick={onExport}
        disabled={exporting}
        className={cn(
          'inline-flex items-center gap-2 rounded-[9999px] border border-white/[0.12] bg-[rgba(var(--accent-rgb),0.14)] px-4 py-2',
          'text-[13px] font-semibold text-[#60a5fa] transition-colors',
          'hover:bg-[rgba(var(--accent-rgb),0.22)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          'disabled:cursor-not-allowed disabled:opacity-50',
        )}
        aria-label="Export interviews as CSV"
        data-testid="export-csv-btn"
      >
        <Download size={14} aria-hidden="true" />
        {exporting ? 'Exporting…' : 'Export CSV'}
      </button>
    </div>
  );
}

// ── Loading skeleton rows ──────────────────────────────────────────────────────

function LoadingRows() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          aria-hidden="true"
          className="grid grid-cols-[1.6fr_1.2fr_1.4fr_1.2fr_0.8fr_0.9fr_1fr_0.4fr] items-center gap-3 border-b border-white/[0.04] px-6 py-3.5 last:border-0"
        >
          {Array.from({ length: 8 }).map((__, j) => (
            <div key={j} className="h-4 rounded bg-white/[0.06] animate-pulse" />
          ))}
        </div>
      ))}
    </>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────────

function EmptyState({ hasFilters }: { hasFilters: boolean }) {
  return (
    <div data-testid="interviews-empty-state">
      <GlassCard className="flex flex-col items-center justify-center py-20 text-center gap-4">
        <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-white/[0.06]">
          <ClipboardList className="h-7 w-7 text-[#70757c]" aria-hidden="true" />
        </div>
        <div>
          <p className="text-[15px] font-semibold text-white">
            {hasFilters ? 'No interviews match your filters' : 'No interviews yet'}
          </p>
          <p className="mt-1.5 text-[13px] text-[#888b91]">
            {hasFilters
              ? 'Try adjusting your search or filters.'
              : 'Interview sessions will appear here once candidates start.'}
          </p>
        </div>
      </GlassCard>
    </div>
  );
}

// ── Interview row — design grid layout ────────────────────────────────────────

interface InterviewRowProps {
  item: InterviewListItem;
  onClick: (sessionId: string) => void;
}

function InterviewRow({ item, onClick }: InterviewRowProps) {
  const { label } = statusProps(item.status);
  const tone = statusTone(item.status);

  return (
    <div
      className="grid grid-cols-[1.6fr_1.2fr_1.4fr_1.2fr_0.8fr_0.9fr_1fr_0.4fr] items-center gap-3 border-b border-white/[0.04] px-6 py-3.5 last:border-0 cursor-pointer transition-colors hover:bg-white/[0.03] focus-visible:outline-none focus-visible:bg-white/[0.05]"
      role="row"
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
      <div className="flex items-center gap-3 min-w-0">
        <Avatar
          initials={initialsOf(item.candidate_name, item.candidate_email)}
          gradient={gradientFor(item.session_id)}
          size={32}
        />
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-medium text-white">
            {item.candidate_name ?? <span className="text-[#70757c]">—</span>}
          </div>
          <div className="font-mono text-[11px] text-[#70757c] truncate">
            {item.session_id.slice(0, 8)}…
          </div>
        </div>
      </div>

      {/* Email */}
      <div className="truncate text-[12.5px] text-[#888b91]">
        {item.candidate_email}
      </div>

      {/* Role */}
      <div className="truncate text-[13px] text-[#b8babf]">
        {item.job_title ?? <span className="text-[#70757c]">—</span>}
      </div>

      {/* Status — design StatusTag */}
      <div>
        <StatusTag tone={tone} dot={item.status === 'in_progress'}>
          {label}
        </StatusTag>
      </div>

      {/* Language */}
      <div className="text-[12.5px] text-[#888b91] whitespace-nowrap">
        {languageLabel(item.language)}
      </div>

      {/* Score */}
      <div
        className="text-[14px] font-semibold tabular-nums"
        style={{
          color:
            item.composite_score === null
              ? '#70757c'
              : scoreColorInline(item.composite_score),
        }}
      >
        {fmtScore(item.composite_score)}
      </div>

      {/* Date */}
      <div className="text-[12.5px] text-[#888b91] whitespace-nowrap">
        {formatDate(item.created_at)}
      </div>

      {/* Duration + arrow */}
      <div className="flex items-center justify-between gap-1">
        <span className="text-[12.5px] text-[#888b91] whitespace-nowrap">
          {formatDuration(item.duration_seconds)}
        </span>
        <ArrowRight size={15} className="text-[#70757c] flex-none" aria-hidden="true" />
      </div>
    </div>
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
    <div className="flex items-center justify-between pt-2 flex-wrap gap-2" aria-label="Pagination">
      <span className="text-[12.5px] text-[#888b91] tabular-nums">
        {total > 0 ? `${start}–${end} of ${total.toLocaleString()}` : '0 results'}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onPrev}
          disabled={page <= 1}
          aria-label="Previous page"
          className={cn(
            'inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.08]',
            'bg-transparent px-3.5 py-1.5 text-[13px] text-[#b8babf] transition-colors',
            'hover:text-white hover:bg-white/[0.06]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            'disabled:cursor-not-allowed disabled:opacity-40',
          )}
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          Previous
        </button>
        <span className="text-[12.5px] text-[#888b91] tabular-nums px-1">
          {page} / {totalPages}
        </span>
        <button
          type="button"
          onClick={onNext}
          disabled={page >= totalPages}
          aria-label="Next page"
          className={cn(
            'inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.08]',
            'bg-transparent px-3.5 py-1.5 text-[13px] text-[#b8babf] transition-colors',
            'hover:text-white hover:bg-white/[0.06]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            'disabled:cursor-not-allowed disabled:opacity-40',
          )}
        >
          Next
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

// ── AdminInterviews page ───────────────────────────────────────────────────────

export default function AdminInterviews() {
  const navigate = useNavigate();
  const [exporting, setExporting] = useState(false);

  const [filters, setFilters] = useState<InterviewFilters>({
    page: 1,
    per_page: PER_PAGE,
    sort_by: 'created_at',
    sort_desc: true,
  });

  const updateFilters = useCallback((patch: Partial<InterviewFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
  }, []);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['admin', 'interviews', filters],
    queryFn: () => listInterviews(filters),
    staleTime: 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  useEffect(() => {
    if (isError) {
      toast.error(
        error instanceof Error ? error.message : 'Failed to load interview list.',
      );
    }
  }, [isError, error]);

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      await exportInterviewsCsv(filters);
      toast.success('CSV download started.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed.');
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
    <motion.div initial="hidden" animate="visible" variants={stagger} className="space-y-6">
      {/* Page heading */}
      <motion.div variants={fadeUp}>
        <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">Interviews</h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          All candidate interview sessions. Click a row to view full detail.
        </p>
      </motion.div>

      {/* Filters */}
      <motion.div variants={fadeUp}>
        <FilterBar
          filters={filters}
          onFiltersChange={updateFilters}
          onExport={() => void handleExport()}
          exporting={exporting}
        />
      </motion.div>

      {/* Content area */}
      <motion.div variants={fadeUp}>
        {isError && !isLoading ? (
          <div role="alert">
            <GlassCard className="flex flex-col items-center justify-center py-16 gap-3 text-center">
              <AlertCircle className="h-8 w-8 text-[#e6714f]" aria-hidden="true" />
              <p className="text-[15px] font-semibold text-white">Failed to load interviews</p>
              <p className="text-[13px] text-[#888b91]">
                {error instanceof Error ? error.message : 'Unknown error'}
              </p>
            </GlassCard>
          </div>
        ) : !isLoading && items.length === 0 ? (
          <EmptyState hasFilters={hasFilters} />
        ) : (
          <GlassCard className="overflow-hidden p-0">
            {/* Card header row */}
            <div className="flex items-center justify-between flex-wrap gap-2 border-b border-white/[0.06] px-6 py-4">
              <div>
                <p className="text-[15px] font-semibold text-white">Sessions</p>
                <p className="text-[12.5px] text-[#888b91] mt-0.5">
                  {isLoading
                    ? 'Loading…'
                    : `${total.toLocaleString()} interview${total !== 1 ? 's' : ''}`}
                </p>
              </div>
              <span className="ml-auto text-[12.5px] text-[#70757c]">
                {!isLoading && `${items.length} shown`}
              </span>
            </div>

            {/* Column headers */}
            <div
              role="row"
              className="grid grid-cols-[1.6fr_1.2fr_1.4fr_1.2fr_0.8fr_0.9fr_1fr_0.4fr] gap-3 border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]"
            >
              <div>Candidate</div>
              <div>Email</div>
              <div>Role</div>
              <div>Status</div>
              <div>Language</div>
              <div>Score</div>
              <div>Date</div>
              <div />
            </div>

            {/* Rows */}
            <div role="rowgroup">
              {isLoading ? (
                <LoadingRows />
              ) : (
                items.map((item) => (
                  <InterviewRow
                    key={item.session_id}
                    item={item}
                    onClick={handleRowClick}
                  />
                ))
              )}
            </div>

            {/* Pagination */}
            {!isLoading && totalPages > 1 && (
              <div className="border-t border-white/[0.06] px-6 pb-5 pt-3">
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
          </GlassCard>
        )}
      </motion.div>
    </motion.div>
  );
}
