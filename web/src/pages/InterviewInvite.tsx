// InterviewInvite — public applicant landing for an interview magic link (Phase 3).
//
// No login. Token comes from the URL #fragment (NOT a path param — security).
// Preview → DPDP consent → redeem (which lazily provisions a guest session
// server-side) → store the returned guest token → navigate into the existing
// /interview/:sessionId UI, unchanged.
//
// SECURITY: token MUST come from window.location.hash — NOT useParams.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useTranslation, Trans } from 'react-i18next';
import { motion } from 'framer-motion';
import {
  Loader2,
  AlertCircle,
  CheckCircle2,
  Clock,
  Camera,
  Mic,
  ShieldCheck,
  Calendar,
} from '@/design/components/icons';
import { getInterviewInvite, redeemInterviewInvite } from '@/api/publicInterview';
import { useAuth } from '@/context/AuthContext';
import { AuroraField } from '@/design/components/AuroraField';
import { GlassCard, Pill, StatusTag, Avatar } from '@/design/components/primitives';

const LANGUAGE_STORAGE_KEY = 'intants:interview-language';

// ── Full-page dark centred layout ──
function PageWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-midnight px-6 py-12 font-sans text-foreground">
      <AuroraField />
      {/* Logo mark */}
      <div className="absolute left-6 top-6 z-10 flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[linear-gradient(135deg,#112d72,#a887dc)]">
          <span className="h-2.5 w-2.5 rounded-full bg-white" />
        </span>
        <span className="text-[15px] font-semibold text-foreground">Anterview</span>
      </div>
      <div className="relative z-10 flex w-full max-w-[520px] flex-col items-center gap-4 text-center">
        {children}
      </div>
    </div>
  );
}

export default function InterviewInvite() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setAuth } = useAuth();

  // SECURITY: token from #fragment — never from a path param.
  const [token] = useState(() => window.location.hash.replace(/^#/, '').trim());
  const [consent, setConsent] = useState(false);

  const inviteQ = useQuery({
    queryKey: ['interview-invite', token],
    queryFn: () => getInterviewInvite(token),
    enabled: token.length > 0,
    retry: false,
    staleTime: Infinity,
  });

  const redeemMut = useMutation({
    mutationFn: () => redeemInterviewInvite(token, true),
    onSuccess: (r) => {
      setAuth(r.access_token, {
        user_id: r.user_id,
        full_name: r.full_name,
        email: r.email ?? '',
        roles: r.roles,
        must_change_password: false,
      });
      try {
        localStorage.setItem(LANGUAGE_STORAGE_KEY, r.language);
      } catch {
        // non-fatal — the session already carries the language server-side
      }
      // Strip the token from the URL before leaving the page.
      window.history.replaceState(null, '', '/interview-invite');
      navigate(`/interview/${r.session_id}`, { replace: true });
    },
  });

  // ── No token / invalid / expired ──
  if (!token || inviteQ.isError) {
    return (
      <PageWrap>
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-[9px] bg-[rgba(255,183,100,0.15)] text-amber-glow shadow-sm">
          <AlertCircle className="h-6 w-6" aria-hidden="true" />
        </span>
        <h1 className="text-subheading font-semibold text-foreground">
          {t('interviewInvite.invalidTitle')}
        </h1>
        <p className="max-w-sm text-body-sm text-muted-foreground">
          {t('interviewInvite.invalidDesc')}
        </p>
      </PageWrap>
    );
  }

  // ── Loading ──
  if (inviteQ.isLoading || !inviteQ.data) {
    return (
      <PageWrap>
        <Loader2 className="h-8 w-8 animate-spin text-electric" aria-hidden="true" />
        <p className="text-body-sm text-muted-foreground">{t('interviewInvite.loading')}</p>
      </PageWrap>
    );
  }

  const info = inviteQ.data;

  // ── Already completed ──
  if (info.already_completed) {
    return (
      <PageWrap>
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-[9px] bg-[rgba(39,201,63,0.15)] text-vivid-mint shadow-sm">
          <CheckCircle2 className="h-6 w-6" aria-hidden="true" />
        </span>
        <h1 className="text-subheading font-semibold text-foreground">
          {t('interviewInvite.completedTitle')}
        </h1>
        <p className="max-w-sm text-body-sm text-muted-foreground">
          {t('interviewInvite.completedDesc')}
        </p>
      </PageWrap>
    );
  }

  // ── Device readiness checklist items ──
  const checks: { icon: typeof Camera; label: string }[] = [
    { icon: Camera, label: t('interviewInvite.checkCamera') },
    { icon: Mic, label: t('interviewInvite.checkMic') },
    { icon: Clock, label: t('interviewInvite.checkTime') },
  ];

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-midnight px-6 py-12 font-sans text-foreground">
      <AuroraField />
      <div className="relative z-10 w-full max-w-[520px]">
        {/* Logo */}
        <div className="mb-6 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[linear-gradient(135deg,#112d72,#a887dc)]">
            <span className="h-2.5 w-2.5 rounded-full bg-white" />
          </span>
          <span className="text-[15px] font-semibold text-foreground">Anterview</span>
        </div>

        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <GlassCard className="p-8">
            {/* Status badge */}
            <StatusTag tone="electric" dot>
              {t('interviewInvite.title')}
            </StatusTag>

            {/* Role heading */}
            <h1 className="mt-4 text-[26px] font-semibold tracking-[-0.8px] text-foreground">
              {info.job_title}
            </h1>

            {/* Token hint — keeps the magic-link UX without leaking company_name (not in API type) */}
            <p className="mt-1 font-mono text-[11px] text-fog">
              #{token.slice(0, 8)}
            </p>

            {/* Personalised greeting (Trans keeps the bold-name interpolation) */}
            <p className="mt-3 text-body-sm text-muted-foreground">
              <Trans
                i18nKey="interviewInvite.greeting"
                values={{ name: info.applicant_name, role: info.job_title }}
                components={{ 1: <span className="font-medium text-foreground" /> }}
              />
            </p>

            {/* Avatar / AI interviewer card */}
            <div className="mt-5 flex items-center gap-3 rounded-[12px] border border-white/[0.08] bg-white/[0.02] p-4">
              <Avatar
                initials="AI"
                gradient="linear-gradient(135deg,var(--accent),#a887dc)"
                size={40}
              />
              <div>
                <div className="text-[14px] font-medium text-foreground">
                  {t('interviewInvite.avatarName')}
                </div>
                {info.scheduled_at ? (
                  <p className="flex items-center gap-1.5 text-[12.5px] text-ash">
                    <Calendar size={13} aria-hidden="true" />
                    {t('interviewInvite.scheduledFor', {
                      time: new Date(info.scheduled_at).toLocaleString(),
                    })}
                  </p>
                ) : (
                  <div className="flex items-center gap-1.5 text-[12.5px] text-ash">
                    <Calendar size={13} aria-hidden="true" />
                    {t('interviewInvite.availableNow')}
                  </div>
                )}
              </div>
            </div>

            {/* Device readiness checklist */}
            <div className="mt-5">
              <div className="mb-2 text-[12px] uppercase tracking-[0.5px] text-fog">
                {t('interviewInvite.beforeYouBegin')}
              </div>
              <div className="grid grid-cols-3 gap-3">
                {checks.map((c) => {
                  const Icon = c.icon;
                  return (
                    <div
                      key={c.label}
                      className="flex flex-col items-center gap-2 rounded-[12px] border border-white/[0.08] bg-white/[0.02] p-4 text-center"
                    >
                      <Icon size={18} className="text-electric" aria-hidden="true" />
                      <span className="text-[12px] text-mist">{c.label}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* DPDP consent checkbox — gates the Begin button */}
            <label className="mt-5 flex cursor-pointer items-start gap-2.5 rounded-[12px] border border-white/[0.08] bg-white/[0.02] p-4 text-[12.5px] text-mist">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 h-4 w-4 flex-none accent-electric"
              />
              <span className="flex items-center gap-1.5">
                <ShieldCheck size={14} className="flex-none text-vivid-mint" aria-hidden="true" />
                {t('interviewInvite.consent')}
              </span>
            </label>

            {/* Begin button — disabled until consent is given */}
            <Pill
              className="mt-6 w-full py-3.5"
              disabled={!consent || redeemMut.isPending}
              onClick={() => redeemMut.mutate()}
            >
              {redeemMut.isPending ? (
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              ) : null}
              {t('interviewInvite.begin')}
            </Pill>

            {redeemMut.isError && (
              <p className="mt-3 text-center text-body-sm text-ember">
                {redeemMut.error instanceof Error
                  ? redeemMut.error.message
                  : t('interviewInvite.startError')}
              </p>
            )}

            {/* Help footer — support contact only. This is an HR-issued invite
                link; the applicant has NO account, so there is deliberately no
                login/sign-in affordance anywhere in this flow. */}
            <p className="mt-4 text-center text-[12px] text-fog">
              {t('interviewInvite.needHelp')}{' '}
              <a href="mailto:support@intants.com" className="text-electric hover:underline">
                support@intants.com
              </a>
            </p>
          </GlassCard>
        </motion.div>
      </div>
    </div>
  );
}
