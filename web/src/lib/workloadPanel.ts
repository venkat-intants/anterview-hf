// workloadPanel.ts — pure helpers for WorkloadTab (PH4-O5), kept out of the
// component file so it exports only the component (react-refresh/
// only-export-components — this repo lints with --max-warnings 0).

import type { WorkloadRow } from '@/api/scheduling';
import { isoDateLabel, isoWeekLabel } from '@/lib/timezone';

export type WorkloadPeriodPreset = 'this_week' | 'next_week' | 'next_30_days';

/** The server counts days and ISO weeks in the company's working zone
 *  (panel_workload.WORK_TZ = Asia/Kolkata), so the week presets are cut there
 *  too. Computing them in the browser's zone (IV-L11) put a Sunday-evening
 *  interview for an HR user abroad in a different week from the server's. */
export const WORK_TIMEZONE_LABEL = 'India time (IST)';
/** IST is UTC+05:30 all year (no daylight saving), so a fixed offset is exact. */
const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

/** Monday 00:00 IST of the week containing `from`, as a Date (an instant). */
function startOfWorkWeek(from: Date): Date {
  // Shift into IST wall time, read it with the UTC getters, then shift back.
  const wall = new Date(from.getTime() + IST_OFFSET_MS);
  const mondayOffset = (wall.getUTCDay() + 6) % 7; // 0 = Monday
  const midnightWall = Date.UTC(
    wall.getUTCFullYear(),
    wall.getUTCMonth(),
    wall.getUTCDate() - mondayOffset,
  );
  return new Date(midnightWall - IST_OFFSET_MS);
}

export function presetRange(
  preset: WorkloadPeriodPreset,
  now: Date = new Date(),
): { start: string; end: string } {
  if (preset === 'this_week' || preset === 'next_week') {
    const monday = startOfWorkWeek(now).getTime() + (preset === 'next_week' ? 7 * DAY_MS : 0);
    return {
      start: new Date(monday).toISOString(),
      end: new Date(monday + 7 * DAY_MS).toISOString(),
    };
  }
  return {
    start: now.toISOString(),
    end: new Date(now.getTime() + 30 * DAY_MS).toISOString(),
  };
}

/** A capacity field's problem, or null. Blank is valid (the company default);
 *  otherwise a whole number from 1 to `max`, as the server requires. */
export function capacityProblem(raw: string, max: number, which: 'Daily' | 'Weekly'): string | null {
  if (raw.trim() === '') return null;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1 || n > max) {
    return `${which} limit must be a whole number from 1 to ${max}, or blank for the default.`;
  }
  return null;
}

/** Every flag, said in words — never colour alone. */
export function flagSentences(row: WorkloadRow): string[] {
  const out: string[] = [];
  for (const d of row.over_allocated_days) out.push(`Over the daily limit on ${isoDateLabel(d)}`);
  for (const w of row.over_allocated_weeks) out.push(`Over the weekly limit in ${isoWeekLabel(w)}`);
  if (row.outside_availability > 0) {
    out.push(
      `${row.outside_availability} interview${row.outside_availability === 1 ? '' : 's'} outside their availability`,
    );
  }
  if (row.overdue_scorecards > 0) {
    out.push(`${row.overdue_scorecards} overdue scorecard${row.overdue_scorecards === 1 ? '' : 's'}`);
  }
  if (row.conflicts > 0) {
    out.push(`${row.conflicts} overlapping interview${row.conflicts === 1 ? '' : 's'}`);
  }
  return out;
}
