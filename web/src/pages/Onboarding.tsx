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
import { Reveal } from '@/design/components/Reveal';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Check } from '@/design/components/icons';

const TOTAL_STEPS = 4;

/** Where every page that starts an interview reads the spoken language from.
 *  Mirrors the constant in StartInterview/JobsList/Interview/AppShell. */
const INTERVIEW_LANGUAGE_KEY = 'intants:interview-language';

/** How long the "your plan is ready" recap holds before the dashboard.
 *
 * Long enough to read three lines, short enough that it still reads as one
 * continuous motion rather than a second page. The CTA skips it, and the
 * dashboard shows the same plan in more detail, so nothing is lost either way.
 */
const HANDOFF_MS = 2600;

/** Translation keys for the wizard's option lists.
 *
 * Keyed off the API's option `value`, not its `label`: api/onboarding.ts is
 * the transport contract and its English label strings are the fallback for
 * anything the bundles have not caught up with. */
const GOAL_KEYS: Record<OnboardingGoal, string> = {
  campus_placement: 'campusPlacement',
  first_job: 'firstJob',
  switching_field: 'switchingField',
  interview_soon: 'interviewSoon',
  general_practice: 'generalPractice',
};

const LEVEL_KEYS: Record<TargetLevel, string> = {
  entry: 'Entry',
  mid: 'Mid',
  senior: 'Senior',
};

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

  // Auto-advance off the recap. Cleared on unmount so an early CTA click does
  // not fire a second navigate.
  useEffect(() => {
    if (handoff === null) return;
    const id = setTimeout(() => goToDashboard(true), HANDOFF_MS);
    return () => clearTimeout(id);
  }, [handoff, goToDashboard]);

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
      <div className="mx-auto flex min-h-[80vh] max-w-[720px] flex-col justify-center px-6 py-10">
        <Reveal>
          <GlassCard className="p-6 sm:p-8 space-y-5">
            <div>
              <StatusTag tone="forest">{t('onboarding.readyTag')}</StatusTag>
              <h1 className="mt-3 text-2xl font-semibold">
                {firstName
                  ? t('onboarding.readyTitleNamed', { name: firstName })
                  : t('onboarding.readyTitle')}
              </h1>
              <p className="mt-2 text-sm opacity-70">
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
              <div className="space-y-2 rounded-lg bg-white/5 px-4 py-3">
                <p className="text-xs opacity-60">
                  {t('onboarding.readyCompetencies', {
                    count: plan.competencies.length,
                  })}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {plan.competencies.map((c) => (
                    <span
                      key={c.id}
                      className="rounded-full bg-white/5 px-2 py-0.5 text-xs opacity-80"
                    >
                      {c.name}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <button
              type="button"
              onClick={() => goToDashboard(true)}
              className="w-full rounded-lg px-4 py-3 text-sm font-medium transition sm:w-auto"
              style={{ background: 'var(--accent)', color: '#08131f' }}
            >
              {t('onboarding.goToDashboard')}
            </button>
          </GlassCard>
        </Reveal>
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-[80vh] max-w-[720px] flex-col justify-center px-6 py-10">
      <Reveal>
        <GlassCard className="p-6 sm:p-8 space-y-6">
          {/* progress */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs opacity-60">
              <span>{t('onboarding.stepOf', { step, total: TOTAL_STEPS })}</span>
              <button
                type="button"
                onClick={() => skip.mutate()}
                disabled={busy}
                className="underline underline-offset-2 hover:opacity-100 disabled:opacity-40"
              >
                {t('onboarding.skip')}
              </button>
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${(step / TOTAL_STEPS) * 100}%`, background: 'var(--accent)' }}
              />
            </div>
          </div>

          {step === 1 && (
            <div className="space-y-4">
              <div>
                <h1 id="onboarding-name-title" className="text-xl font-semibold">
                  {t('onboarding.nameTitle')}
                </h1>
                <p className="mt-1 text-sm opacity-70">{t('onboarding.nameHint')}</p>
              </div>
              <input
                autoFocus
                aria-labelledby="onboarding-name-title"
                value={fullName}
                onChange={(e) => {
                  touch();
                  setFullName(e.target.value);
                }}
                placeholder={t('onboarding.namePlaceholder')}
                maxLength={120}
                className="w-full rounded-lg bg-white/5 px-4 py-3 text-base outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div>
                <h1 id="onboarding-goal-title" className="text-xl font-semibold">
                  {t('onboarding.goalTitle')}
                </h1>
                <p className="mt-1 text-sm opacity-70">{t('onboarding.goalHint')}</p>
              </div>
              <div
                role="radiogroup"
                aria-labelledby="onboarding-goal-title"
                className="flex flex-col gap-2"
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
                      className={cn(
                        'rounded-lg px-4 py-3 text-left transition',
                        selected
                          ? 'bg-[var(--accent)]/15 ring-1 ring-[var(--accent)]'
                          : 'bg-white/5 hover:bg-white/10',
                      )}
                    >
                      <div className="flex items-center gap-1.5 text-sm font-medium">
                        {/* Selection is otherwise carried by a tint and a ring
                            alone — no use to anyone who cannot see them. */}
                        {selected && (
                          <Check
                            size={14}
                            className="shrink-0 text-[var(--accent)]"
                            aria-hidden="true"
                          />
                        )}
                        {t(`onboarding.goal${GOAL_KEYS[g.value]}`, g.label)}
                      </div>
                      <div className="text-xs opacity-60">
                        {t(`onboarding.goal${GOAL_KEYS[g.value]}Hint`, g.hint)}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <div>
                <h1 id="onboarding-role-title" className="text-xl font-semibold">
                  {t('onboarding.roleTitle')}
                </h1>
                <p className="mt-1 text-sm opacity-70">{t('onboarding.roleHint')}</p>
              </div>
              <input
                autoFocus
                aria-labelledby="onboarding-role-title"
                value={role}
                onChange={(e) => {
                  touch();
                  setRole(e.target.value);
                }}
                placeholder={t('onboarding.rolePlaceholder')}
                maxLength={200}
                className="w-full rounded-lg bg-white/5 px-4 py-3 text-base outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />

              {/* Live confirmation that we understood the job. */}
              {debouncedRole.length >= 2 && (
                <div className="rounded-lg bg-white/5 px-4 py-3 text-sm">
                  {previewing && (
                    <span className="opacity-60">{t('onboarding.roleReading')}</span>
                  )}
                  {!previewing && preview?.ready && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <StatusTag tone="forest">{preview.domain_label}</StatusTag>
                        <span className="text-xs opacity-60">
                          {t('onboarding.roleCompetencies', {
                            count: preview.competencies.length,
                          })}
                        </span>
                      </div>
                      <p className="text-xs opacity-70">{preview.what_this_person_does}</p>
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {preview.competencies.map((c) => (
                          <span
                            key={c.id}
                            className="rounded-full bg-white/5 px-2 py-0.5 text-xs opacity-80"
                          >
                            {c.name}
                          </span>
                        ))}
                      </div>
                      <p className="pt-1 text-xs opacity-50">
                        {t('onboarding.roleNotRight')}
                      </p>
                    </div>
                  )}
                </div>
              )}

              <div>
                <div id="onboarding-level-title" className="mb-2 text-sm font-medium">
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
                        className={cn(
                          'flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition',
                          selected
                            ? 'bg-[var(--accent)]/15 ring-1 ring-[var(--accent)]'
                            : 'bg-white/5 hover:bg-white/10',
                        )}
                      >
                        {selected && (
                          <Check
                            size={14}
                            className="shrink-0 text-[var(--accent)]"
                            aria-hidden="true"
                          />
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
                <h1 id="onboarding-language-title" className="text-xl font-semibold">
                  {t('onboarding.languageTitle')}
                </h1>
                <p className="mt-1 text-sm opacity-70">{t('onboarding.languageHint')}</p>
              </div>
              <div
                role="radiogroup"
                aria-labelledby="onboarding-language-title"
                className="flex flex-wrap gap-2"
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
                      className={cn(
                        'flex items-center gap-1.5 rounded-lg px-4 py-3 text-sm transition',
                        selected
                          ? 'bg-[var(--accent)]/15 ring-1 ring-[var(--accent)]'
                          : 'bg-white/5 hover:bg-white/10',
                      )}
                    >
                      {selected && (
                        <Check
                          size={14}
                          className="shrink-0 text-[var(--accent)]"
                          aria-hidden="true"
                        />
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
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={() => setStep((s) => Math.max(1, s - 1))}
              disabled={step === 1 || busy}
              className="rounded-lg px-3 py-2 text-sm transition hover:bg-white/10 disabled:opacity-30"
            >
              {t('onboarding.back')}
            </button>

            {step < TOTAL_STEPS ? (
              <button
                type="button"
                onClick={() => setStep((s) => s + 1)}
                disabled={!canAdvance() || busy}
                className="rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-40"
                style={{ background: 'var(--accent)', color: '#08131f' }}
              >
                {t('onboarding.continue')}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => finish.mutate()}
                disabled={role.trim().length < 2 || busy}
                className="rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-40"
                style={{ background: 'var(--accent)', color: '#08131f' }}
              >
                {finish.isPending ? t('onboarding.saving') : t('onboarding.submit')}
              </button>
            )}
          </div>

          {step === TOTAL_STEPS && role.trim().length < 2 && (
            <p className="text-xs" style={{ color: '#ffb764' }}>
              {t('onboarding.roleRequired')}
            </p>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}
