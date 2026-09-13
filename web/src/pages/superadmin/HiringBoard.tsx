// HiringBoard — every open opening in the company, and whether it is on course. E3.
//
// For the company super admin, who until now could see no hiring at all. It is
// a board, not a console: read-only, scanned rather than read, so the summary
// leads and each row carries its state as a chip AND a sentence.
//
// The health is computed from how candidates have actually moved, never typed
// in. "Not published" is its own state, and leads, because an opening taking
// applications with no live workflow is the one problem nothing else on the
// board will surface — nobody who applies moves anywhere.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2 } from '@/design/components/icons';
import { GlassCard } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import { getHiringBoard, type BoardOpening, type HealthBand } from '@/api/companyBoard';

const BAND: Record<HealthBand, { label: string; chip: string }> = {
  not_published: {
    label: 'Not published',
    chip: 'border-[var(--ui-danger)]/40 text-[var(--ui-danger)]',
  },
  at_risk: { label: 'At risk', chip: 'border-[var(--ui-warn)]/45 text-[var(--ui-warn)]' },
  watch: { label: 'Watch', chip: 'border-[var(--ui-info)]/40 text-[var(--ui-info)]' },
  on_track: { label: 'On track', chip: 'border-[var(--ui-ok)]/40 text-[var(--ui-ok)]' },
};
const ORDER: HealthBand[] = ['not_published', 'at_risk', 'watch', 'on_track'];

function workflowText(o: BoardOpening): string {
  if (o.workflow_state === 'published') {
    return `v${o.published_version} live${o.draft_version ? ` · v${o.draft_version} draft` : ''}`;
  }
  return o.workflow_state === 'draft' ? `v${o.draft_version} draft only` : 'none';
}

export default function HiringBoard(): JSX.Element {
  const [filter, setFilter] = useState<HealthBand | null>(null);
  const board = useQuery({
    queryKey: ['superadmin', 'hiring-board'],
    queryFn: getHiringBoard,
    refetchInterval: LIVE_POLL_MS,
  });

  const openings = (board.data?.openings ?? []).filter((o) => !filter || o.health.band === filter);

  return (
    <div className="mx-auto w-full max-w-[1200px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">Hiring board</h1>
          <p className="mt-1 max-w-[72ch] text-[13.5px] leading-relaxed text-muted-foreground">
            Every open opening in the company. Health is worked out from how candidates have
            actually moved through each workflow — nobody types it in. Read-only: the hiring
            decisions stay with your HR managers.
          </p>
        </header>
      </Reveal>

      {board.data ? (
        <div className="mb-5 flex flex-wrap gap-2" role="group" aria-label="Filter by health">
          {ORDER.map((band) => (
            <button
              key={band}
              type="button"
              aria-pressed={filter === band}
              onClick={() => setFilter(filter === band ? null : band)}
              className={cn(
                'rounded-full border px-3 py-1 text-[12.5px] tabular-nums',
                BAND[band].chip,
                filter === band ? 'bg-[var(--ui-inset)]' : 'bg-transparent',
              )}
            >
              {BAND[band].label} · {board.data.summary[band] ?? 0}
            </button>
          ))}
        </div>
      ) : null}

      {board.isLoading ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading the board…
        </div>
      ) : board.isError ? (
        <GlassCard className="flex items-center gap-2 p-6 text-[13.5px] text-[var(--ui-danger)]">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          Could not load the hiring board.
        </GlassCard>
      ) : openings.length === 0 ? (
        <GlassCard className="p-10 text-center text-[13.5px] text-muted-foreground">
          {filter ? 'No openings in this state.' : 'There are no open openings.'}
        </GlassCard>
      ) : (
        <GlassCard className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-border text-[11.5px] uppercase tracking-wide text-[var(--ui-faint)]">
                  <th className="px-4 py-3 font-medium">Opening</th>
                  <th className="px-3 py-3 font-medium">Status</th>
                  <th className="px-3 py-3 font-medium">Workflow</th>
                  <th className="px-3 py-3 text-right font-medium">Applied</th>
                  <th className="px-3 py-3 text-right font-medium">In play</th>
                  <th className="px-3 py-3 text-right font-medium">Hired</th>
                  <th className="px-3 py-3 text-right font-medium">Awaiting decision</th>
                  <th className="px-4 py-3 font-medium">Health</th>
                </tr>
              </thead>
              <tbody>
                {openings.map((o) => (
                  <tr
                    key={o.requisition_id}
                    className="border-b border-border/60 align-top last:border-b-0"
                    data-testid={`board-row-${o.requisition_id}`}
                  >
                    <td className="px-4 py-3">
                      <Link
                        to={`/superadmin/requisitions/${o.requisition_id}`}
                        className="font-medium text-foreground hover:underline"
                      >
                        {o.title}
                      </Link>
                      {o.location ? (
                        <div className="text-[12px] text-muted-foreground">{o.location}</div>
                      ) : null}
                    </td>
                    <td className="px-3 py-3 text-[var(--ui-soft)]">{o.status}</td>
                    <td className="px-3 py-3 text-[var(--ui-soft)]">{workflowText(o)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{o.applied}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{o.in_play}</td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      {o.target_hires ? `${o.hired} of ${o.target_hires}` : o.hired}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{o.awaiting_decision}</td>
                    <td className="max-w-[340px] px-4 py-3">
                      <span
                        className={cn(
                          'inline-block rounded-full border px-2 py-0.5 text-[11.5px] font-medium',
                          BAND[o.health.band].chip,
                        )}
                      >
                        {BAND[o.health.band].label}
                      </span>
                      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                        {o.health.reason}
                      </p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
