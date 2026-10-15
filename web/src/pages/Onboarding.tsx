// Onboarding — the one-time personalization wizard for self-serve users.
//
// Four short steps: name → goal → target role + level → language. The role
// step is the load-bearing one: it feeds the role engine, which decides what
// the mock interview asks about and what the practice plan measures. Every
// other answer is nice-to-have.
//
// Two rules this file keeps:
//   1. Skip is always visible. A student who just wants to talk to the thing
//      should never be trapped behind a form.
//   2. The role step shows LIVE what we understood — the occupational family
//      and the competencies. If we misread "CNC Operator" as something else,
//      they find out here, not after a wasted 10-minute interview.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import {
  GOAL_OPTIONS,
  LANGUAGE_OPTIONS,
  LEVEL_OPTIONS,
  getOnboardingStatus,
  getPracticePlan,
  previewPracticePlan,
  skipOnboarding,
  submitOnboarding,
  type OnboardingGoal,
  type PracticePlan,
  type TargetLevel,
} from '@/api/onboarding';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { StatusTag } from '@/design/components/primitives';
import { Page, Stepper, type Step } from '@/design/components/page';
import { Check } from '@/design/components/icons';

const TOTAL_STEPS = 4;

/** The wizard's four steps, in order. Labels are `practice.step*` keys. */
const WIZARD_STEPS: { key: string; labelKey: string }[] = [
  { key: '1', labelKey: 'practice.stepName' },
  { key: '2', labelKey: 'practice.stepGoal' },
  { key: '3', labelKey: 'practice.stepRole' },
  { key: '4', labelKey: 'practice.stepLanguage' },
];

/** Where every page that starts an interview reads the spoken language from.
 *  Mirrors the constant in StartInterview/JobsList/Interview/AppShell. */
const INTERVIEW_LANGUAGE_KEY = 'intants:interview-language';

/** Translation keys for the wizard's option lists.
 *
 * Keyed off the API's option `value`, not its `label`: api/onboarding.ts is
 * the transport contract and its English label strings are the fallback for
 * anything the bundles have not caught up with. */
// Capitalised: these are concatenated as `onboarding.goal${KEY}`, so the value
// has to start where the bundle key does — `goalCampusPlacement`, not
// `goalcampusPlacement`. i18next does not fold case, so lower-camel values
// missed every lookup and silently fell through to the English defaultValue,
// leaving all ten Hindi and Telugu strings on step 2 dead. LEVEL_KEYS below
// already capitalises, which is what made the mismatch invisible.
const GOAL_KEYS: Record<OnboardingGoal, string> = {
  campus_placement: 'CampusPlacement',
  first_job: 'FirstJob',
  switching_field: 'SwitchingField',
  interview_soon: 'InterviewSoon',
  general_practice: 'GeneralPractice',
};

const LEVEL_KEYS: Record<TargetLevel, string> = {
  entry: 'Entry',
  mid: 'Mid',
  senior: 'Senior',
};

/** Shared look for the wizard's radio tiles. Selection is a border and a wash
 *  here, and a check icon at each call site. */
function optionCls(selected: boolean): string {
  return cn(
    'rounded-[16px] border-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
    selected
      ? 'border-[var(--accent)] bg-[var(--ui-info-wash)]'
      : 'border-border bg-card hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)]',
  );
}

/** Debounce so typing a role doesn't fire a request per keystroke.
 *
 * useEffect, not useMemo: useMemo's return value is the memoized result, not a
 * cleanup, so timers scheduled there are never cleared and every keystroke
 * leaks one that still fires.
 */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export default function Onboarding(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t, i18n } = useTranslation();
  const { user, updateUser } = useAuth();

  const { data: status } = useQuery({
    queryKey: ['onboarding-status'],
    queryFn: getOnboardingStatus,
    staleTime: 60_000,
  });

  const [step, setStep] = useState(1);
  const [fullName, setFullName] = useState('');
  const [goal, setGoal] = useState<OnboardingGoal | null>(null);
  const [role, setRole] = useState('');
  const [level, setLevel] = useState<TargetLevel>('entry');
  const [language, setLanguage] = useState('en');

  // Prefill from whatever we already know — but only while they have not
  // started answering. The guard has to be "has the user interacted", not "has
  // the query resolved": step 1's input is autofocused and typeable while
  // GET /users/me/onboarding is still in flight, and on a cold container that
  // response lands mid-keystroke and would overwrite what they typed.
  const seeded = useRef(false);
  const touched = useRef(false);
  const touch = useCallback(() => {
    touched.current = true;
  }, []);

  useEffect(() => {
    if (seeded.current || !status) return;
    seeded.current = true;
    if (touched.current) return;
    setFullName(status.full_name ?? user?.full_name ?? '');
    setGoal(status.goal);
    setRole(status.target_role ?? '');
    setLevel(status.target_level ?? 'entry');
    setLanguage(status.preferred_language || 'en');
  }, [status, user?.full_name]);

  const debouncedRole = useDebounced(role.trim(), 450);

  // Live "did we understand the job?" check on step 3.
  const { data: preview, isFetching: previewing } = useQuery({
    queryKey: ['practice-preview', debouncedRole, level],
    queryFn: () => previewPracticePlan(debouncedRole, level),
    enabled: step === 3 && debouncedRole.length >= 2,
    staleTime: 5 * 60_000,
  });

  // The handoff beat: null = still in the wizard, otherwise the saved plan
  // (or `false` if we could not read it back — the recap degrades, the
  // navigation does not).
  const [handoff, setHandoff] = useState<PracticePlan | false | null>(null);

  const goToDashboard = useCallback(
    (justOnboarded: boolean) => {
      void navigate('/dashboard', { replace: true, state: { justOnboarded } });
    },
    [navigate],
  );

  const finish = useMutation({
    mutationFn: () =>
      submitOnboarding({
        full_name: fullName.trim() || null,
        goal,
        target_role: role.trim(),
        target_level: level,
        preferred_language: language,
      }),
    onSuccess: async (saved) => {
      // The language answer is the one the wizard makes an explicit promise
      // about ("your AI interviewer will speak and listen in this language"),
      // so it has to land in the two places that can keep it. AppShell has
      // already seeded this key with 'en' by the time anyone reaches step 4,
      // which is why this overwrites rather than falls back.
      try {
        localStorage.setItem(INTERVIEW_LANGUAGE_KEY, language);
      } catch {
        // localStorage unavailable (private mode) — the picker on /start is
        // still there, they just start from the default.
      }
      // The other half: they have just told us which language they read.
      await i18n.changeLanguage(language);

      // Onboarding writes users.full_name server-side. Without this the
      // dashboard greets them by the name they just replaced — the ['auth','me']
      // query has a 5-minute staleTime and the shell's copy is only ever
      // written at login.
      const savedName = saved.full_name ?? (fullName.trim() || null);
      if (savedName) updateUser({ full_name: savedName });
      await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });

      await queryClient.invalidateQueries({ queryKey: ['onboarding-status'] });
      // Fetch — not invalidate — the plan before we leave. The dashboard's
      // centrepiece is this query; warming it here means the plan is on screen
      // the instant they arrive instead of popping in a beat later, which is
      // the whole difference between a handoff and a jump cut.
      //
      // staleTime: 0 is load-bearing. fetchQuery honours the cached staleTime,
      // and this same route is how someone CHANGES their role — without it a
      // re-run serves the previous role's plan back for a minute, in the recap
      // and on the dashboard both.
      const plan = await queryClient
        .fetchQuery({
          queryKey: ['practice-plan'],
          queryFn: getPracticePlan,
          staleTime: 0,
        })
        .catch(() => null);
      setHandoff(plan ?? false);
    },
    onError: () => toast.error(t('onboarding.saveFailed')),
  });

  const skip = useMutation({
    mutationFn: skipOnboarding,
    // onSuccess, not onSettled: leaving on a failed skip sends them to a
    // dashboard that still reads `seen: false` and bounces them straight back
    // here — a loop. Staying put with an error is the honest outcome.
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['onboarding-status'] });
      goToDashboard(false);
    },
    onError: () => toast.error(t('onboarding.skipFailed')),
  });

  // No timed hand-off: the recap stays until the person chooses. A change of
  // page they did not ask for, on a clock they cannot extend, is not a finish
  // line. The two buttons below are the only ways out.

  const canAdvance = useCallback((): boolean => {
    if (step === 3) return role.trim().length >= 2;
    return true; // name, goal and language all have sane defaults
  }, [step, role]);

  const busy = finish.isPending || skip.isPending;

  // HR/admin have their own consoles; "what job are you practising for?" is
  // nonsense for them. The server already refuses to build them a plan — this
  // stops them reaching the form by typing the URL and stamping a target_role
  // on their own account.
  if (status && !status.applicable) {
    return <Navigate to="/dashboard" replace />;
  }

  // ── Handoff: the wizard's payoff, shown in place before the dashboard ──────
  if (handoff !== null) {
    const plan = handoff === false ? null : handoff;
    const firstName = fullName.trim().split(' ')[0] ?? '';
    return (
      <Page width="form">
        <div className="max-w-[640px] space-y-6 rounded-card border border-border bg-card p-6 shadow-card sm:p-8">
          <div>
            <StatusTag tone="forest">{t('onboarding.readyTag')}</StatusTag>
            <h1 className="mt-3 text-[28px] font-bold leading-tight tracking-[-0.5px] text-foreground">
              {firstName
                ? t('onboarding.readyTitleNamed', { name: firstName })
                : t('onboarding.readyTitle')}
            </h1>
            <p className="mt-2 text-[15px] text-muted-foreground">
              {plan?.ready
                ? plan.domain_label
                  ? t('onboarding.readyPlanDomain', {
                      role: plan.target_role,
                      domain: plan.domain_label,
                    })
                  : t('onboarding.readyPlan', { role: plan.target_role })
                : t('onboarding.readySaved')}
            </p>
          </div>

          {plan?.ready && plan.competencies.length > 0 && (
            <div className="space-y-2 rounded-[16px] bg-[var(--ui-highlight-wash)] px-4 py-4">
              <p className="text-[13px] font-semibold text-foreground">
                {t('onboarding.readyCompetencies', {
                  count: plan.competencies.length,
                })}
              </p>
              <ul className="flex flex-wrap gap-2">
                {plan.competencies.map((c) => (
                  <li
                    key={c.id}
                    className="rounded-full border border-border bg-card px-3 py-1 text-[13px] text-foreground"
                  >
                    {c.name}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* The finish line: one primary, one way back. Nothing happens on a timer. */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Button
              type="button"
              size="lg"
              onClick={() => void navigate('/start', { replace: true })}
              className="w-full sm:w-auto"
            >
              {t('practice.startFirst')}
            </Button>
            <Button
              type="button"
              size="lg"
              variant="outline"
              onClick={() => goToDashboard(true)}
              className="w-full sm:w-auto"
            >
              {t('onboarding.goToDashboard')}
            </Button>
          </div>
        </div>
      </Page>
    );
  }

  const wizardSteps: Step[] = WIZARD_STEPS.map((s) => ({ key: s.key, label: t(s.labelKey) }));

  return (
    <Page width="form">
      <div className="max-w-[640px] space-y-7 rounded-card border border-border bg-card p-6 shadow-card sm:p-8">
        {/* Where they are in the four steps: the shared Stepper (state in words
            as well as shape), with Skip always one press away. */}
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3 text-[13px] text-muted-foreground">
            <Stepper
              steps={wizardSteps}
              current={String(step)}
              label={t('practice.stepsLabel')}
              compactLabel={t('onboarding.stepOf', { step, total: TOTAL_STEPS })}
              className="min-w-0 flex-1"
            />
            <button
              type="button"
              onClick={() => skip.mutate()}
              disabled={busy}
              className="inline-flex min-h-10 items-center rounded-full px-3 underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40"
            >
              {t('onboarding.skip')}
            </button>
          </div>
        </div>

        {step === 1 && (
          <div className="space-y-4">
            <div>
              <h1 id="onboarding-name-title" className="text-[24px] font-bold tracking-[-0.4px] text-foreground">
                {t('onboarding.nameTitle')}
              </h1>
              <p className="mt-1.5 text-[14.5px] text-muted-foreground">{t('onboarding.nameHint')}</p>
            </div>
            <Input
              autoFocus
              aria-labelledby="onboarding-name-title"
              value={fullName}
              onChange={(e) => {
                touch();
                setFullName(e.target.value);
              }}
              placeholder={t('onboarding.namePlaceholder')}
              maxLength={120}
              className="h-12 text-base"
            />
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <div>
              <h1 id="onboarding-goal-title" className="text-[24px] font-bold tracking-[-0.4px] text-foreground">
                {t('onboarding.goalTitle')}
              </h1>
              <p className="mt-1.5 text-[14.5px] text-muted-foreground">{t('onboarding.goalHint')}</p>
            </div>
            <div
              role="radiogroup"
              aria-labelledby="onboarding-goal-title"
              className="flex flex-col gap-2.5"
            >
              {GOAL_OPTIONS.map((g) => {
                const selected = goal === g.value;
                return (
                  <button
                    key={g.value}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => {
                      touch();
                      setGoal(g.value);
                    }}
                    className={cn(optionCls(selected), 'min-h-14 px-4 py-3 text-left')}
                  >
                    <div className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
                      {/* Selection is otherwise carried by a tint and a border
                          alone — no use to anyone who cannot see them. */}
                      {selected && (
                        <Check size={16} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
                      )}
                      {t(`onboarding.goal${GOAL_KEYS[g.value]}`, g.label)}
                    </div>
                    <div className="mt-0.5 text-[13px] text-muted-foreground">
                      {t(`onboarding.goal${GOAL_KEYS[g.value]}Hint`, g.hint)}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-5">
            <div>
              <h1 id="onboarding-role-title" className="text-[24px] font-bold tracking-[-0.4px] text-foreground">
                {t('onboarding.roleTitle')}
              </h1>
              <p className="mt-1.5 text-[14.5px] text-muted-foreground">{t('onboarding.roleHint')}</p>
            </div>
            <Input
              autoFocus
              aria-labelledby="onboarding-role-title"
              value={role}
              onChange={(e) => {
                touch();
                setRole(e.target.value);
              }}
              placeholder={t('onboarding.rolePlaceholder')}
              maxLength={200}
              className="h-12 text-base"
            />

            {/* Live confirmation that we understood the job. */}
            {debouncedRole.length >= 2 && (
              <div className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] px-4 py-3 text-sm" aria-live="polite">
                {previewing && (
                  <span className="text-muted-foreground">{t('onboarding.roleReading')}</span>
                )}
                {!previewing && preview?.ready && (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusTag tone="forest">{preview.domain_label}</StatusTag>
                      <span className="text-[13px] text-muted-foreground">
                        {t('onboarding.roleCompetencies', {
                          count: preview.competencies.length,
                        })}
                      </span>
                    </div>
                    <p className="text-[13px] text-[var(--ui-soft)]">{preview.what_this_person_does}</p>
                    <ul className="flex flex-wrap gap-1.5 pt-1">
                      {preview.competencies.map((c) => (
                        <li
                          key={c.id}
                          className="rounded-full border border-border bg-card px-2.5 py-0.5 text-[12px] text-foreground"
                        >
                          {c.name}
                        </li>
                      ))}
                    </ul>
                    <p className="pt-1 text-[12px] text-muted-foreground">
                      {t('onboarding.roleNotRight')}
                    </p>
                  </div>
                )}
              </div>
            )}

            <div>
              <div id="onboarding-level-title" className="mb-2 text-[14px] font-semibold text-foreground">
                {t('onboarding.levelTitle')}
              </div>
              <div
                role="radiogroup"
                aria-labelledby="onboarding-level-title"
                className="flex flex-wrap gap-2"
              >
                {LEVEL_OPTIONS.map((l) => {
                  const selected = level === l.value;
                  return (
                    <button
                      key={l.value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => {
                        touch();
                        setLevel(l.value);
                      }}
                      title={t(`onboarding.level${LEVEL_KEYS[l.value]}Hint`, l.hint)}
                      className={cn(optionCls(selected), 'flex min-h-11 items-center gap-1.5 px-4 text-[14px] font-medium')}
                    >
                      {selected && (
                        <Check size={15} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
                      )}
                      {t(`onboarding.level${LEVEL_KEYS[l.value]}`, l.label)}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="space-y-4">
            <div>
              <h1 id="onboarding-language-title" className="text-[24px] font-bold tracking-[-0.4px] text-foreground">
                {t('onboarding.languageTitle')}
              </h1>
              <p className="mt-1.5 text-[14.5px] text-muted-foreground">{t('onboarding.languageHint')}</p>
            </div>
            <div
              role="radiogroup"
              aria-labelledby="onboarding-language-title"
              className="flex flex-wrap gap-2.5"
            >
              {LANGUAGE_OPTIONS.map((l) => {
                const selected = language === l.value;
                return (
                  <button
                    key={l.value}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => {
                      touch();
                      setLanguage(l.value);
                    }}
                    className={cn(optionCls(selected), 'flex min-h-12 items-center gap-2 px-5 text-[15px] font-medium')}
                  >
                    {selected && (
                      <Check size={16} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
                    )}
                    {/* Endonyms — never translated, that is the point of them. */}
                    {l.label}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* nav */}
        <div className="flex items-center justify-between gap-3 pt-1">
          <Button
            type="button"
            variant="ghost"
            onClick={() => setStep((s) => Math.max(1, s - 1))}
            disabled={step === 1 || busy}
          >
            {t('onboarding.back')}
          </Button>

          {step < TOTAL_STEPS ? (
            <Button
              type="button"
              size="lg"
              onClick={() => setStep((s) => s + 1)}
              disabled={!canAdvance() || busy}
            >
              {t('onboarding.continue')}
            </Button>
          ) : (
            <Button
              type="button"
              size="lg"
              onClick={() => finish.mutate()}
              disabled={role.trim().length < 2 || busy}
            >
              {finish.isPending ? t('onboarding.saving') : t('onboarding.submit')}
            </Button>
          )}
        </div>

        {step === TOTAL_STEPS && role.trim().length < 2 && (
          <p role="alert" className="text-[13px] font-medium text-[var(--ui-warn)]">
            {t('onboarding.roleRequired')}
          </p>
        )}
      </div>
    </Page>
  );
}
