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

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  GOAL_OPTIONS,
  LANGUAGE_OPTIONS,
  LEVEL_OPTIONS,
  getOnboardingStatus,
  previewPracticePlan,
  skipOnboarding,
  submitOnboarding,
  type OnboardingGoal,
  type TargetLevel,
} from '@/api/onboarding';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Reveal } from '@/design/components/Reveal';
import { GlassCard, StatusTag } from '@/design/components/primitives';

const TOTAL_STEPS = 4;

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
  const { user } = useAuth();

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

  // Prefill once from whatever we already know, without clobbering typing.
  const [seeded, setSeeded] = useState(false);
  if (!seeded && status) {
    setFullName(status.full_name ?? user?.full_name ?? '');
    setGoal(status.goal);
    setRole(status.target_role ?? '');
    setLevel(status.target_level ?? 'entry');
    setLanguage(status.preferred_language || 'en');
    setSeeded(true);
  }

  const debouncedRole = useDebounced(role.trim(), 450);

  // Live "did we understand the job?" check on step 3.
  const { data: preview, isFetching: previewing } = useQuery({
    queryKey: ['practice-preview', debouncedRole, level],
    queryFn: () => previewPracticePlan(debouncedRole, level),
    enabled: step === 3 && debouncedRole.length >= 2,
    staleTime: 5 * 60_000,
  });

  const finish = useMutation({
    mutationFn: () =>
      submitOnboarding({
        full_name: fullName.trim() || null,
        goal,
        target_role: role.trim(),
        target_level: level,
        preferred_language: language,
      }),
    onSuccess: async () => {
      // Both the plan and the status are now stale; the dashboard reads them
      // immediately on arrival.
      await queryClient.invalidateQueries({ queryKey: ['onboarding-status'] });
      await queryClient.invalidateQueries({ queryKey: ['practice-plan'] });
      toast.success('Your practice plan is ready');
      void navigate('/dashboard', { replace: true });
    },
    onError: () => toast.error('Could not save. Please try again.'),
  });

  const skip = useMutation({
    mutationFn: skipOnboarding,
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['onboarding-status'] });
      void navigate('/dashboard', { replace: true });
    },
  });

  const canAdvance = useCallback((): boolean => {
    if (step === 3) return role.trim().length >= 2;
    return true; // name, goal and language all have sane defaults
  }, [step, role]);

  const busy = finish.isPending || skip.isPending;

  return (
    <div className="mx-auto flex min-h-[80vh] max-w-[720px] flex-col justify-center px-6 py-10">
      <Reveal>
        <GlassCard className="p-6 sm:p-8 space-y-6">
          {/* progress */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs opacity-60">
              <span>
                Step {step} of {TOTAL_STEPS}
              </span>
              <button
                type="button"
                onClick={() => skip.mutate()}
                disabled={busy}
                className="underline underline-offset-2 hover:opacity-100 disabled:opacity-40"
              >
                Skip for now
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
                <h1 className="text-xl font-semibold">What should we call you?</h1>
                <p className="mt-1 text-sm opacity-70">
                  Your interviewer will greet you by name.
                </p>
              </div>
              <input
                autoFocus
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Your name"
                maxLength={120}
                className="w-full rounded-lg bg-white/5 px-4 py-3 text-base outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div>
                <h1 className="text-xl font-semibold">What brings you here?</h1>
                <p className="mt-1 text-sm opacity-70">
                  So we can pitch the practice at the right thing.
                </p>
              </div>
              <div className="flex flex-col gap-2">
                {GOAL_OPTIONS.map((g) => (
                  <button
                    key={g.value}
                    type="button"
                    onClick={() => setGoal(g.value)}
                    className={cn(
                      'rounded-lg px-4 py-3 text-left transition',
                      goal === g.value
                        ? 'bg-[var(--accent)]/15 ring-1 ring-[var(--accent)]'
                        : 'bg-white/5 hover:bg-white/10',
                    )}
                  >
                    <div className="text-sm font-medium">{g.label}</div>
                    <div className="text-xs opacity-60">{g.hint}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <div>
                <h1 className="text-xl font-semibold">What role are you aiming for?</h1>
                <p className="mt-1 text-sm opacity-70">
                  Any job — welder, staff nurse, sales executive, developer. This decides
                  what your interviews ask about.
                </p>
              </div>
              <input
                autoFocus
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="e.g. CNC Machine Operator"
                maxLength={200}
                className="w-full rounded-lg bg-white/5 px-4 py-3 text-base outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />

              {/* Live confirmation that we understood the job. */}
              {debouncedRole.length >= 2 && (
                <div className="rounded-lg bg-white/5 px-4 py-3 text-sm">
                  {previewing && <span className="opacity-60">Reading the role…</span>}
                  {!previewing && preview?.ready && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <StatusTag tone="forest">{preview.domain_label}</StatusTag>
                        <span className="text-xs opacity-60">
                          {preview.competencies.length} competencies
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
                        Not right? Try naming the job more specifically.
                      </p>
                    </div>
                  )}
                </div>
              )}

              <div>
                <div className="mb-2 text-sm font-medium">How much experience?</div>
                <div className="flex flex-wrap gap-2">
                  {LEVEL_OPTIONS.map((l) => (
                    <button
                      key={l.value}
                      type="button"
                      onClick={() => setLevel(l.value)}
                      title={l.hint}
                      className={cn(
                        'rounded-lg px-3 py-2 text-sm transition',
                        level === l.value
                          ? 'bg-[var(--accent)]/15 ring-1 ring-[var(--accent)]'
                          : 'bg-white/5 hover:bg-white/10',
                      )}
                    >
                      {l.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-4">
              <div>
                <h1 className="text-xl font-semibold">Which language for your interview?</h1>
                <p className="mt-1 text-sm opacity-70">
                  Your AI interviewer will speak and listen in this language.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {LANGUAGE_OPTIONS.map((l) => (
                  <button
                    key={l.value}
                    type="button"
                    onClick={() => setLanguage(l.value)}
                    className={cn(
                      'rounded-lg px-4 py-3 text-sm transition',
                      language === l.value
                        ? 'bg-[var(--accent)]/15 ring-1 ring-[var(--accent)]'
                        : 'bg-white/5 hover:bg-white/10',
                    )}
                  >
                    {l.label}
                  </button>
                ))}
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
              Back
            </button>

            {step < TOTAL_STEPS ? (
              <button
                type="button"
                onClick={() => setStep((s) => s + 1)}
                disabled={!canAdvance() || busy}
                className="rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-40"
                style={{ background: 'var(--accent)', color: '#08131f' }}
              >
                Continue
              </button>
            ) : (
              <button
                type="button"
                onClick={() => finish.mutate()}
                disabled={role.trim().length < 2 || busy}
                className="rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-40"
                style={{ background: 'var(--accent)', color: '#08131f' }}
              >
                {finish.isPending ? 'Saving…' : 'Build my practice plan'}
              </button>
            )}
          </div>

          {step === TOTAL_STEPS && role.trim().length < 2 && (
            <p className="text-xs" style={{ color: '#ffb764' }}>
              Go back and tell us the role you&apos;re aiming for — it&apos;s the one thing
              we genuinely need.
            </p>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}
