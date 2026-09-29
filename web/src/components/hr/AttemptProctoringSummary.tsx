// AttemptProctoringSummary — the HR-facing view of GET
// /hr/exams/{examId}/attempts/{attemptId}/proctoring (camera-proctoring
// contract §7). Rendered on ExamAttemptDetail's overview.
//
// THE LOAD-BEARING RULE ON THIS SCREEN: a score tells HR nothing about *why*
// it dropped, and a reviewer under time pressure is exactly the person most
// likely to pattern-match "low score = cheated" regardless of which events
// produced it (security review). So this panel:
//
//   1. Leads with what actually happened — events, grouped by kind, with
//      times and durations — and puts the score last, as a summary of that,
//      never as the headline.
//   2. Renders `gaze_away` as its own clearly-labelled, informational block,
//      separate from the counted signals. It is the least reliable signal
//      (weighted lowest on the server on purpose, contract §2) and the most
//      likely to fire on someone thinking, or using assistive technology. It
//      never triggers anything by itself and is shown for context only.
//   3. Never states or implies a conclusion — no "suspicious", no verdict
//      styling, no per-candidate ranking. CLAUDE.md hard constraint 9: this
//      informs a human, it does not decide.
//   4. When the camera was never in use, says so in words. The server never
//      puts a camera-signal key in `counts` in that case, so there is
//      nothing to render as a (misleadingly clean-looking) zero.
//
// English only — HR console copy is EN-only by house convention; see
// CLAUDE.md hard constraint 5.

import { useQuery } from '@tanstack/react-query';
import { getAttemptProctoring, type ProctoringEvent } from '@/api/exams';
import { GlassCard } from '@/design/components/primitives';
import { Info, Loader2, ShieldCheck, VideoOff } from '@/design/components/icons';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** Friendly, neutral labels — never a judgement word ("suspicious",
 *  "violation"). Anything the server sends that isn't in this map still
 *  renders, under its raw event_type, rather than disappearing. */
const EVENT_LABELS: Record<string, string> = {
  multiple_faces: 'More than one face visible',
  face_absent: 'No face visible',
  fullscreen_exit: 'Left fullscreen',
  tab_blur: 'Switched away from the exam tab',
  copy: 'Copied text',
  paste: 'Pasted text',
};

/** Display order mirrors the severity weighting in the camera-proctoring
 *  contract §2 (highest weight first), purely for a stable, predictable
 *  reading order — it plays no part in the score itself. */
const EVENT_ORDER = ['multiple_faces', 'face_absent', 'fullscreen_exit', 'tab_blur', 'copy', 'paste'];

function orderIndex(type: string): number {
  const i = EVENT_ORDER.indexOf(type);
  return i === -1 ? EVENT_ORDER.length : i;
}

function labelFor(type: string): string {
  return EVENT_LABELS[type] ?? type;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** 6 -> "6s", 65 -> "1m 5s", 120 -> "2m". Seconds and minutes are different
 *  facts (contract §7 item 5) — always show the real duration, never just a
 *  tick mark. */
function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function eventsOfType(events: ProctoringEvent[], type: string): ProctoringEvent[] {
  return events.filter((e) => e.event_type === type).sort((a, b) => a.started_at.localeCompare(b.started_at));
}

export default function AttemptProctoringSummary({
  examId,
  attemptId,
}: {
  examId: string;
  attemptId: string;
}): JSX.Element | null {
  const enabled = Boolean(examId) && Boolean(attemptId);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempt', attemptId, 'proctoring'],
    queryFn: () => getAttemptProctoring(examId, attemptId),
    enabled,
    retry: false,
  });

  if (!enabled) return null;

  if (isLoading) {
    return (
      <GlassCard className="mt-5 p-5" data-testid="attempt-proctoring-summary">
        <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading proctoring timeline…
        </div>
      </GlassCard>
    );
  }

  if (isError) {
    return (
      <GlassCard className="mt-5 p-5" data-testid="attempt-proctoring-summary">
        <p className="text-[12.5px] text-muted-foreground">
          {errText(error, 'Could not load proctoring data for this attempt.')}
        </p>
      </GlassCard>
    );
  }

  if (!data) return null;

  // gaze_away is deliberately pulled out of every list below (contract §7
  // item 1) — it must never be folded back in, however tempting a single
  // "all events" list would be.
  const gazeEvents = eventsOfType(data.events, 'gaze_away');
  const gazeCount = data.counts.gaze_away ?? 0;

  const otherCounts = Object.entries(data.counts)
    .filter(([type]) => type !== 'gaze_away')
    .sort(([a], [b]) => orderIndex(a) - orderIndex(b));
  const otherEventsTotal = otherCounts.reduce((n, [, c]) => n + c, 0);

  return (
    <GlassCard className="mt-5 p-5" data-testid="attempt-proctoring-summary">
      <div className="flex items-center gap-1.5">
        <ShieldCheck size={14} className="text-[var(--ui-faint)]" aria-hidden="true" />
        <h3 className="text-[13px] font-medium text-foreground">Proctoring timeline</h3>
      </div>

      {/* ── The timeline is incomplete and must say so. The client swallows a
          429 exactly like a lost packet, so without this a reviewer cannot
          tell a quiet exam from one we stopped recording, and a short record
          reads as a clean one. Worded as a fact about OUR collection, not
          about the candidate — being throttled is not something they did. ── */}
      {data.events_dropped && (
        <p
          className="mt-2.5 flex items-start gap-1.5 text-[12.5px] leading-relaxed text-muted-foreground"
          data-testid="proctoring-incomplete-notice"
        >
          <Info size={13} className="mt-0.5 shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
          <span>
            This timeline is incomplete — some events could not be recorded during the attempt.
            Read what follows as a partial record, and not as evidence about the candidate.
          </span>
        </p>
      )}

      {/* ── Camera not in use — say so, rather than rendering the camera
          signals as zero (contract §7 item 4). ── */}
      {!data.camera_in_use && (
        <p className="mt-2.5 flex items-start gap-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
          <VideoOff size={13} className="mt-0.5 shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
          <span>
            Camera proctoring was not enabled for this attempt — presence, multiple-face and gaze
            signals were not measured.
            {otherEventsTotal === 0 ? ' No other proctoring events were recorded either.' : ''}
          </span>
        </p>
      )}

      {data.camera_in_use && otherEventsTotal === 0 && (
        <p className="mt-2.5 text-[12.5px] text-muted-foreground">
          No fullscreen, tab, face or copy/paste events were recorded for this attempt.
        </p>
      )}

      {/* ── The events that actually happened, grouped by kind, with times
          and durations — leads the panel; the score comes last. ── */}
      {otherCounts.length > 0 && (
        <div className="mt-3 flex flex-col gap-3" data-testid="proctoring-event-groups">
          {otherCounts.map(([type, count]) => {
            const occurrences = eventsOfType(data.events, type);
            return (
              <div key={type}>
                <p className="text-[12px] font-medium text-foreground">
                  {labelFor(type)} · {count} time{count === 1 ? '' : 's'}
                </p>
                {occurrences.length > 0 && (
                  <ul className="mt-1 flex flex-col gap-0.5" aria-label={`${labelFor(type)} occurrences`}>
                    {occurrences.map((e, i) => (
                      <li key={`${type}-${i}`} className="font-mono text-[11.5px] text-muted-foreground">
                        {formatTime(e.started_at)}
                        {e.duration_seconds != null ? ` — lasted ${formatDuration(e.duration_seconds)}` : ''}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── gaze_away — its own informational block, never merged into the
          counted signals above (contract §7 item 1). Camera-only, so it only
          exists to show when the camera actually ran. ── */}
      {data.camera_in_use && (
        <div
          className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3"
          data-testid="proctoring-gaze-line"
        >
          <div className="flex items-center gap-1.5">
            <Info size={12} className="text-[var(--ui-faint)]" aria-hidden="true" />
            <p className="text-[12px] font-medium text-foreground">
              Looked away from the screen — informational only
            </p>
          </div>
          {gazeCount === 0 ? (
            <p className="mt-1 text-[11.5px] text-muted-foreground">Not observed during this attempt.</p>
          ) : (
            <ul className="mt-1 flex flex-col gap-0.5" aria-label="Looked-away occurrences">
              {gazeEvents.map((e, i) => (
                <li key={`gaze-${i}`} className="font-mono text-[11.5px] text-muted-foreground">
                  {formatTime(e.started_at)} — looked away for{' '}
                  {formatDuration(e.duration_seconds ?? 0)}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--ui-faint)]">
            This alone never ends an exam and is the least reliable signal here — looking away has
            many innocent explanations (thinking, a motor or visual difference, assistive
            technology). It is shown for context, not as evidence.
          </p>
        </div>
      )}

      {/* ── The score — a summary of the above, shown last and without any
          pass/fail styling (contract §7 item 3, CLAUDE.md hard constraint 9). ── */}
      <div className="mt-4 border-t border-border pt-3">
        <p className="text-[11.5px] text-muted-foreground">
          {data.integrity_score !== null ? (
            <>
              Integrity score{' '}
              <span className="font-mono text-foreground">{data.integrity_score}/100</span> — a
              weighted summary of the events above. It does not decide anything about the
              candidate; HR reviews the timeline.
            </>
          ) : (
            'No integrity score recorded for this attempt.'
          )}
        </p>
      </div>
    </GlassCard>
  );
}
