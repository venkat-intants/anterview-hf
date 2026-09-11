// PipelineBoard — the pipeline as columns rather than a list.
//
// §6.4. The same rows the table shows, arranged by where each candidate is, so
// "where is everyone" is one glance instead of a scan.
//
// NOTHING HERE MOVES ANYBODY.
//
// The spec's own warning, and the right one: a candidate's stage is not a
// property the UI owns. It is derived from what has actually happened to them —
// an exam submitted, an interview scored, a person deciding — and every one of
// those goes through an endpoint that records who did it and why.
//
// Drag-and-drop on a board like this looks obvious and is a trap. Dropping
// somebody into "Interviewed" would either lie (a display change with no
// interview behind it) or silently fabricate the event that justifies it.
// Neither is a thing to do to a hiring record, so a card here opens the
// candidate and the real actions live where they are validated.
//
// COLUMNS ARE THE DERIVED STATUS, NOT A STAGE MACHINE. `status` on a pipeline
// row is computed in SQL from the funnel and is never persisted on read. The
// board reflects it; it does not maintain it.

import { useMemo } from 'react';
import type { PipelineRow, PipelineStatus } from '@/api/pipeline';
import { applicationKey } from '@/lib/applicationKey';
import { Avatar, StatusTag, type TagTone } from '@/design/components/primitives';
import { cn } from '@/lib/utils';

/**
 * The columns, in the order a candidate passes through them.
 *
 * 'rejected' shares the last column with 'hired' rather than getting one of
 * its own: they are both "decided", the board is about who still needs
 * attention, and a column of rejections is a wall of bad news nobody scans.
 */
const COLUMNS: { key: string; label: string; statuses: PipelineStatus[] }[] = [
  // 'held': the automatic shortlist could not decide and a person must — still
  // at the applied stage, and exactly who needs attention. Without a column it
  // would silently drop off the board.
  { key: 'new', label: 'Applied', statuses: ['new', 'held'] },
  { key: 'shortlisted', label: 'Shortlisted', statuses: ['shortlisted'] },
  { key: 'interviewed', label: 'Interviewed', statuses: ['interviewed'] },
  { key: 'decided', label: 'Decided', statuses: ['hired', 'rejected'] },
];

/**
 * Outcome colours for the Decided column only.
 *
 * Everywhere else the column heading already says the status, and repeating it
 * on every card is noise. "Decided" is the exception: it holds hired and
 * rejected together, so the card has to say which.
 */
const DECIDED_TONE: Record<string, TagTone> = {
  hired: 'forest',
  rejected: 'ember',
};

function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] ?? '')
    .join('')
    .toUpperCase();
}

/**
 * The most advanced score this candidate has, and what it is.
 *
 * One number rather than three columns of mostly-blanks: a card is a glance,
 * and the interesting figure is the furthest one they have reached. Labelled,
 * because 78 out of 100 on a CV and 7.8 out of 10 in an interview are not
 * comparable and a bare number invites reading them as if they were.
 */
function headlineScore(row: PipelineRow): { label: string; value: string } | null {
  if (row.interview_score != null) {
    return { label: 'Interview', value: `${row.interview_score.toFixed(1)}/10` };
  }
  if (row.best_exam_percent != null) {
    return { label: 'Exam', value: `${row.best_exam_percent}%` };
  }
  if (row.ats_overall != null) {
    return { label: 'Resume', value: `${row.ats_overall}/100` };
  }
  return null;
}

function Card({
  row,
  showOutcome,
  onOpen,
}: {
  row: PipelineRow;
  /** Only the Decided column, where the heading does not say which it was. */
  showOutcome: boolean;
  onOpen: () => void;
}) {
  const score = headlineScore(row);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open details for ${row.full_name}`}
      className="w-full rounded-[14px] border border-border bg-card p-3 text-left transition-colors hover:border-[rgba(var(--accent-rgb),0.3)] focus:outline-none focus-visible:border-[var(--accent)]"
    >
      <div className="flex items-center gap-2.5">
        <Avatar initials={initialsOf(row.full_name)} size={28} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-medium text-foreground">{row.full_name}</p>
          <p className="truncate text-[11.5px] text-[var(--ui-faint)]">
            {row.opening_title ?? row.target_job_title}
          </p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {showOutcome ? (
          <StatusTag tone={DECIDED_TONE[row.status] ?? 'neutral'}>{row.status}</StatusTag>
        ) : null}
        {score ? (
          <span className="text-[11.5px] text-muted-foreground">
            {score.label} <span className="text-[var(--ui-soft)]">{score.value}</span>
          </span>
        ) : null}
      </div>
    </button>
  );
}

export default function PipelineBoard({
  rows,
  onOpen,
}: {
  rows: PipelineRow[];
  onOpen: (row: PipelineRow) => void;
}) {
  const columns = useMemo(
    () =>
      COLUMNS.map((c) => ({
        ...c,
        rows: rows.filter((r) => c.statuses.includes(r.status)),
      })),
    [rows],
  );

  return (
    <div
      // Scrolls sideways rather than squeezing four columns onto a phone —
      // a column three characters wide is not a column.
      className="overflow-x-auto pb-2"
      role="region"
      aria-label="Candidate pipeline board"
    >
      <div className="flex min-w-[860px] gap-3">
        {columns.map((col) => (
          <section
            key={col.key}
            aria-labelledby={`col-${col.key}`}
            className="flex w-full min-w-0 flex-col gap-2 rounded-[16px] border border-border bg-white/[0.015] p-2.5"
          >
            <h3
              id={`col-${col.key}`}
              className="flex items-baseline justify-between px-1 text-[12px] font-medium text-[var(--ui-soft)]"
            >
              {col.label}
              <span className="text-[11.5px] text-[var(--ui-faint)]">{col.rows.length}</span>
            </h3>

            {col.rows.length === 0 ? (
              // Named rather than blank: an empty column that says nothing
              // looks like a loading failure.
              <p className="px-1 py-3 text-[11.5px] text-[var(--ui-faint)]">Nobody here.</p>
            ) : (
              col.rows.map((row) => (
                <Card
                  key={applicationKey(row)}
                  row={row}
                  showOutcome={col.key === 'decided'}
                  onOpen={() => onOpen(row)}
                />
              ))
            )}
          </section>
        ))}
      </div>

      <p className={cn('mt-3 px-1 text-[11.5px] text-[var(--ui-faint)]')}>
        Cards open a candidate. Moving somebody happens through their own record, so
        every move is recorded with who made it.
      </p>
    </div>
  );
}
