// workloadPanel.ts — pure helpers for WorkloadTab (PH4-O5), kept out of the
// component file so it exports only the component (react-refresh/
// only-export-components — this repo lints with --max-warnings 0).

import type { WorkloadRow } from '@/api/scheduling';
import { isoDateLabel, isoWeekLabel } from '@/lib/timezone';

export type WorkloadPeriodPreset = 'this_week' | 'next_week' | 'next_30_days';

function startOfWeek(from: Date): Date {
  const d = new Date(from);
  d.setHours(0, 0, 0, 0);
  const mondayOffset = (d.getDay() + 6) % 7; // 0 = Monday
  d.setDate(d.getDate() - mondayOffset);
  return d;
}

export function presetRange(
  preset: WorkloadPeriodPreset,
  now: Date = new Date(),
): { start: string; end: string } {
  if (preset === 'this_week') {
    const start = startOfWeek(now);
    const end = new Date(start);
    end.setDate(end.getDate() + 7);
    return { start: start.toISOString(), end: end.toISOString() };
  }
  if (preset === 'next_week') {
    const start = startOfWeek(now);
    start.setDate(start.getDate() + 7);
    const end = new Date(start);
    end.setDate(end.getDate() + 7);
    return { start: start.toISOString(), end: end.toISOString() };
  }
  const start = new Date(now);
  const end = new Date(now);
  end.setDate(end.getDate() + 30);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** Every flag, said in words — never colour alone. */
export function flagSentences(row: WorkloadRow): string[] {
  const out: string[] = [];
  for (const d of row.over_allocated_days) out.push(`Over the daily limit on ${isoDateLabel(d)}`);
  for (const w of row.over_allocated_weeks) out.push(`Over the weekly limit in ${isoWeekLabel(w)}`);
  if (row.outside_availability > 0) {
    out.push(
      `${row.outside_availability} session${row.outside_availability === 1 ? '' : 's'} outside their availability`,
    );
  }
  if (row.overdue_scorecards > 0) {
    out.push(`${row.overdue_scorecards} overdue scorecard${row.overdue_scorecards === 1 ? '' : 's'}`);
  }
  if (row.conflicts > 0) {
    out.push(`${row.conflicts} overlapping session${row.conflicts === 1 ? '' : 's'}`);
  }
  return out;
}
