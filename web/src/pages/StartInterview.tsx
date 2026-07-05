// StartInterview — multi-step wizard for launching a self-serve interview.
//
// Layout: 2-column (lg:grid-cols-[1fr_340px]).
//   LEFT  — steps 1–3 stacked: Role details, Choose interviewer, Interview language
//   RIGHT — sticky device-check GlassCard with WaveBars + consent gate + submit CTA
//
// Resume banner spans full width above the columns.
// AppShell already provides the top bar — no second shell here.

import { useState, useCallback, useEffect } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle2,
  AlertCircle,
  Mic,
  Camera,
  Wifi,
  Globe,
  ShieldCheck,
  ArrowRight,
  Sparkles,
  Check,
} from '@/design/components/icons';
import { useAuth } from '@/context/AuthContext';
import { useConsent } from '@/context/ConsentContext';
import { getMe } from '@/api/auth';
import { createCustomJob } from '@/api/jobs';
import { createSession } from '@/api/sessions';
import { getAvatars } from '@/api/avatars';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import ConsentModal from '@/components/ConsentModal';
import {
  GlassCard,
  Pill,
  SegTabs,
  StatusTag,
  WaveBars,
} from '@/design/components/primitives';
import { gradientFor, initialsOf } from '@/design/data/shared';
import { staggerParent, staggerChild } from '@/design/lib/motion';
import type { Language, Avatar as AvatarType } from '@/types/interview';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LANGUAGE_STORAGE_KEY = 'intants:interview-language';
const AVATAR_STORAGE_KEY = 'intants:interview-avatar';
const DEFAULT_AVATAR_ID = 'anna';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FormValues {
  title: string;
  company_name: string;
  jd_text: string;
  level: 'entry' | 'mid' | 'senior';
}

// ---------------------------------------------------------------------------
// Shared input / select / textarea class (design dark surface)
// ---------------------------------------------------------------------------

const inputCls =
  'w-full rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3.5 py-3 ' +
  'text-[14px] text-white placeholder:text-[#5a5f66] ' +
  'focus:outline-none focus:border-[var(--accent)] focus:ring-0 ' +
  'transition-colors resize-none';

const inputErrCls = 'border-[rgba(230,113,79,0.5)] bg-[rgba(230,113,79,0.06)]';

// ---------------------------------------------------------------------------
// Section header
// ---------------------------------------------------------------------------

interface SectionHeaderProps {
  step: number;
  title: string;
  description?: string;
}

function SectionHeader({ step, title, description }: SectionHeaderProps) {
  return (
    <div className="flex items-start gap-3 mb-4">
      <span
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full
          bg-[rgba(var(--accent-rgb),0.15)] border border-[rgba(var(--accent-rgb),0.35)]
          text-[#60a5fa] text-[11px] font-semibold mt-0.5"
      >
        {step}
      </span>
      <div>
        <p className="text-[14px] font-semibold text-white">{title}</p>
        {description && (
          <p className="text-[12px] text-[#888b91] mt-0.5">{description}</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Labeled field wrapper
// ---------------------------------------------------------------------------

interface FieldWrapProps {
  id: string;
  label: React.ReactNode;
  error?: string | null;
  children: React.ReactNode;
}

function FieldWrap({ id, label, error, children }: FieldWrapProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[12.5px] font-medium text-[#b8babf]">
        {label}
      </label>
      {children}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-[11.5px] text-[#e6714f]">
          {error}
        </p>
      )}
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
  const seed = avatar.id.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0);
  const gradient = gradientFor(seed);
  const initials = initialsOf(avatar.name);

  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={`Select ${avatar.name}`}
      onClick={() => onSelect(avatar.id)}
      className={cn(
        'relative flex flex-col items-center gap-2 rounded-[16px] border p-4 transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        'w-28 cursor-pointer',
        selected
          ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.08)]'
          : 'border-white/[0.08] hover:border-white/20',
      )}
    >
      <span className="relative">
        <span className="relative h-16 w-16 block overflow-hidden rounded-full">
          <span
            className="absolute inset-0 rounded-full"
            style={{ background: gradient }}
            aria-hidden="true"
          />
          <video
            src={avatar.thumbnail_url}
            autoPlay
            muted
            loop
            playsInline
            aria-hidden="true"
            className="absolute inset-0 h-full w-full object-cover rounded-full"
            onError={(e) => {
              (e.currentTarget as HTMLVideoElement).style.display = 'none';
            }}
          />
          <span className="absolute inset-0 flex items-center justify-center text-[15px] font-semibold text-white">
            {initials}
          </span>
        </span>

        {selected && (
          <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent)]">
            <Check size={12} aria-hidden="true" />
          </span>
        )}
      </span>

      <div className="text-[13px] font-medium text-white">{avatar.name}</div>
      <div className="text-[10.5px] text-[#888b91] capitalize">{avatar.gender}</div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Device check row — presentation only
// ---------------------------------------------------------------------------

interface DeviceCheckRowProps {
  icon: React.ReactNode;
  label: string;
}

function DeviceCheckRow({ icon, label }: DeviceCheckRowProps) {
  return (
    <div className="flex items-center gap-3 py-2 border-b border-white/[0.06] last:border-0">
      <span className="flex h-7 w-7 flex-none items-center justify-center rounded-[8px] bg-white/[0.05] text-[#888b91]">
        {icon}
      </span>
      <span className="flex-1 text-[13px] text-[#b8babf]">{label}</span>
      <span className="text-[11.5px] text-[#70757c]">pending</span>
    </div>
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

  // ── Language options ────────────────────────────────────────────────────────
  const LANG_TABS: { key: Language; label: string }[] = [
    { key: 'en', label: 'English' },
    { key: 'hi', label: 'हिन्दी' },
    { key: 'te', label: 'తెలుగు' },
  ];

  const LEVEL_TABS: {
    key: 'entry' | 'mid' | 'senior';
    label: string;
    description: string;
  }[] = [
    { key: 'entry', label: t('startInterview.entryLevel'), description: t('startInterview.years03') },
    { key: 'mid', label: t('startInterview.midLevel'), description: t('startInterview.years36') },
    { key: 'senior', label: t('startInterview.seniorLevel'), description: t('startInterview.years6plus') },
  ];

  // ── Language (persisted) ────────────────────────────────────────────────────
  const [selectedLanguage, setSelectedLanguage] = useState<Language>(() => {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === 'en' || stored === 'hi' || stored === 'te') return stored;
    return 'en';
  });

  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, selectedLanguage);
  }, [selectedLanguage]);

  // ── Avatar selection (persisted) ────────────────────────────────────────────
  const [selectedAvatarId, setSelectedAvatarId] = useState<string>(() => {
    const stored = localStorage.getItem(AVATAR_STORAGE_KEY);
    return stored ?? DEFAULT_AVATAR_ID;
  });

  useEffect(() => {
    localStorage.setItem(AVATAR_STORAGE_KEY, selectedAvatarId);
  }, [selectedAvatarId]);

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

  const proceedToSession = useCallback(
    async (formVals: FormValues) => {
      const created = await createJobMutation.mutateAsync(formVals);
      createSessionMutation.mutate(created.id);
    },
    [createJobMutation, createSessionMutation],
  );

  const apiError =
    createJobMutation.error instanceof Error
      ? createJobMutation.error.message
      : createSessionMutation.error instanceof Error
        ? createSessionMutation.error.message
        : null;

  useEffect(() => {
    if (apiError) {
      toast.error(apiError);
    }
  }, [apiError]);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
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
      setConsentError(
        err instanceof Error ? err.message : 'Failed to record consent. Please try again.',
      );
    } finally {
      setIsSubmittingConsent(false);
    }
  }, [recordConsent, proceedToSession, values]);

  const handleDecline = useCallback(() => {
    setShowConsent(false);
    setConsentError(null);
    toast.info('You must consent to use the interview feature.');
  }, []);

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

  // ── SegTabs adapters
  const langTabItems = LANG_TABS.map((l) => ({ key: l.key as string, label: l.label }));
  const levelTabItems = LEVEL_TABS.map((l) => ({ key: l.key as string, label: l.label }));

  return (
    <div className="pb-12">
      {/* ── Page heading ─────────────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        className="mb-6"
      >
        <div className="flex items-center gap-2 text-[13px] text-[#888b91] mb-1">
          <Sparkles size={15} className="text-[#a887dc]" aria-hidden="true" />
          <span>Pre-flight</span>
        </div>
        <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">
          {t('startInterview.pageTitle')}
        </h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          {t('startInterview.pageDesc')}
        </p>
      </motion.div>

      {/* ── Resume status banner — full width ────────────────────────────── */}
      <AnimatePresence mode="wait">
        {!consentLoading && me !== undefined && (
          <motion.div
            key={me.has_resume ? 'resume-ok' : 'resume-missing'}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="mb-5"
          >
            {me.has_resume ? (
              <div
                aria-live="polite"
                className="flex items-center gap-2 rounded-[16px] border border-[rgba(39,201,63,0.25)]
                  bg-[rgba(39,201,63,0.06)] px-4 py-2.5 text-[13px]"
              >
                <CheckCircle2 className="h-4 w-4 shrink-0 text-[#27c93f]" aria-hidden="true" />
                <span className="font-medium text-white">{t('startInterview.resumeOnFile')}</span>
                <span className="text-[#888b91]">{t('startInterview.resumeOnFileDesc')}</span>
              </div>
            ) : (
              <div
                aria-live="polite"
                className="flex items-start gap-2 rounded-[16px] border border-white/[0.08]
                  bg-[rgba(255,183,100,0.06)] px-4 py-2.5 text-[13px]"
              >
                <AlertCircle className="h-4 w-4 shrink-0 text-[#ffb764] mt-0.5" aria-hidden="true" />
                <span className="text-[#b8babf]">
                  {t('startInterview.noResume')}{' '}
                  <Link
                    to="/dashboard"
                    className="font-medium text-[#60a5fa] underline-offset-2 hover:underline
                      focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
                  >
                    {t('startInterview.noResumeLink')}
                  </Link>{' '}
                  {t('startInterview.noResumeDesc')}
                </span>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── 2-column form layout ──────────────────────────────────────────── */}
      <form onSubmit={handleSubmit} noValidate aria-label="Start interview form">
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_340px] lg:items-start">

          {/* ── LEFT COLUMN: Steps 1–3 ─────────────────────────────────── */}
          <motion.div
            variants={staggerParent}
            initial="hidden"
            animate="show"
            className="space-y-5"
          >
            {/* Step 1: Role details */}
            <motion.div variants={staggerChild}>
              <GlassCard className="p-5 space-y-4">
                <SectionHeader
                  step={1}
                  title={t('startInterview.step1Title')}
                  description={t('startInterview.step1Desc')}
                />

                <FieldWrap
                  id="si-title"
                  label={
                    <>
                      {t('startInterview.jobTitleLabel')}{' '}
                      <span aria-hidden="true" className="text-[#e6714f]">*</span>
                    </>
                  }
                  error={titleError}
                >
                  <input
                    id="si-title"
                    type="text"
                    required
                    aria-required="true"
                    aria-describedby={titleError ? 'si-title-error' : undefined}
                    aria-invalid={titleError !== null}
                    value={values.title}
                    onChange={(e) => {
                      setValues((v) => ({ ...v, title: e.target.value }));
                      if (titleError) setTitleError(null);
                    }}
                    placeholder={t('startInterview.jobTitlePlaceholder')}
                    className={cn(inputCls, titleError && inputErrCls)}
                  />
                </FieldWrap>

                <FieldWrap
                  id="si-company"
                  label={
                    <>
                      {t('startInterview.companyLabel')}{' '}
                      <span className="text-[11px] font-normal text-[#70757c]">
                        ({t('startInterview.optional')})
                      </span>
                    </>
                  }
                >
                  <input
                    id="si-company"
                    type="text"
                    value={values.company_name}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, company_name: e.target.value }))
                    }
                    placeholder={t('startInterview.companyPlaceholder')}
                    className={inputCls}
                  />
                </FieldWrap>

                <FieldWrap
                  id="si-jd"
                  label={
                    <>
                      {t('startInterview.jdLabel')}{' '}
                      <span className="text-[11px] font-normal text-[#70757c]">
                        ({t('startInterview.optional')})
                      </span>
                    </>
                  }
                >
                  <textarea
                    id="si-jd"
                    rows={4}
                    value={values.jd_text}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, jd_text: e.target.value }))
                    }
                    placeholder={t('startInterview.jdPlaceholder')}
                    className={cn(inputCls, 'resize-y min-h-[80px]')}
                  />
                </FieldWrap>
              </GlassCard>
            </motion.div>

            {/* Step 2: Avatar picker */}
            <motion.div variants={staggerChild}>
              <GlassCard className="p-5 space-y-4">
                <SectionHeader
                  step={2}
                  title={t('startInterview.step2Title')}
                  description={t('startInterview.step2Desc')}
                />

                {avatarsLoading && (
                  <div
                    aria-live="polite"
                    aria-label={t('startInterview.loadingInterviewers')}
                    className="flex flex-wrap gap-3"
                  >
                    {[0, 1, 2].map((i) => (
                      <div
                        key={i}
                        className="h-36 w-28 rounded-[16px] bg-white/[0.06] animate-pulse"
                      />
                    ))}
                  </div>
                )}

                {!avatarsLoading && avatarsError && (
                  <div
                    aria-live="polite"
                    className="rounded-[12px] border border-white/[0.08] bg-[rgba(255,183,100,0.06)]
                      px-4 py-2.5 text-[13px] text-[#ffb764]"
                  >
                    {t('startInterview.avatarLoadError')}
                  </div>
                )}

                {!avatarsLoading && !avatarsError && avatars !== undefined && (
                  <div className="space-y-4">
                    {maleAvatars.length > 0 && (
                      <div>
                        <p className="text-[11px] font-semibold text-[#70757c] uppercase tracking-[0.08em] mb-2">
                          {t('startInterview.maleGroup')}
                        </p>
                        <div
                          className="flex flex-wrap gap-3"
                          role="radiogroup"
                          aria-label={t('startInterview.maleGroup')}
                        >
                          {maleAvatars.map((avatar) => (
                            <AvatarCard
                              key={avatar.id}
                              avatar={avatar}
                              selected={selectedAvatarId === avatar.id}
                              onSelect={setSelectedAvatarId}
                            />
                          ))}
                        </div>
                      </div>
                    )}

                    {femaleAvatars.length > 0 && (
                      <div>
                        <p className="text-[11px] font-semibold text-[#70757c] uppercase tracking-[0.08em] mb-2">
                          {t('startInterview.femaleGroup')}
                        </p>
                        <div
                          className="flex flex-wrap gap-3"
                          role="radiogroup"
                          aria-label={t('startInterview.femaleGroup')}
                        >
                          {femaleAvatars.map((avatar) => (
                            <AvatarCard
                              key={avatar.id}
                              avatar={avatar}
                              selected={selectedAvatarId === avatar.id}
                              onSelect={setSelectedAvatarId}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </GlassCard>
            </motion.div>

            {/* Step 3: Interview language + level */}
            <motion.div variants={staggerChild}>
              <GlassCard className="p-5 space-y-5">
                <SectionHeader
                  step={3}
                  title={t('startInterview.step3Title')}
                  description={t('startInterview.step3Desc')}
                />

                {/* Language */}
                <div className="space-y-2">
                  <p className="text-[12.5px] font-medium text-[#b8babf]">
                    {t('startInterview.interviewLanguageLabel')}
                  </p>
                  <select
                    id="si-language"
                    value={selectedLanguage}
                    onChange={(e) => setSelectedLanguage(e.target.value as Language)}
                    className="sr-only"
                    aria-label={t('startInterview.interviewLanguageLabel')}
                  >
                    {LANG_TABS.map((opt) => (
                      <option key={opt.key} value={opt.key}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                  <SegTabs
                    tabs={langTabItems}
                    active={selectedLanguage as string}
                    onChange={(k) => setSelectedLanguage(k as Language)}
                    className="w-full justify-stretch"
                  />
                  <p className="text-[12px] text-[#70757c]">
                    You can switch languages mid-interview if you get stuck.
                  </p>
                </div>

                {/* Experience level */}
                <div className="space-y-2">
                  <p className="text-[12.5px] font-medium text-[#b8babf]">
                    {t('startInterview.experienceLevelLabel')}
                  </p>
                  <select
                    id="si-level"
                    value={values.level}
                    onChange={(e) =>
                      setValues((v) => ({
                        ...v,
                        level: e.target.value as 'entry' | 'mid' | 'senior',
                      }))
                    }
                    className="sr-only"
                    aria-label={t('startInterview.experienceLevelLabel')}
                  >
                    {LEVEL_TABS.map((opt) => (
                      <option key={opt.key} value={opt.key}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                  <SegTabs
                    tabs={levelTabItems}
                    active={values.level as string}
                    onChange={(k) =>
                      setValues((v) => ({
                        ...v,
                        level: k as 'entry' | 'mid' | 'senior',
                      }))
                    }
                    className="w-full justify-stretch"
                  />
                  <p className="text-[12px] text-[#70757c]">
                    {LEVEL_TABS.find((l) => l.key === values.level)?.description}
                  </p>
                </div>
              </GlassCard>
            </motion.div>
          </motion.div>

          {/* ── RIGHT COLUMN: Device check + consent + submit (sticky) ──── */}
          <div className="lg:sticky lg:top-20 space-y-4">
            <GlassCard feature className="p-5 space-y-4">
              {/* Header */}
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#60a5fa] mb-1">
                  MIC &amp; DEVICE CHECK
                </p>
                <p className="text-[12.5px] text-[#888b91]">
                  Make sure your setup is ready before you begin.
                </p>
              </div>

              {/* WaveBars visual */}
              <div className="flex items-center justify-center rounded-[12px] border border-white/[0.08] bg-[rgba(var(--accent-rgb),0.04)] py-4">
                <WaveBars active={false} bars={20} color="var(--accent)" height={32} />
              </div>

              {/* Run device check — presentation */}
              <Pill
                type="button"
                variant="ghost"
                className="w-full"
                aria-label="Run device check (presentation only)"
                onClick={() => {
                  /* presentation only */
                }}
              >
                <Mic size={15} aria-hidden="true" />
                Run device check
              </Pill>

              {/* Checklist */}
              <div>
                <DeviceCheckRow
                  icon={<Mic size={14} aria-hidden="true" />}
                  label="Microphone"
                />
                <DeviceCheckRow
                  icon={<Camera size={14} aria-hidden="true" />}
                  label="Camera (proctoring)"
                />
                <DeviceCheckRow
                  icon={<Wifi size={14} aria-hidden="true" />}
                  label="Connection"
                />
                <DeviceCheckRow
                  icon={<Globe size={14} aria-hidden="true" />}
                  label="Browser"
                />
              </div>

              {/* DPDP consent note */}
              <div className="rounded-[12px] border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
                <div className="flex items-start gap-2">
                  <ShieldCheck
                    size={14}
                    className="shrink-0 text-[#27c93f] mt-0.5"
                    aria-hidden="true"
                  />
                  <p className="text-[12px] text-[#888b91] leading-relaxed">
                    {t('startInterview.consentNote')}
                  </p>
                </div>
              </div>

              {/* Review summary — visible once title is entered */}
              <AnimatePresence>
                {values.title.trim() && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="rounded-[12px] border border-white/[0.08] bg-[rgba(28,29,31,0.6)] px-4 py-3 space-y-1.5 text-[13px]">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-white">
                          {values.title.trim()}
                        </span>
                        {values.company_name.trim() && (
                          <>
                            <span className="text-[#888b91]">{t('startInterview.at')}</span>
                            <span className="text-[#b8babf]">
                              {values.company_name.trim()}
                            </span>
                          </>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <StatusTag tone="electric">{levelLabel}</StatusTag>
                        <StatusTag tone="lavender">{languageLabel}</StatusTag>
                        <StatusTag tone="neutral">{avatarLabel}</StatusTag>
                        {values.jd_text.trim() && (
                          <StatusTag tone="forest">
                            {t('startInterview.jdProvided')}
                          </StatusTag>
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* API error */}
              {apiError && (
                <div
                  role="alert"
                  className="rounded-[12px] border border-[rgba(230,113,79,0.35)]
                    bg-[rgba(230,113,79,0.1)] px-4 py-3 text-[13px] text-[#e6714f]"
                >
                  {apiError}
                </div>
              )}

              {/* Submit CTA */}
              <Pill
                type="submit"
                disabled={isSubmitting}
                aria-busy={isSubmitting}
                className="w-full justify-center"
              >
                {isSubmitting ? (
                  <span className="flex items-center gap-2">
                    <span
                      className="h-4 w-4 animate-spin rounded-full border-2 border-black border-t-transparent"
                      aria-hidden="true"
                    />
                    {t('startInterview.starting')}
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    {consented
                      ? t('startInterview.startButton')
                      : 'Accept consent to begin'}
                    <ArrowRight size={16} aria-hidden="true" />
                  </span>
                )}
              </Pill>

              {/* Ready status indicator */}
              <div className="flex justify-center">
                {!values.title.trim() ? (
                  <StatusTag tone="amber">Fill in role details to begin</StatusTag>
                ) : (
                  <StatusTag tone="forest" dot>
                    Ready
                  </StatusTag>
                )}
              </div>
            </GlassCard>
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
    </div>
  );
}
