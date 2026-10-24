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
import { useTranslation } from 'react-i18next';

import { getPracticePlan, type CompetencyProgress } from '@/api/onboarding';
import { ApiError } from '@/api/client';
import { LoadErrorPanel } from '@/components/candidate/LoadErrorPanel';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Card } from '@/design/components/card';
import { StatusTag } from '@/design/components/primitives';
import { Check } from '@/design/components/icons';
import { cn } from '@/lib/utils';

/** Mon…Sun, as single letters. Index 0 is Monday. */
const DOW_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'] as const;

interface PracticePlanCardProps {
  /**
   * Leave out the "start a mock interview" button. The dashboard sets this when
   * its next-step strip is already the practice call to action, so the page
   * carries exactly one.
   */
  hideStart?: boolean;
  /** Leave out the "personalise" button, for the same reason (the strip says it). */
  hidePersonalize?: boolean;
  /** Days of this week (0 = Mon) with at least one practice interview; draws the weekly streak. */
  thisWeek?: ReadonlySet<number>;
}

/** Bar colour by score. Warn below 5, accent at 5-7.5, ok above. */
function barColor(score: number): string {
  if (score < 5) return 'var(--ui-warn)';
  if (score < 7.5) return 'var(--accent)';
  return 'var(--ui-ok)';
}

function CompetencyRow({
  comp,
  isFocus,
}: {
  comp: CompetencyProgress;
  isFocus: boolean;
}): JSX.Element {
  const { t } = useTranslation();
  const pct = comp.score === null ? 0 : Math.max(0, Math.min(100, comp.score * 10));

  return (
    <div className="py-2.5">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="min-w-0 text-[14px] text-foreground">
          {comp.name}
          {isFocus && (
            <span className="ml-2 text-[12px] font-semibold text-[var(--ui-warn)]">
              {t('dashboard.planPractiseThis')}
            </span>
          )}
        </span>
        <span className="shrink-0 text-[14px] font-semibold tabular-nums text-foreground">
          {comp.score === null ? (
            <span className="text-[12px] font-normal text-muted-foreground">
              {t('dashboard.planNotPracticed')}
            </span>
          ) : (
            <>
              {comp.score.toFixed(1)}
              {comp.trend !== null && comp.trend !== 0 && (
                <span
                  className="ml-1.5 text-[12px]"
                  style={{ color: comp.trend > 0 ? 'var(--ui-ok)' : 'var(--ui-danger)' }}
                >
                  {comp.trend > 0 ? '▲' : '▼'} {Math.abs(comp.trend).toFixed(1)}
                </span>
              )}
            </>
          )}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--ui-inset-strong)]">
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

export default function PracticePlanCard({
  hideStart = false,
  hidePersonalize = false,
  thisWeek,
}: PracticePlanCardProps = {}): JSX.Element | null {
  const { t } = useTranslation();
  const {
    data: plan,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['practice-plan'],
    queryFn: getPracticePlan,
    staleTime: 60_000,
    // A 403 means this account has no self-serve plan (an HR user landing on
    // /dashboard). Permanent — retrying is noise.
    retry: false,
  });

  // A skeleton, not null, while loading: this card sits at the top of the
  // dashboard, so collapsing to nothing and then appearing shoves everything
  // below it down a beat after arrival.
  if (isLoading) {
    return (
      <div className="rounded-card border border-border bg-card p-6" aria-busy="true">
        <div className="space-y-3">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-56" />
          <div className="space-y-3 pt-2">
            <Skeleton className="h-2 w-full rounded-full" />
            <Skeleton className="h-2 w-full rounded-full" />
            <Skeleton className="h-2 w-full rounded-full" />
          </div>
        </div>
      </div>
    );
  }

  // No plan at all — an HR/admin account whose plan request 403s. Render
  // nothing rather than a candidate-shaped card they cannot act on. Any other
  // failure (offline, a 5xx) is a candidate whose plan exists and did not
  // load: losing the dashboard's centrepiece without a word reads as "you have
  // no plan", so it says so and offers to try again.
  if (!plan) {
    if (isError && !(error instanceof ApiError && error.status === 403)) {
      return (
        <LoadErrorPanel
          title={t('dashboard.planLoadError')}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      );
    }
    return null;
  }

  // ── Not personalized yet: the nudge ──────────────────────────────────────
  if (!plan.ready) {
    return (
      <Card
        as="section"
        title={t('dashboard.planNudgeTitle')}
        description={t('dashboard.planNudgeBody')}
        // Outline, never the filled button: the page's one primary is the next step.
        actions={
          hidePersonalize ? undefined : (
            <Link to="/onboarding" className={buttonVariants({ variant: 'outline' })}>
              {t('dashboard.planPersonalize')}
            </Link>
          )
        }
      />
    );
  }

  const scored = plan.competencies.filter((c) => c.score !== null);
  const fresh = plan.interviews_completed === 0;

  return (
    <Card
      as="section"
      title={t('dashboard.planTitle')}
      description={
        <>
          {plan.target_role}
          {plan.target_level ? ` · ${plan.target_level}` : ''}
        </>
      }
      actions={
        plan.readiness !== null ? (
          <div className="text-right">
            <div className="font-display text-[26px] font-bold leading-none tabular-nums text-foreground">
              {Math.round(plan.readiness)}
              <span className="text-[14px] text-muted-foreground">%</span>
            </div>
            <div className="mt-1 text-[12px] text-muted-foreground">
              {t('dashboard.planReadiness')}
            </div>
          </div>
        ) : undefined
      }
    >
      <div className="space-y-4">
        {plan.domain_label && (
          <div>
            <StatusTag tone="electric">{plan.domain_label}</StatusTag>
          </div>
        )}

        {fresh && (
          <div className="rounded-[12px] bg-[var(--ui-highlight-wash)] px-4 py-3 text-[14px] text-foreground">
            {t('dashboard.planFresh', { role: plan.target_role })}
          </div>
        )}

        <div className="divide-y divide-border">
          {plan.competencies.map((c) => (
            <CompetencyRow key={c.id} comp={c} isFocus={c.id === plan.focus_competency_id} />
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
          <p className="text-[13px] text-muted-foreground">
            {plan.interviews_completed === 0
              ? t('dashboard.planNoMocks')
              : t('dashboard.planMocksDone', { count: plan.interviews_completed })}
            {scored.length > 0 && plan.never_probed.length > 0 && (
              <> · {t('dashboard.planNotCovered', { count: plan.never_probed.length })}</>
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/onboarding" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
              {t('dashboard.planChangeRole')}
            </Link>
            {hideStart ? null : (
              // Outline: a page with an application waiting has its primary in
              // the next-step strip, and this is the one practice call to action.
              <Link to="/start" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
                {plan.focus_competency_name
                  ? t('dashboard.planPractiseFocus', { competency: plan.focus_competency_name })
                  : t('dashboard.planStartMock')}
              </Link>
            )}
          </div>
        </div>

        {thisWeek ? (
          <div>
            <p className="mb-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--ui-soft)]">
              {t('dashboard.thisWeekTitle')}
            </p>
            <div
              className="flex items-center gap-1.5"
              role="img"
              aria-label={`${t('dashboard.thisWeekTitle')}: ${thisWeek.size}/7`}
            >
              {DOW_LABELS.map((lbl, i) => (
                <span
                  key={i}
                  aria-hidden="true"
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-full text-[12px] font-semibold',
                    thisWeek.has(i)
                      ? 'border border-[var(--ui-ok)] bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]'
                      : 'border border-border bg-[var(--ui-inset)] text-[var(--ui-soft)]',
                  )}
                >
                  {thisWeek.has(i) ? <Check size={14} /> : lbl}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
