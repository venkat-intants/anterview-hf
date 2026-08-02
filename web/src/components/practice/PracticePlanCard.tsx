// PracticePlanCard — the self-serve dashboard's centrepiece.
//
// Shows the competencies the student's target role is assessed on, their own
// score against each, and which one to practise next. It is the consumer-side
// payoff for the whole intelligence layer: the same RoleProfile that plans the
// interview questions and weights the scorecard is what they see themselves
// measured against.
//
// Three states, all deliberate:
//   ready=false            → the "personalize" nudge (skipped or never onboarded)
//   ready, 0 interviews    → the full competency list with "not practised yet"
//   ready, N interviews    → scores, trend, and a focus recommendation
//
// The middle state matters most. A brand-new student is at peak motivation and
// an empty dashboard wastes it — so the plan is useful before they have done
// anything, by showing exactly what they are about to be assessed on.

import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { getPracticePlan, type CompetencyProgress } from '@/api/onboarding';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { cn } from '@/lib/utils';

/** Bar colour by score. Amber below 5, accent at 5-7.5, green above. */
function barColor(score: number): string {
  if (score < 5) return '#ffb764';
  if (score < 7.5) return 'var(--accent)';
  return '#27c93f';
}

function CompetencyRow({
  comp,
  isFocus,
}: {
  comp: CompetencyProgress;
  isFocus: boolean;
}): JSX.Element {
  const pct = comp.score === null ? 0 : Math.max(0, Math.min(100, comp.score * 10));

  return (
    <div className="py-2">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <span className="truncate text-sm">
          {comp.name}
          {isFocus && (
            <span className="ml-2 text-xs" style={{ color: '#ffb764' }}>
              ← practise this
            </span>
          )}
        </span>
        <span className="shrink-0 text-sm tabular-nums">
          {comp.score === null ? (
            <span className="text-xs opacity-40">not practised yet</span>
          ) : (
            <>
              {comp.score.toFixed(1)}
              {comp.trend !== null && comp.trend !== 0 && (
                <span
                  className="ml-1.5 text-xs"
                  style={{ color: comp.trend > 0 ? '#27c93f' : '#ff6b6b' }}
                >
                  {comp.trend > 0 ? '▲' : '▼'} {Math.abs(comp.trend).toFixed(1)}
                </span>
              )}
            </>
          )}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/8">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${pct}%`,
            background: comp.score === null ? 'transparent' : barColor(comp.score),
          }}
        />
      </div>
    </div>
  );
}

export default function PracticePlanCard(): JSX.Element | null {
  const { data: plan, isLoading } = useQuery({
    queryKey: ['practice-plan'],
    queryFn: getPracticePlan,
    staleTime: 60_000,
    // A 403 means this account has no self-serve plan (an HR user landing on
    // /dashboard). Permanent — retrying is noise.
    retry: false,
  });

  if (isLoading || !plan) return null;

  // ── Not personalized yet: the nudge ──────────────────────────────────────
  if (!plan.ready) {
    return (
      <GlassCard className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-[15px] font-semibold">Make this yours</h3>
            <p className="mt-1 text-sm opacity-70">
              Tell us the role you&apos;re aiming for and we&apos;ll build a practice plan —
              the exact skills you&apos;ll be assessed on, and where you stand on each.
            </p>
          </div>
          <Link
            to="/onboarding"
            className="shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition"
            style={{ background: 'var(--accent)', color: '#08131f' }}
          >
            Personalize
          </Link>
        </div>
      </GlassCard>
    );
  }

  const scored = plan.competencies.filter((c) => c.score !== null);
  const fresh = plan.interviews_completed === 0;

  return (
    <GlassCard className="p-5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <h3 className="text-[15px] font-semibold">Your practice plan</h3>
            {plan.domain_label && <StatusTag tone="electric">{plan.domain_label}</StatusTag>}
          </div>
          <p className="text-sm opacity-70">
            {plan.target_role}
            {plan.target_level ? ` · ${plan.target_level}` : ''}
          </p>
        </div>

        {plan.readiness !== null && (
          <div className="text-right">
            <div className="text-2xl font-semibold tabular-nums">
              {Math.round(plan.readiness)}
              <span className="text-sm opacity-50">%</span>
            </div>
            <div className="text-xs opacity-60">readiness</div>
          </div>
        )}
      </div>

      {fresh && (
        <div className="rounded-lg bg-white/5 px-3 py-2 text-sm opacity-80">
          This is what a <strong>{plan.target_role}</strong> interview will assess. Do one
          mock interview and every bar below fills in.
        </div>
      )}

      <div className="divide-y divide-white/5">
        {plan.competencies.map((c) => (
          <CompetencyRow key={c.id} comp={c} isFocus={c.id === plan.focus_competency_id} />
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <p className="text-xs opacity-60">
          {plan.interviews_completed === 0
            ? 'No mock interviews yet'
            : `${plan.interviews_completed} mock interview${plan.interviews_completed === 1 ? '' : 's'} done`}
          {scored.length > 0 && plan.never_probed.length > 0 && (
            <> · {plan.never_probed.length} competenc{plan.never_probed.length === 1 ? 'y' : 'ies'} not covered yet</>
          )}
        </p>
        <div className="flex items-center gap-2">
          <Link
            to="/onboarding"
            className={cn(
              'rounded-lg px-3 py-1.5 text-xs transition bg-white/5 hover:bg-white/10',
            )}
          >
            Change role
          </Link>
          <Link
            to="/start"
            className="rounded-lg px-3 py-1.5 text-sm font-medium transition"
            style={{ background: 'var(--accent)', color: '#08131f' }}
          >
            {plan.focus_competency_name
              ? `Practise ${plan.focus_competency_name}`
              : 'Start a mock interview'}
          </Link>
        </div>
      </div>
    </GlassCard>
  );
}
