// StartInterview — a calm setup flow for launching a self-serve interview.
//
// Layout: a Stepper (role -> interviewer -> language/level) above a 2-column
// grid (lg:grid-cols-[1fr_340px]).
//   LEFT  — steps 1–3 stacked: Role details, Choose interviewer, Preferences
//   RIGHT — sticky "ready to begin" card: device checklist + consent note + submit CTA
// All three steps stay on one page (every field is reachable at once and the
// form still submits in one press); the Stepper tracks which one is being
// worked on, and marks the role step done only once a title is entered.
//
// Resume banner spans full width above the columns.
// AppShell already provides the top bar — no second shell here.

import { useState, useCallback, useEffect, useId, useRef, type FormEvent, type ReactNode } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  CheckCircle2,
  AlertCircle,
  Mic,
  XCircle,
  Loader2,
  Wifi,
  Globe,
  ShieldCheck,
  ArrowRight,
  Check,
} from '@/design/components/icons';
import { useAuth } from '@/context/AuthContext';
import { useConsent } from '@/context/ConsentContext';
import { getMe } from '@/api/auth';
import { createCustomJob } from '@/api/jobs';
import { createSession } from '@/api/sessions';
import { getAvatars } from '@/api/avatars';
import { toast } from '@/lib/toast';
import { useCandidateError } from '@/lib/candidateCopy';
import { cn } from '@/lib/utils';
import ConsentModal from '@/components/ConsentModal';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { Page, PageHeader, SectionCard, Stepper } from '@/design/components/page';
import { StatusTag, WaveBars } from '@/design/components/primitives';
import { initialsOf } from '@/design/data/shared';
import type { Language, Avatar as AvatarType } from '@/types/interview';
import { readPref, writePref } from '@/lib/localPref';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LANGUAGE_STORAGE_KEY = 'intants:interview-language';
const AVATAR_STORAGE_KEY = 'intants:interview-avatar';
const DEFAULT_AVATAR_ID = 'anna';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ExperienceLevel = 'entry' | 'mid' | 'senior';

interface FormValues {
  title: string;
  company_name: string;
  jd_text: string;
  level: ExperienceLevel;
}

// ---------------------------------------------------------------------------
// Shared input / textarea class
// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// Step number — a neutral outlined numeral (primary is reserved for the action)
// ---------------------------------------------------------------------------

type StepKey = 'role' | 'interviewer' | 'preferences';

function StepBadge({ step }: { step: number }) {
  return (
    <span
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--ui-line-strong)] bg-[var(--ui-inset)] text-[13px] font-bold text-foreground"
      aria-hidden="true"
    >
      {step}
    </span>
  );
}

function StepCard({
  step,
  title,
  description,
  onActivate,
  children,
}: {
  step: number;
  title: string;
  description: string;
  /** Called when focus or a press lands inside the card, so the Stepper follows. */
  onActivate: () => void;
  children: ReactNode;
}) {
  return (
    <div onFocusCapture={onActivate} onPointerDownCapture={onActivate}>
      <SectionCard
        title={
          <span className="flex items-center gap-3">
            <StepBadge step={step} />
            <span>{title}</span>
          </span>
        }
        description={description}
      >
        <div className="space-y-4">{children}</div>
      </SectionCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Labeled field wrapper
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Choice pills — native radios, so arrow keys and Space work for free. The
// selected pill is filled AND carries a check, so selection never rests on
// colour alone.
// ---------------------------------------------------------------------------

interface ChoicePillsProps<T extends string> {
  name: string;
  label: string;
  options: { key: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
}

function ChoicePills<T extends string>({ name, label, options, value, onChange }: ChoicePillsProps<T>) {
  const labelId = useId();
  return (
    <div className="space-y-2">
      <p id={labelId} className="text-[13px] font-semibold text-foreground">
        {label}
      </p>
      <div role="radiogroup" aria-labelledby={labelId} className="flex flex-wrap gap-2">
        {options.map((opt) => {
          const on = opt.key === value;
          return (
            <label
              key={opt.key}
              className={cn(
                'relative inline-flex min-h-[44px] cursor-pointer items-center gap-1.5 rounded-full border px-5 text-[14px] font-semibold transition-colors',
                'focus-within:ring-2 focus-within:ring-[var(--accent)] focus-within:ring-offset-2 focus-within:ring-offset-background',
                on
                  ? 'border-primary bg-highlight text-highlight-foreground'
                  : 'border-input bg-card text-foreground hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)]',
              )}
            >
              <input
                type="radio"
                name={name}
                value={opt.key}
                checked={on}
                onChange={() => onChange(opt.key)}
                className="sr-only"
              />
              {on ? <Check size={14} aria-hidden="true" /> : null}
              {opt.label}
            </label>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Avatar card
// ---------------------------------------------------------------------------

interface AvatarCardProps {
  avatar: AvatarType;
  selected: boolean;
  onSelect: (id: string) => void;
}

function AvatarCard({ avatar, selected, onSelect }: AvatarCardProps) {
  const { t } = useTranslation();
  const initials = initialsOf(avatar.name);

  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={t('startInterview.selectAvatar', { name: avatar.name })}
      onClick={() => onSelect(avatar.id)}
      className={cn(
        'relative flex w-28 cursor-pointer flex-col items-center gap-2 rounded-[16px] border-2 p-4 transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        selected
          ? 'border-primary bg-[var(--ui-highlight-wash)]'
          : 'border-border bg-card hover:border-[var(--ui-line-strong)]',
      )}
    >
      <span className="relative">
        <span className="relative block h-16 w-16 overflow-hidden rounded-full bg-[var(--ui-inset-strong)]">
          {/* Initials sit underneath: they show until the thumbnail paints and
              stay if it fails to load. */}
          <span
            className="absolute inset-0 flex items-center justify-center text-[15px] font-bold text-foreground"
            aria-hidden="true"
          >
            {initials}
          </span>
          <video
            src={avatar.thumbnail_url}
            autoPlay
            muted
            loop
            playsInline
            aria-hidden="true"
            className="absolute inset-0 h-full w-full rounded-full object-cover"
            onError={(e) => {
              (e.currentTarget as HTMLVideoElement).style.display = 'none';
            }}
          />
        </span>

        {selected && (
          <span className="absolute -bottom-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full bg-foreground text-background">
            <Check size={13} aria-hidden="true" />
          </span>
        )}
      </span>

      <div className="text-[14px] font-semibold text-foreground">{avatar.name}</div>
      <div className="text-[12px] text-muted-foreground">
        {avatar.gender === 'female' ? t('startInterview.genderFemale') : t('startInterview.genderMale')}
      </div>
      {selected ? <span className="sr-only">{t('startInterview.selected')}</span> : null}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Device check row — presentation only
// ---------------------------------------------------------------------------

// The live interview needs a microphone (hooks/useLiveKitInterview.ts enables
// it on connect). The camera is optional and is requested on the interview page
// only after its own consent step, so this check never asks for it.

type CheckState = 'idle' | 'running' | 'ok' | 'fail' | 'skipped';

interface DeviceRow {
  state: CheckState;
  /** i18n key of the specific result; null before the check has run. */
  detailKey: string | null;
}

const IDLE_ROW: DeviceRow = { state: 'idle', detailKey: null };

interface DeviceCheckResult {
  mic: DeviceRow;
  connection: DeviceRow;
  browser: DeviceRow;
}

const IDLE_RESULT: DeviceCheckResult = { mic: IDLE_ROW, connection: IDLE_ROW, browser: IDLE_ROW };

function micFailureKey(err: unknown): string {
  const name = err instanceof DOMException || err instanceof Error ? err.name : '';
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
    return 'startInterview.devMicDenied';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
    return 'startInterview.devMicNone';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
    return 'startInterview.devMicBusy';
  }
  return 'startInterview.devMicFailed';
}

/** Stops every track a stream opened, so the check never leaves the mic live. */
function stopStream(stream: MediaStream): void {
  stream.getTracks().forEach((track) => track.stop());
}

function useDeviceCheck(): { result: DeviceCheckResult; running: boolean; run: () => void } {
  const [result, setResult] = useState<DeviceCheckResult>(IDLE_RESULT);
  const [running, setRunning] = useState(false);
  const mountedRef = useRef(true);
  const runningRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const run = useCallback(() => {
    // A second click while a request is pending would stack permission prompts.
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);

    // Only what can be tested truthfully: onLine says the device has a network
    // interface up, nothing about the quality of the connection.
    const online = typeof navigator.onLine === 'boolean' ? navigator.onLine : true;
    const connection: DeviceRow = online
      ? { state: 'ok', detailKey: 'startInterview.devOnline' }
      : { state: 'fail', detailKey: 'startInterview.devOffline' };

    const insecure = typeof window !== 'undefined' && window.isSecureContext === false;
    const hasGetUserMedia = typeof navigator.mediaDevices?.getUserMedia === 'function';
    const hasWebRtc = typeof RTCPeerConnection !== 'undefined';

    const finish = (next: DeviceCheckResult) => {
      runningRef.current = false;
      if (!mountedRef.current) return;
      setResult(next);
      setRunning(false);
    };

    if (insecure || !hasGetUserMedia || !hasWebRtc) {
      finish({
        connection,
        browser: {
          state: 'fail',
          detailKey: insecure ? 'startInterview.devBrowserInsecure' : 'startInterview.devBrowserUnsupported',
        },
        mic: { state: 'skipped', detailKey: 'startInterview.devMicSkipped' },
      });
      return;
    }

    const browser: DeviceRow = { state: 'ok', detailKey: 'startInterview.devBrowserOk' };
    setResult({
      connection,
      browser,
      mic: { state: 'running', detailKey: null },
    });

    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        // Release the microphone immediately, even if the page has gone.
        stopStream(stream);
        finish({ connection, browser, mic: { state: 'ok', detailKey: 'startInterview.devPassed' } });
      })
      .catch((err: unknown) => {
        finish({ connection, browser, mic: { state: 'fail', detailKey: micFailureKey(err) } });
      });
  }, []);

  return { result, running, run };
}

interface DeviceCheckRowProps {
  icon: ReactNode;
  label: string;
  row: DeviceRow;
}

function DeviceCheckRow({ icon, label, row }: DeviceCheckRowProps) {
  const { t } = useTranslation();
  const failed = row.state === 'fail';
  const passed = row.state === 'ok';
  const unchecked = row.state === 'idle' || row.state === 'skipped';
  const statusText =
    row.state === 'idle'
      ? t('startInterview.devNotChecked')
      : row.state === 'running'
        ? t('startInterview.devChecking')
        : failed
          ? t('startInterview.devFailed')
          : row.state === 'skipped'
            ? t('startInterview.devNotChecked')
            : t('startInterview.devPassed');
  return (
    <li
      className={cn(
        'border-b border-border py-2.5 last:border-0',
        failed && 'rounded-[10px] border border-[var(--ui-danger)] bg-[var(--ui-danger-wash)] px-2.5 my-1 last:border',
      )}
    >
      <div className="flex items-center gap-3">
        <span className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-[var(--ui-inset)] text-muted-foreground">
          {icon}
        </span>
        <span className="flex-1 text-[14px] text-foreground">{label}</span>
        <span
          className={cn(
            'inline-flex items-center gap-1 text-[12px] font-semibold',
            passed ? 'text-[var(--ui-ok)]' : failed ? 'text-[var(--ui-danger)]' : 'text-muted-foreground',
          )}
        >
          {passed ? <CheckCircle2 size={14} aria-hidden="true" /> : null}
          {failed ? <XCircle size={14} aria-hidden="true" /> : null}
          {unchecked ? <span aria-hidden="true" className="w-3.5 text-center">-</span> : null}
          {row.state === 'running' ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
          {statusText}
        </span>
      </div>
      {row.detailKey && row.state !== 'running' && !(passed && row.detailKey === 'startInterview.devPassed') ? (
        <p
          className={cn(
            'mt-1.5 pl-11 text-[13px] leading-relaxed',
            failed ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          {t(row.detailKey)}
        </p>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function StartInterview() {
  const { t } = useTranslation();
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const { consented, loading: consentLoading, recordConsent } = useConsent();
  const deviceCheck = useDeviceCheck();
  const errorText = useCandidateError();

  // ── Language options ────────────────────────────────────────────────────────
  const LANG_TABS: { key: Language; label: string }[] = [
    { key: 'en', label: 'English' },
    { key: 'hi', label: 'हिन्दी' },
    { key: 'te', label: 'తెలుగు' },
  ];

  const LEVEL_TABS: {
    key: ExperienceLevel;
    label: string;
    description: string;
  }[] = [
    { key: 'entry', label: t('startInterview.entryLevel'), description: t('startInterview.years03') },
    { key: 'mid', label: t('startInterview.midLevel'), description: t('startInterview.years36') },
    { key: 'senior', label: t('startInterview.seniorLevel'), description: t('startInterview.years6plus') },
  ];

  // ── Language (persisted) ────────────────────────────────────────────────────
  const [selectedLanguage, setSelectedLanguage] = useState<Language>(() => {
    const stored = readPref(LANGUAGE_STORAGE_KEY);
    if (stored === 'en' || stored === 'hi' || stored === 'te') return stored;
    return 'en';
  });

  useEffect(() => {
    writePref(LANGUAGE_STORAGE_KEY, selectedLanguage);
  }, [selectedLanguage]);

  // ── Avatar selection (persisted) ────────────────────────────────────────────
  const [selectedAvatarId, setSelectedAvatarId] = useState<string>(() => {
    const stored = readPref(AVATAR_STORAGE_KEY);
    return stored ?? DEFAULT_AVATAR_ID;
  });

  useEffect(() => {
    writePref(AVATAR_STORAGE_KEY, selectedAvatarId);
  }, [selectedAvatarId]);

  // ── Stepper position (presentation only) ────────────────────────────────────
  const [activeStep, setActiveStep] = useState<StepKey>('role');

  // ── Form state ──────────────────────────────────────────────────────────────
  const [values, setValues] = useState<FormValues>({
    title: '',
    company_name: '',
    jd_text: '',
    level: 'entry',
  });
  const [titleError, setTitleError] = useState<string | null>(null);

  // ── Consent modal state ─────────────────────────────────────────────────────
  const [showConsent, setShowConsent] = useState(false);
  const [isSubmittingConsent, setIsSubmittingConsent] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);

  // ── Resume status (getMe) ───────────────────────────────────────────────────
  const { data: me } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => {
      if (!accessToken) throw new Error('No access token');
      return getMe(accessToken);
    },
    enabled: accessToken !== null,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  // ── Avatars list ─────────────────────────────────────────────────────────────
  const {
    data: avatars,
    isLoading: avatarsLoading,
    isError: avatarsError,
  } = useQuery<AvatarType[]>({
    queryKey: ['avatars'],
    queryFn: getAvatars,
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

  // ── Mutations ───────────────────────────────────────────────────────────────
  const createJobMutation = useMutation({
    mutationFn: (formVals: FormValues) => {
      if (!accessToken) return Promise.reject(new Error('No access token'));
      return createCustomJob(
        {
          title: formVals.title.trim(),
          ...(formVals.company_name.trim()
            ? { company_name: formVals.company_name.trim() }
            : {}),
          ...(formVals.jd_text.trim() ? { jd_text: formVals.jd_text.trim() } : {}),
          level: formVals.level,
        },
        accessToken,
      );
    },
  });

  const createSessionMutation = useMutation({
    mutationFn: (jobId: string) => {
      if (!accessToken) return Promise.reject(new Error('No access token'));
      return createSession(
        {
          job_id: jobId,
          language: selectedLanguage,
          avatar_id: selectedAvatarId,
        },
        accessToken,
      );
    },
    onSuccess: (result) => {
      void navigate(`/interview/${result.session_id}`);
    },
  });

  // The practice job made for these exact form values. A retry after the
  // session failed to start reuses it instead of creating a duplicate job
  // (audit CA-L5); any edit to the form makes a new one.
  const createdJobRef = useRef<{ key: string; id: string } | null>(null);

  const proceedToSession = useCallback(
    async (formVals: FormValues) => {
      const key = JSON.stringify([
        formVals.title.trim(),
        formVals.company_name.trim(),
        formVals.jd_text.trim(),
        formVals.level,
      ]);
      let jobId = createdJobRef.current?.key === key ? createdJobRef.current.id : null;
      if (!jobId) {
        const created = await createJobMutation.mutateAsync(formVals);
        createdJobRef.current = { key, id: created.id };
        jobId = created.id;
      }
      createSessionMutation.mutate(jobId);
    },
    [createJobMutation, createSessionMutation],
  );

  const failure = createJobMutation.error ?? createSessionMutation.error;
  const apiError = failure ? errorText(failure, t('jobs.startError')) : null;

  useEffect(() => {
    if (apiError) {
      toast.error(apiError);
    }
  }, [apiError]);

  const handleSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      setTitleError(null);
      createJobMutation.reset();
      createSessionMutation.reset();

      if (!values.title.trim()) {
        setTitleError(t('startInterview.jobTitleRequired'));
        return;
      }

      if (consented === true) {
        proceedToSession(values).catch(() => {
          // Error surfaced via mutation state and toast
        });
      } else {
        setConsentError(null);
        setShowConsent(true);
      }
    },
    [values, consented, proceedToSession, createJobMutation, createSessionMutation, t],
  );

  const handleAgree = useCallback(async () => {
    setIsSubmittingConsent(true);
    setConsentError(null);
    try {
      await recordConsent();
      setShowConsent(false);
      await proceedToSession(values);
    } catch (err) {
      setConsentError(errorText(err, t('jobs.consentError')));
    } finally {
      setIsSubmittingConsent(false);
    }
  }, [recordConsent, proceedToSession, values, t, errorText]);

  const handleDecline = useCallback(() => {
    setShowConsent(false);
    setConsentError(null);
    toast.info(t('jobs.declineBanner'));
  }, [t]);

  const isSubmitting = createJobMutation.isPending || createSessionMutation.isPending;

  // ── Derived summary values ──────────────────────────────────────────────────
  const levelLabel =
    LEVEL_TABS.find((o) => o.key === values.level)?.label ?? values.level;
  const languageLabel =
    LANG_TABS.find((o) => o.key === selectedLanguage)?.label ?? selectedLanguage;
  const avatarLabel =
    avatars?.find((a) => a.id === selectedAvatarId)?.name ?? selectedAvatarId;

  // ── Avatar groups ─────────────────────────────────────────────────────────
  const maleAvatars = avatars?.filter((a) => a.gender === 'male') ?? [];
  const femaleAvatars = avatars?.filter((a) => a.gender === 'female') ?? [];

  const avatarGroups = [
    { label: t('startInterview.maleGroup'), items: maleAvatars },
    { label: t('startInterview.femaleGroup'), items: femaleAvatars },
  ];

  // The role step is done only once it has a title; until then the Stepper
  // stays on it whatever else is touched. After that it follows the person.
  const stepperCurrent: StepKey = !values.title.trim()
    ? 'role'
    : activeStep === 'role'
      ? 'interviewer'
      : activeStep;

  return (
    <Page width="default">
      <PageHeader
        title={t('startInterview.pageTitle')}
        description={t('startInterview.pageDesc')}
      />

      <Stepper
        label={t('startInterview.pageTitle')}
        current={stepperCurrent}
        steps={[
          { key: 'role', label: t('startInterview.step1Title'), hint: t('startInterview.step1Desc') },
          { key: 'interviewer', label: t('startInterview.step2Title'), hint: t('startInterview.step2Desc') },
          { key: 'preferences', label: t('startInterview.step3Title'), hint: t('startInterview.step3Desc') },
        ]}
      />

      {/* ── Resume status banner — full width ────────────────────────────── */}
      {!consentLoading && me !== undefined && (
        <div>
          {me.has_resume ? (
            <div
              aria-live="polite"
              className="flex items-center gap-2 rounded-[14px] bg-[var(--ui-ok-wash)] px-4 py-3 text-[14px]"
            >
              <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--ui-ok)]" aria-hidden="true" />
              <span className="font-semibold text-foreground">{t('startInterview.resumeOnFile')}</span>
              <span className="text-muted-foreground">{t('startInterview.resumeOnFileDesc')}</span>
            </div>
          ) : (
            <div
              aria-live="polite"
              className="flex items-start gap-2 rounded-[14px] bg-[var(--ui-warn-wash)] px-4 py-3 text-[14px]"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
              <span className="text-foreground">
                {t('startInterview.noResume')}{' '}
                <Link
                  to="/dashboard"
                  className="rounded font-semibold text-[var(--ui-info)] underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  {t('startInterview.noResumeLink')}
                </Link>{' '}
                {t('startInterview.noResumeDesc')}
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── 2-column form layout ──────────────────────────────────────────── */}
      <form onSubmit={handleSubmit} noValidate aria-label={t('startInterview.formAria')}>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px] lg:items-start">
          {/* ── LEFT COLUMN: Steps 1–3 ─────────────────────────────────── */}
          <div className="space-y-6">
            {/* Step 1: Role details */}
            <StepCard
              step={1}
              onActivate={() => setActiveStep('role')}
              title={t('startInterview.step1Title')}
              description={t('startInterview.step1Desc')}
            >
              <FormField
                id="si-title"
                label={
                  <>
                    {t('startInterview.jobTitleLabel')}{' '}
                    <span aria-hidden="true" className="text-[var(--ui-danger)]">*</span>
                  </>
                }
                error={titleError ?? undefined}
              >
                {(p) => (
                  <Input
                    {...p}
                    type="text"
                    required
                    aria-required="true"
                    aria-invalid={titleError !== null}
                    value={values.title}
                    onChange={(e) => {
                      setValues((v) => ({ ...v, title: e.target.value }));
                      if (titleError) setTitleError(null);
                    }}
                    placeholder={t('startInterview.jobTitlePlaceholder')}
                  />
                )}
              </FormField>

              <FormField
                id="si-company"
                label={
                  <>
                    {t('startInterview.companyLabel')}{' '}
                    <span className="text-[12px] font-normal text-muted-foreground">
                      ({t('startInterview.optional')})
                    </span>
                  </>
                }
              >
                {(p) => (
                  <Input
                    {...p}
                    type="text"
                    value={values.company_name}
                    onChange={(e) => setValues((v) => ({ ...v, company_name: e.target.value }))}
                    placeholder={t('startInterview.companyPlaceholder')}
                  />
                )}
              </FormField>

              <FormField
                id="si-jd"
                label={
                  <>
                    {t('startInterview.jdLabel')}{' '}
                    <span className="text-[12px] font-normal text-muted-foreground">
                      ({t('startInterview.optional')})
                    </span>
                  </>
                }
              >
                {(p) => (
                  <Textarea
                    {...p}
                    rows={4}
                    value={values.jd_text}
                    onChange={(e) => setValues((v) => ({ ...v, jd_text: e.target.value }))}
                    placeholder={t('startInterview.jdPlaceholder')}
                    className="min-h-[96px]"
                  />
                )}
              </FormField>
            </StepCard>

            {/* Step 2: Avatar picker */}
            <StepCard
              step={2}
              onActivate={() => setActiveStep('interviewer')}
              title={t('startInterview.step2Title')}
              description={t('startInterview.step2Desc')}
            >
              {avatarsLoading && (
                <div
                  aria-live="polite"
                  aria-label={t('startInterview.loadingInterviewers')}
                  className="flex flex-wrap gap-3"
                >
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-36 w-28 rounded-[16px]" />
                  ))}
                </div>
              )}

              {!avatarsLoading && avatarsError && (
                <div
                  aria-live="polite"
                  className="rounded-[12px] bg-[var(--ui-warn-wash)] px-4 py-3 text-[14px] text-foreground"
                >
                  {t('startInterview.avatarLoadError')}
                </div>
              )}

              {!avatarsLoading && !avatarsError && avatars !== undefined && (
                <div className="space-y-5">
                  {avatarGroups
                    .filter((g) => g.items.length > 0)
                    .map((group) => (
                      <div key={group.label}>
                        <p className="mb-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--ui-soft)]">
                          {group.label}
                        </p>
                        <div className="flex flex-wrap gap-3" role="radiogroup" aria-label={group.label}>
                          {group.items.map((avatar) => (
                            <AvatarCard
                              key={avatar.id}
                              avatar={avatar}
                              selected={selectedAvatarId === avatar.id}
                              onSelect={setSelectedAvatarId}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                </div>
              )}
            </StepCard>

            {/* Step 3: Interview language + level */}
            <StepCard
              step={3}
              onActivate={() => setActiveStep('preferences')}
              title={t('startInterview.step3Title')}
              description={t('startInterview.step3Desc')}
            >
              <div className="space-y-2">
                <ChoicePills
                  name="si-language"
                  label={t('startInterview.interviewLanguageLabel')}
                  options={LANG_TABS}
                  value={selectedLanguage}
                  onChange={setSelectedLanguage}
                />
                <p className="text-[13px] text-muted-foreground">
                  {t('startInterview.switchLanguageNote')}
                </p>
              </div>

              <div className="space-y-2">
                <ChoicePills
                  name="si-level"
                  label={t('startInterview.experienceLevelLabel')}
                  options={LEVEL_TABS}
                  value={values.level}
                  onChange={(k) => setValues((v) => ({ ...v, level: k }))}
                />
                <p className="text-[13px] text-muted-foreground">
                  {LEVEL_TABS.find((l) => l.key === values.level)?.description}
                </p>
              </div>
            </StepCard>
          </div>

          {/* ── RIGHT COLUMN: Device check + consent + submit (sticky) ──── */}
          <div className="lg:sticky lg:top-20">
            <section className="space-y-4 rounded-card border border-border bg-card p-6 shadow-card">
              <div>
                <h2 className="text-[17px] font-bold tracking-[-0.2px] text-foreground">
                  {t('startInterview.deviceTitle')}
                </h2>
                <p className="mt-1 text-[13.5px] text-muted-foreground">
                  {t('startInterview.deviceDesc')}
                </p>
              </div>

              <div className="flex items-center justify-center rounded-[12px] bg-[var(--ui-inset-soft)] py-4">
                <WaveBars active={false} bars={20} color="var(--accent)" height={32} />
              </div>

              {/* Device check — optional; the interview page asks for the mic itself */}
              <Button
                type="button"
                variant="outline"
                className="w-full"
                disabled={deviceCheck.running}
                aria-busy={deviceCheck.running}
                onClick={deviceCheck.run}
              >
                <Mic aria-hidden="true" />
                {deviceCheck.running ? t('startInterview.devChecking') : t('startInterview.runDeviceCheck')}
              </Button>

              {/* Checklist — results are announced as they land */}
              <ul aria-live="polite" aria-label={t('startInterview.deviceTitle')}>
                <DeviceCheckRow
                  icon={<Mic size={15} aria-hidden="true" />}
                  label={t('startInterview.devMic')}
                  row={deviceCheck.result.mic}
                />
                <DeviceCheckRow
                  icon={<Wifi size={15} aria-hidden="true" />}
                  label={t('startInterview.devConnection')}
                  row={deviceCheck.result.connection}
                />
                <DeviceCheckRow
                  icon={<Globe size={15} aria-hidden="true" />}
                  label={t('startInterview.devBrowser')}
                  row={deviceCheck.result.browser}
                />
              </ul>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                {t('startInterview.devCameraNote')}
              </p>

              {/* DPDP consent note */}
              <div className="flex items-start gap-2 rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-3">
                <ShieldCheck
                  size={16}
                  className="mt-0.5 shrink-0 text-[var(--ui-ok)]"
                  aria-hidden="true"
                />
                <p className="text-[13px] leading-relaxed text-muted-foreground">
                  {t('startInterview.consentNote')}
                </p>
              </div>

              {/* Review summary — visible once title is entered */}
              {values.title.trim() && (
                <div className="space-y-2 rounded-[12px] border border-border bg-card px-4 py-3 text-[14px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-foreground">{values.title.trim()}</span>
                    {values.company_name.trim() && (
                      <>
                        <span className="text-muted-foreground">{t('startInterview.at')}</span>
                        <span className="text-[var(--ui-soft)]">{values.company_name.trim()}</span>
                      </>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusTag tone="electric">{levelLabel}</StatusTag>
                    <StatusTag tone="lavender">{languageLabel}</StatusTag>
                    <StatusTag tone="neutral">{avatarLabel}</StatusTag>
                    {values.jd_text.trim() && (
                      <StatusTag tone="forest">{t('startInterview.jdProvided')}</StatusTag>
                    )}
                  </div>
                </div>
              )}

              {/* API error */}
              {apiError && (
                <div
                  role="alert"
                  className="rounded-[12px] bg-[var(--ui-danger-wash)] px-4 py-3 text-[14px] text-[var(--ui-danger)]"
                >
                  {apiError}
                </div>
              )}

              {/* Submit CTA */}
              <Button type="submit" size="lg" disabled={isSubmitting} aria-busy={isSubmitting} className="w-full">
                {isSubmitting ? (
                  <span className="flex items-center gap-2">
                    <span
                      className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
                      aria-hidden="true"
                    />
                    {t('startInterview.starting')}
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    {consented ? t('startInterview.startButton') : t('startInterview.acceptToBegin')}
                    <ArrowRight size={16} aria-hidden="true" />
                  </span>
                )}
              </Button>

              {/* Ready status indicator */}
              <div className="flex justify-center">
                {!values.title.trim() ? (
                  <StatusTag tone="amber">{t('startInterview.fillRoleFirst')}</StatusTag>
                ) : (
                  <StatusTag tone="forest" dot>
                    {t('startInterview.readyTag')}
                  </StatusTag>
                )}
              </div>
            </section>
          </div>
        </div>
      </form>

      {/* ── DPDP Consent modal ──────────────────────────────────────────────── */}
      {showConsent && (
        <ConsentModal
          onAgree={handleAgree}
          onDecline={handleDecline}
          isSubmitting={isSubmittingConsent}
          error={consentError}
        />
      )}
    </Page>
  );
}
