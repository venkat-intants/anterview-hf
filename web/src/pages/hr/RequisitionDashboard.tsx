// RequisitionDashboard — one opening, end to end. Group E, E1.
//
// The Openings list answers "which of these needs me?". This answers "what is
// actually happening inside this one?", which is a different question and
// needs a different shape.
//
// Three things here that the list deliberately cannot show:
//
//   WHERE PEOPLE ARE, per round rather than per status. "Shortlisted" is the
//   same word whether someone is waiting for round one or sitting between
//   rounds three and four, and those are different problems with different
//   fixes.
//
//   WHERE THEY STOP. A per-round pass rate is the only view that tells a hard
//   round from a broken one. A round nobody clears is usually the second, and
//   usually the threshold rather than the candidates.
//
//   WHO IS WAITING ON A PERSON. Leading the page, because under D-05 nothing
//   advances those candidates without a human and a queue nobody opens is the
//   one backlog that cannot clear itself.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  Copy,
  ExternalLink,
  Globe,
  Loader2,
  Users,
} from '@/design/components/icons';
import { GlassCard, StatCard, StatusTag, ToggleSwitch } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import {
  getRequisitionDashboard,
  setRequisitionStatus,
  updateRequisition,
  UnresolvedCandidatesError,
  type RequisitionStatus,
  type RoundProgress,
} from '@/api/requisitions';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/* ── Open / pause / close, with the AC-12 prompt ────────────────────────── */

/**
 * Status control for one opening.
 *
 * Closing is the only transition that can strand anybody, so it is the only
 * one that asks. The server refuses a close while candidates are mid-process
 * (409) and returns the count; that refusal is not an error to report but a
 * question to put to the manager, so it renders as a panel offering the
 * decision queue first and closing anyway second.
 *
 * Under D-05 neither answer rejects anyone: candidates in a closed opening
 * stay exactly where they are and remain in the decision queue. The panel says
 * so, because "close anyway" otherwise reads as "discard these people".
 */
function StatusControl({
  requisitionId,
  status,
  title,
}: {
  requisitionId: string;
  status: string;
  title: string;
}) {
  const qc = useQueryClient();
  const [pendingClose, setPendingClose] = useState<number | null>(null);

  const mut = useMutation({
    mutationFn: (v: { next: RequisitionStatus; ack?: boolean }) =>
      setRequisitionStatus(requisitionId, v.next, { acknowledgeUnresolved: v.ack }),
    onSuccess: (_r, v) => {
      setPendingClose(null);
      toast.success(
        v.next === 'closed'
          ? `Closed “${title}”`
          : v.next === 'paused'
            ? `Paused “${title}”`
            : `Reopened “${title}”`,
      );
      void qc.invalidateQueries({ queryKey: ['hr', 'requisition', requisitionId] });
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
    },
    onError: (e) => {
      if (e instanceof UnresolvedCandidatesError) {
        setPendingClose(e.unresolved);
        return;
      }
      toast.error(errText(e, 'Could not change the status'));
    },
  });

  const btn =
    'rounded-[10px] border border-white/[0.12] px-3.5 py-2 text-[12.5px] text-[#d5d7da] hover:border-white/25 hover:text-white disabled:opacity-40';

  return (
    <>
      {status === 'open' ? (
        <>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'paused' })}
          >
            Pause
          </button>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'closed' })}
          >
            Close
          </button>
        </>
      ) : null}
      {status === 'paused' ? (
        <>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'open' })}
          >
            Reopen
          </button>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'closed' })}
          >
            Close
          </button>
        </>
      ) : null}
      {status === 'closed' ? (
        <button
          type="button"
          className={btn}
          disabled={mut.isPending}
          onClick={() => mut.mutate({ next: 'open' })}
        >
          Reopen
        </button>
      ) : null}

      {pendingClose !== null ? (
        <div
          role="alertdialog"
          aria-labelledby="close-unresolved-title"
          className="mt-3 w-full rounded-[14px] border border-[#f0b429]/30 bg-[rgba(240,180,41,0.06)] p-4"
        >
          <h3
            id="close-unresolved-title"
            className="flex items-center gap-1.5 text-[13.5px] font-semibold text-white"
          >
            <AlertTriangle className="h-4 w-4 text-[#f0b429]" aria-hidden="true" />
            {pendingClose} candidate{pendingClose === 1 ? '' : 's'} still undecided
          </h3>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-[#b8babf]">
            Closing does not reject them. They stay in the decision queue exactly as
            they are — nothing here ends a candidacy without you. Decide on them now,
            or close the opening and come back to them.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              to={`/hr/requisitions/${requisitionId}/decisions`}
              className="inline-flex items-center gap-1.5 rounded-[10px] bg-white px-3.5 py-2 text-[12.5px] font-medium text-black hover:opacity-90"
            >
              <Users className="h-3.5 w-3.5" aria-hidden="true" />
              Review {pendingClose}
            </Link>
            <button
              type="button"
              className={btn}
              disabled={mut.isPending}
              onClick={() => mut.mutate({ next: 'closed', ack: true })}
            >
              {mut.isPending ? (
                <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : null}
              Close anyway
            </button>
            <button type="button" className={btn} onClick={() => setPendingClose(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

/* ── Per-round funnel ───────────────────────────────────────────────────── */

function RoundBar({ round, widest }: { round: RoundProgress; widest: number }) {
  const pct = widest > 0 ? Math.max(2, Math.round((round.attempted / widest) * 100)) : 0;
  // A pass rate this low usually means the threshold, not the candidates —
  // flagged rather than asserted, because sometimes the round is simply hard.
  const suspicious = round.pass_rate !== null && round.attempted >= 5 && round.pass_rate < 20;

  return (
    <li className="py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-[13px] text-white">
          {round.position + 1}. {round.title}
        </span>
        <span className="shrink-0 text-[11.5px] text-[#888b91]">
          {round.at_this_round > 0 ? `${round.at_this_round} here now · ` : ''}
          {round.attempted} sat
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="h-2 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
          <span
            className="block h-full rounded-full bg-[var(--accent)]/70"
            style={{ width: `${pct}%` }}
          />
        </span>
        <span
          className={cn(
            'w-[86px] shrink-0 text-right text-[11.5px] tabular-nums',
            round.pass_rate === null
              ? 'text-[#5a5f66]'
              : suspicious
                ? 'text-[#ffb764]'
                : 'text-[#27c93f]',
          )}
        >
          {round.pass_rate === null ? 'not sat yet' : `${round.pass_rate}% pass`}
        </span>
      </div>
      {suspicious ? (
        <p className="mt-1 text-[11.5px] text-[#ffb764]">
          Almost nobody clears this. Worth checking the threshold before the questions.
        </p>
      ) : null}
    </li>
  );
}

/* ── Public apply ───────────────────────────────────────────────────────── */

function PublicApplyCard({
  requisitionId,
  enabled,
  status,
}: {
  requisitionId: string;
  enabled: boolean;
  status: string;
}) {
  const qc = useQueryClient();
  const link = `${window.location.origin}/apply/${requisitionId}`;

  const mut = useMutation({
    mutationFn: (next: boolean) =>
      updateRequisition(requisitionId, { public_apply_enabled: next }),
    onSuccess: (_r, next) => {
      toast.success(next ? 'Applications are open' : 'Applications closed');
      void qc.invalidateQueries({ queryKey: ['hr', 'requisition-dashboard', requisitionId] });
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
    },
    onError: (e) => toast.error(errText(e, 'Could not change that')),
  });

  return (
    <GlassCard className="p-5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-[#70757c]" aria-hidden="true" />
            <span className="text-[14px] font-medium text-white">Accept applications</span>
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-[#888b91]">
            Publishes a page anyone with the link can apply through. Off by default — a
            link that leaks should not open a channel you did not choose to open.
          </p>
        </div>
        <ToggleSwitch
          checked={enabled}
          onChange={(next) => mut.mutate(next)}
          label="Accept public applications"
        />
      </div>

      {enabled ? (
        status === 'open' ? (
          <div className="mt-3 flex items-center gap-2 rounded-[10px] border border-white/[0.08] bg-black/25 px-3 py-2">
            <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[#b8babf]">
              {link}
            </span>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(link);
                toast.success('Link copied');
              }}
              className="shrink-0 rounded p-1 text-[#888b91] hover:text-white"
              aria-label="Copy the application link"
            >
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 rounded p-1 text-[#888b91] hover:text-white"
              aria-label="Open the application page"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          </div>
        ) : (
          // The toggle is on but the opening is paused or closed, so the page
          // 404s. Saying so beats letting someone share a dead link.
          <p className="mt-3 flex items-start gap-1.5 text-[12px] text-[#ffb764]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This opening is {status}, so the link will not accept applications until you
            reopen it.
          </p>
        )
      ) : null}
    </GlassCard>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function RequisitionDashboard(): JSX.Element {
  const { requisitionId = '' } = useParams();

  const dash = useQuery({
    queryKey: ['hr', 'requisition-dashboard', requisitionId],
    queryFn: () => getRequisitionDashboard(requisitionId),
    enabled: Boolean(requisitionId),
    refetchInterval: LIVE_POLL_MS,
  });

  if (dash.isLoading) {
    return (
      <div className="flex items-center gap-2 p-8 text-[13px] text-[#888b91]">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading…
      </div>
    );
  }
  if (dash.isError || !dash.data) {
    return (
      <div className="mx-auto max-w-[1100px] p-8">
        <GlassCard className="p-6 text-[13.5px] text-[#e6714f]">
          {errText(dash.error, 'Could not load this opening')}
        </GlassCard>
      </div>
    );
  }

  const { requisition: req, rounds, median_days_in_stage, still_being_read } = dash.data;
  const widest = Math.max(1, ...rounds.map((r) => r.attempted));

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <Link
            to="/hr/requisitions"
            className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-[#888b91] hover:text-white"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            All openings
          </Link>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-[26px] font-semibold tracking-[-0.8px] text-white">
                {req.title}
              </h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-[#888b91]">
                <StatusTag
                  tone={req.status === 'open' ? 'forest' : req.status === 'paused' ? 'amber' : 'neutral'}
                  dot={req.status === 'open'}
                >
                  {req.status}
                </StatusTag>
                <span>{req.level}</span>
                {req.target_hires ? <span>target {req.target_hires} hires</span> : null}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Link
                to={`/hr/requisitions/${requisitionId}/workflow`}
                className="rounded-[10px] border border-white/[0.12] px-3.5 py-2 text-[12.5px] text-[#d5d7da] hover:border-white/25 hover:text-white"
              >
                Workflow
              </Link>
              <Link
                to={`/hr/requisitions/${requisitionId}/decisions`}
                className="inline-flex items-center gap-1.5 rounded-[10px] bg-white px-3.5 py-2 text-[12.5px] font-medium text-black hover:opacity-90"
              >
                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                Decisions
              </Link>
              <StatusControl
                requisitionId={requisitionId}
                status={req.status}
                title={req.title}
              />
            </div>
          </div>
        </header>
      </Reveal>

      {/* Awaiting-you leads, because it is the only number here that nothing
          else in the system will clear. */}
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Awaiting your decision"
          value={String(req.awaiting_decision)}
          feature={req.awaiting_decision > 0}
        />
        <StatCard label="Candidates" value={String(req.total_enrolments)} />
        <StatCard label="Hired" value={String(req.hired)} />
        <StatCard
          label="Median days in stage"
          value={median_days_in_stage === null ? '—' : String(median_days_in_stage)}
        />
      </div>

      {still_being_read > 0 ? (
        <div className="mb-5 flex items-center gap-2 rounded-[14px] border border-white/[0.08] bg-black/25 px-4 py-3 text-[12.5px] text-[#888b91]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          {still_being_read} CV{still_being_read === 1 ? '' : 's'} still being read and
          scored. Their names and fit scores fill in shortly.
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <GlassCard className="p-5">
          <h2 className="text-[15px] font-semibold text-white">Where candidates are</h2>
          {rounds.length === 0 ? (
            <div className="mt-3">
              <p className="text-[13px] leading-relaxed text-[#888b91]">
                No published workflow yet, so nobody is being moved through anything
                automatically.
              </p>
              <Link
                to={`/hr/requisitions/${requisitionId}/workflow`}
                className="mt-3 inline-block text-[13px] text-[var(--accent)] hover:underline"
              >
                Build the hiring process →
              </Link>
            </div>
          ) : (
            <ul className="mt-2 flex list-none flex-col divide-y divide-white/[0.05]">
              {rounds.map((r) => (
                <RoundBar key={r.round_id} round={r} widest={widest} />
              ))}
            </ul>
          )}
        </GlassCard>

        <div className="flex flex-col gap-5">
          <PublicApplyCard
            requisitionId={requisitionId}
            enabled={req.public_apply_enabled}
            status={req.status}
          />

          <GlassCard className="p-5">
            <h2 className="mb-2 text-[15px] font-semibold text-white">By stage</h2>
            {req.funnel.length === 0 ? (
              <p className="text-[12.5px] text-[#888b91]">Nobody has applied yet.</p>
            ) : (
              <ul className="flex list-none flex-col gap-1.5">
                {req.funnel.map((stage) => (
                  <li
                    key={stage.status}
                    className="flex items-center justify-between text-[12.5px]"
                  >
                    <span className="text-[#d5d7da]">{stage.status}</span>
                    <span className="tabular-nums text-white">{stage.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </GlassCard>
        </div>
      </div>
    </div>
  );
}
