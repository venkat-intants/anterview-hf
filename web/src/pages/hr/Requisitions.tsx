// Requisitions — the openings this company is hiring for.
//
// The list exists because Phase 2 gave the product a first-class job. Before
// it, an applicant carried a free-text `target_job_title` and there was nothing
// to open: no per-job funnel, no per-job workflow, nothing to count. Everything
// on the workflow side hangs off a row on this page.
//
// The column that matters most is "awaiting you". A hiring process that runs
// itself still stops at every point where a person must decide (D-05), and this
// is the number that says how many people are standing at one of those stops.
// It leads the row for that reason — a queue nobody can see is a queue nobody
// works.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Briefcase,
  ChevronRight,
  Loader2,
  Plus,
  Users,
} from '@/design/components/icons';
import { GlassCard, StatCard, StatusTag, SegTabs } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { LIVE_POLL_MS } from '@/lib/polling';
import {
  listRequisitions,
  createRequisition,
  getBackfillReview,
  type Requisition,
  type RequisitionStatus,
} from '@/api/requisitions';

const TABS = [
  { key: 'open', label: 'Open' },
  { key: 'paused', label: 'Paused' },
  { key: 'closed', label: 'Closed' },
  { key: 'all', label: 'All' },
];

const LEVELS = ['entry', 'mid', 'senior'];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/**
 * The roll-up across every opening in view — E3.
 *
 * Computed from the rows already fetched rather than from another endpoint:
 * the list carries per-opening totals, and a second query would be a second
 * thing to keep in step with the first for no new information.
 *
 * "Awaiting you" leads and is the only figure coloured, because it is the only
 * one nothing in the system will clear on its own.
 */
function RollUp({ rows }: { rows: Requisition[] }) {
  if (rows.length === 0) return null;
  const waiting = rows.reduce((n, r) => n + r.awaiting_decision, 0);
  const candidates = rows.reduce((n, r) => n + r.total_enrolments, 0);
  const hired = rows.reduce((n, r) => n + r.hired, 0);
  const live = rows.filter((r) => r.public_apply_enabled && r.status === 'open').length;

  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Awaiting your decision" value={String(waiting)} feature={waiting > 0} />
      <StatCard label="Candidates in play" value={String(candidates)} />
      <StatCard label="Hired" value={String(hired)} />
      <StatCard label="Open to applications" value={String(live)} />
    </div>
  );
}

function RequisitionRow({ req }: { req: Requisition }) {
  return (
    <GlassCard hover className="p-0">
      <Link
        to={`/hr/requisitions/${req.id}`}
        className="flex items-center gap-4 p-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px] bg-white/[0.06]">
          <Briefcase className="h-[18px] w-[18px] text-[#d5d7da]" aria-hidden="true" />
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="truncate text-[15px] font-medium text-white">{req.title}</span>
            <StatusTag
              tone={
                req.status === 'open' ? 'forest' : req.status === 'paused' ? 'amber' : 'neutral'
              }
              dot={req.status === 'open'}
            >
              {req.status}
            </StatusTag>
            {req.from_backfill ? (
              <StatusTag tone="lavender">imported</StatusTag>
            ) : null}
            {req.public_apply_enabled && req.status === 'open' ? (
              <StatusTag tone="electric">accepting applications</StatusTag>
            ) : null}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-[#888b91]">
            <span>{req.level}</span>
            <span>
              {req.total_enrolments} candidate{req.total_enrolments === 1 ? '' : 's'}
            </span>
            {req.hired > 0 ? <span className="text-[#27c93f]">{req.hired} hired</span> : null}
            {req.target_hires ? <span>target {req.target_hires}</span> : null}
          </span>
        </span>

        {req.awaiting_decision > 0 ? (
          <span className="flex shrink-0 items-center gap-1.5 rounded-pill border border-[#ffb764]/35 bg-[#ffb764]/[0.08] px-3 py-1.5 text-[12px] font-medium text-[#ffb764]">
            <Users className="h-3.5 w-3.5" aria-hidden="true" />
            {req.awaiting_decision} awaiting you
          </span>
        ) : null}

        <ChevronRight className="h-4 w-4 shrink-0 text-[#5a5f66]" aria-hidden="true" />
      </Link>
    </GlassCard>
  );
}

function NewRequisitionForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState('');
  const [level, setLevel] = useState('mid');

  const mut = useMutation({
    mutationFn: () => createRequisition({ title: title.trim(), level }),
    onSuccess: (r) => {
      toast.success(`Opened “${r.title}”`);
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
      onDone();
    },
    // A 409 means an open requisition already uses that title, and the server
    // says which. Two recruiters opening the same role on one morning is
    // normal; the right answer is to point at the one that exists.
    onError: (e) => toast.error(errText(e, 'Could not create the opening')),
  });

  const field =
    'rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3.5 py-2.5 text-[14px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none';

  return (
    <GlassCard className="p-5">
      <h2 className="text-[15px] font-semibold text-white">New opening</h2>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <div className="min-w-[240px] flex-1">
          <label htmlFor="new-title" className="mb-1.5 block text-[12px] font-medium text-[#b8babf]">
            Job title
          </label>
          <input
            id="new-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Backend Engineer"
            className={`w-full ${field}`}
          />
        </div>
        <div>
          <label htmlFor="new-level" className="mb-1.5 block text-[12px] font-medium text-[#b8babf]">
            Level
          </label>
          <select
            id="new-level"
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className={field}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => mut.mutate()}
          disabled={mut.isPending || title.trim().length < 2}
          className="inline-flex items-center gap-1.5 rounded-[12px] bg-white px-4 py-2.5 text-[13px] font-medium text-black hover:opacity-90 disabled:opacity-40"
        >
          {mut.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          Create
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded-[12px] border border-white/[0.12] px-4 py-2.5 text-[13px] text-[#d5d7da] hover:text-white"
        >
          Cancel
        </button>
      </div>
    </GlassCard>
  );
}

export default function Requisitions(): JSX.Element {
  const [tab, setTab] = useState('open');
  const [creating, setCreating] = useState(false);

  const list = useQuery({
    queryKey: ['hr', 'requisitions', tab],
    queryFn: () =>
      listRequisitions(tab === 'all' ? {} : { status: tab as RequisitionStatus }),
    refetchInterval: LIVE_POLL_MS,
  });

  // Only to decide whether the review banner is worth showing. Cheap, cached,
  // and the alternative — a permanent link to a page that says "nothing to
  // review" — is the kind of dead nav that trains people to ignore banners.
  const review = useQuery({ queryKey: ['hr', 'requisitions', 'review'], queryFn: getBackfillReview });
  const pending =
    (review.data?.backfilled_requisitions.length ?? 0) +
    (review.data?.merge_candidates.length ?? 0);

  const rows = list.data ?? [];

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-8">
      <Reveal>
        <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-white">Openings</h1>
            <p className="mt-1 text-[13.5px] text-[#888b91]">
              Each opening carries its own hiring workflow and its own funnel.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setCreating((c) => !c)}
            className="inline-flex items-center gap-1.5 rounded-[12px] bg-white px-4 py-2.5 text-[13px] font-medium text-black hover:opacity-90"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            New opening
          </button>
        </header>
      </Reveal>

      {pending > 0 ? (
        <Link
          to="/hr/requisitions/review"
          className="mb-4 flex items-center gap-2.5 rounded-[14px] border border-[#ffb764]/30 bg-[#ffb764]/[0.07] px-4 py-3 text-[13px] text-[#d5d7da] hover:border-[#ffb764]/55"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-[#ffb764]" aria-hidden="true" />
          <span className="flex-1">
            {pending} thing{pending === 1 ? '' : 's'} to confirm from your imported data —
            openings that were grouped by job title, and people who may be duplicated.
          </span>
          <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
        </Link>
      ) : null}

      {creating ? (
        <div className="mb-4">
          <NewRequisitionForm onDone={() => setCreating(false)} />
        </div>
      ) : null}

      <div className="mb-4">
        <SegTabs tabs={TABS} active={tab} onChange={setTab} />
      </div>

      {list.isLoading ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-[#888b91]">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading openings…
        </div>
      ) : list.isError ? (
        <GlassCard className="p-6 text-[13.5px] text-[#e6714f]">
          {errText(list.error, 'Could not load openings')}
        </GlassCard>
      ) : rows.length === 0 ? (
        <GlassCard className="p-10 text-center">
          <Briefcase className="mx-auto h-8 w-8 text-[#5a5f66]" aria-hidden="true" />
          <div className="mt-3 text-[15px] font-medium text-white">
            {tab === 'open' ? 'No open roles' : `Nothing ${tab}`}
          </div>
          <p className="mx-auto mt-1.5 max-w-[44ch] text-[13px] text-[#888b91]">
            Create an opening to give a role its own workflow, funnel and decision queue.
          </p>
        </GlassCard>
      ) : (
        <>
          <RollUp rows={rows} />
          <div className="flex flex-col gap-3">
            {rows.map((r) => (
              <RequisitionRow key={r.id} req={r} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
