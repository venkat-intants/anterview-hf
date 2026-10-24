// InterviewInvite — public applicant landing for an interview magic link (Phase 3).
//
// No login. Token comes from the URL #fragment (NOT a path param — security).
// Preview → DPDP consent → redeem (which lazily provisions a guest session
// server-side) → store the returned guest token → navigate into the existing
// /interview/:sessionId UI, unchanged.
//
// SECURITY: token MUST come from window.location.hash — NOT useParams. It is
// kept for this tab in sessionStorage so a reload of this page works (audit
// CA-H3, the /exam pattern), and dropped once the link can do nothing more.
//
// A SIGNED-IN CANDIDATE KEEPS THEIR ACCOUNT (audit CA-H4). The dashboard's
// "Start interview" mints a fresh link for the candidate's own invitation and
// sends them here. Redeeming used to replace their account session with the
// guest token, silently: afterwards the complete and scorecard pages treated
// them as a guest, with no way back to their dashboard. An invitation minted
// from an account belongs to an applicant linked to that account, so redeem
// creates the session under the account's own user id, and the account token
// can take the interview. The guest token is therefore not adopted when it is
// for the same user. When it is for SOMEBODY ELSE (a shared machine, a link
// forwarded to the wrong person), the account session is not replaced either:
// the page says whose account is signed in and offers to sign out first.

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation, Trans } from 'react-i18next';
import {
  Loader2,
  CheckCircle2,
  Clock,
  Camera,
  Mic,
  ShieldCheck,
  Calendar,
} from '@/design/components/icons';
import {
  getInterviewInvite,
  redeemInterviewInvite,
  type InterviewRedeem as RedeemResult,
} from '@/api/publicInterview';
import { logout } from '@/api/auth';
import { isTransientApiError } from '@/api/client';
import { useAuth } from '@/context/AuthContext';
import { StatusTag, Avatar } from '@/design/components/primitives';
import { Button } from '@/components/ui/button';
import { LoadingSkeleton } from '@/components/candidate/LoadingSkeleton';
import { PublicFrame, PublicNotice } from '@/components/PublicFrame';
import { PublicNext } from '@/components/PublicNext';
import {
  FRAGMENT_TOKEN_KEYS,
  forgetFragmentToken,
  useFragmentToken,
} from '@/hooks/useFragmentToken';
import { formatDateTime, uiLocale } from '@/lib/formatters';
import { isGuestOnly } from '@/lib/guest';
import { useCandidateError } from '@/lib/candidateCopy';

const LANGUAGE_STORAGE_KEY = 'intants:interview-language';

// ── Shared public frame, one centred card ──
function PageWrap({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  return (
    <PublicFrame width="narrow" privateLink step={t('public.step.invite')}>
      {children}
    </PublicFrame>
  );
}

export default function InterviewInvite() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { setAuth, clearAuth, isAuthenticated, user } = useAuth();
  const errorText = useCandidateError();
  // The account signed in on this tab, if any. A guest session from an earlier
  // invite is not an account and may be replaced as before.
  const account = isAuthenticated && user && !isGuestOnly(user.roles) ? user : null;

  // SECURITY: token from #fragment — never from a path param.
  const token = useFragmentToken(FRAGMENT_TOKEN_KEYS.invite);
  const [consent, setConsent] = useState(false);
  // Set when the invitation turned out to be for a different person than the
  // signed-in account: the page asks them to sign out rather than swapping.
  const [otherAccount, setOtherAccount] = useState<RedeemResult | null>(null);

  const inviteQ = useQuery({
    queryKey: ['interview-invite', token],
    queryFn: () => getInterviewInvite(token),
    enabled: token.length > 0,
    // A definitive rejection (404 bad/revoked link) must not be retried, but a
    // transient failure (network blip, Space replica swap, 5xx) must not brand
    // a perfectly good link "invalid" — retry those a couple of times.
    retry: (failureCount, error) => isTransientApiError(error) && failureCount < 2,
    staleTime: Infinity,
  });

  function enter(r: RedeemResult, adoptGuest: boolean): void {
    if (adoptGuest) {
      setAuth(r.access_token, {
        user_id: r.user_id,
        full_name: r.full_name,
        email: r.email ?? '',
        roles: r.roles,
        must_change_password: false,
      });
    }
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, r.language);
    } catch {
      // non-fatal — the session already carries the language server-side
    }
    // Strip the token from the URL before leaving the page.
    window.history.replaceState(null, '', '/interview-invite');
    void navigate(`/interview/${r.session_id}`, { replace: true });
  }

  const redeemMut = useMutation({
    // The signed-in account's id travels with the call, so the decision below
    // is about who was signed in when Begin was pressed.
    mutationFn: (_accountId: string | null) => redeemInterviewInvite(token, true),
    onSuccess: (r, accountId) => {
      if (accountId === null) {
        enter(r, true);
      } else if (accountId === r.user_id) {
        // The account owns the session (redeem created it under the account's
        // user id), so its own token takes the interview; nothing is swapped.
        enter(r, false);
      } else {
        // Not this account's interview. Never swap a signed-in account for a
        // guest without saying so.
        setOtherAccount(r);
      }
    },
  });

  // Sign the other account out (server side too, so a reload does not bring it
  // back from the refresh cookie), then enter as the invited applicant with the
  // guest session the redeem already issued.
  const switchAccount = useMutation({
    mutationFn: async () => {
      try {
        await logout();
      } catch {
        // Signing out locally still stops this tab acting as that account.
      }
    },
    onSettled: () => {
      const pending = otherAccount;
      queryClient.clear();
      clearAuth();
      setOtherAccount(null);
      if (pending) enter(pending, true);
    },
  });

  // A link that can do nothing more is spent: drop it from this tab's storage.
  const spent =
    (inviteQ.isError && !isTransientApiError(inviteQ.error)) ||
    inviteQ.data?.already_completed === true;
  useEffect(() => {
    if (spent) forgetFragmentToken(FRAGMENT_TOKEN_KEYS.invite);
  }, [spent]);

  // ── Transient failure (network / server hiccup) — the link may be fine ──
  if (token && inviteQ.isError && isTransientApiError(inviteQ.error)) {
    return (
      <PageWrap>
        <PublicNotice
          title={t('interviewInvite.networkTitle')}
          description={t('interviewInvite.networkDesc')}
          action={
            <Button className="h-12 px-8" onClick={() => void inviteQ.refetch()}>
              {t('interviewInvite.retry')}
            </Button>
          }
        />
      </PageWrap>
    );
  }

  // ── No token / invalid / expired ──
  if (!token || inviteQ.isError) {
    return (
      <PageWrap>
        <PublicNotice
          title={t('interviewInvite.invalidTitle')}
          description={t('interviewInvite.invalidDesc')}
          hint={t('publicFrame.reopenEmail')}
        />
      </PageWrap>
    );
  }

  // ── Loading ──
  if (inviteQ.isLoading || !inviteQ.data) {
    return (
      <PageWrap>
        <LoadingSkeleton label={t('interviewInvite.loading')} />
      </PageWrap>
    );
  }

  const info = inviteQ.data;

  // ── Already completed ──
  if (info.already_completed) {
    return (
      <PageWrap>
        <PublicNotice
          tone="ok"
          icon={<CheckCircle2 size={22} aria-hidden="true" />}
          title={t('interviewInvite.completedTitle')}
          description={t('interviewInvite.completedDesc')}
        />
        <PublicNext className="mt-6">{t('public.invite.next')}</PublicNext>
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
    <PageWrap>
      <div className="rounded-card border border-border bg-card p-5 shadow-card sm:p-8">
        {/* Status badge */}
        <StatusTag tone="electric" dot>
          {t('interviewInvite.title')}
        </StatusTag>

        {/* Role heading */}
        <h1 className="mt-4 text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground">
          {info.job_title}
        </h1>

        {/* Personalised greeting (Trans keeps the bold-name interpolation) */}
        <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground">
          <Trans
            i18nKey="interviewInvite.greeting"
            values={{ name: info.applicant_name, role: info.job_title }}
            components={{ 1: <span className="font-medium text-foreground" /> }}
          />
        </p>

        {/* Avatar / AI interviewer card */}
        <div className="mt-5 flex items-center gap-3 rounded-[12px] bg-[var(--ui-inset-soft)] p-4">
          <Avatar initials="AI" gradient="var(--ui-grad-avatar)" size={40} />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-foreground">
              {t('interviewInvite.avatarName')}
            </div>
            {info.scheduled_at ? (
              <p className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
                <Calendar size={13} aria-hidden="true" />
                {t('interviewInvite.scheduledFor', {
                  time: formatDateTime(info.scheduled_at, { locale: uiLocale(i18n.language) }),
                })}
              </p>
            ) : (
              <div className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
                <Calendar size={13} aria-hidden="true" />
                {t('interviewInvite.availableNow')}
              </div>
            )}
          </div>
        </div>

        {/* Device readiness checklist */}
        <div className="mt-5">
          <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--ui-soft)]">
            {t('interviewInvite.beforeYouBegin')}
          </div>
          <div className="grid grid-cols-3 gap-2 sm:gap-3">
            {checks.map((c) => {
              const Icon = c.icon;
              return (
                <div
                  key={c.label}
                  className="flex flex-col items-center gap-2 rounded-[12px] bg-[var(--ui-inset-soft)] p-3 text-center sm:p-4"
                >
                  <Icon size={20} className="text-[var(--ui-info)]" aria-hidden="true" />
                  <span className="text-[12.5px] leading-snug text-foreground">{c.label}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* DPDP consent checkbox — gates the Begin button */}
        <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-[12px] bg-[var(--ui-inset-soft)] p-4 text-[13.5px] leading-relaxed text-[var(--ui-soft)] focus-within:ring-2 focus-within:ring-[var(--accent)]">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            className="mt-0.5 h-5 w-5 flex-none accent-[var(--accent)]"
          />
          <span className="flex items-start gap-1.5">
            <ShieldCheck size={16} className="mt-0.5 flex-none text-[var(--ui-ok)]" aria-hidden="true" />
            {t('interviewInvite.consent')}
          </span>
        </label>

        {/* Begin button — disabled until consent is given */}
        {otherAccount ? (
          <div role="alert" className="mt-5 rounded-[12px] border border-[var(--ui-warn)] p-4 text-[13.5px] leading-relaxed text-foreground">
            <p>{t('interviewInvite.otherAccount', { email: account?.email ?? '' })}</p>
            <Button
              variant="outline"
              className="mt-3 h-11"
              disabled={switchAccount.isPending}
              onClick={() => switchAccount.mutate()}
            >
              {switchAccount.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              {t('interviewInvite.signOutAndBegin')}
            </Button>
          </div>
        ) : null}

        <Button
          className="mt-6 h-12 w-full text-[15px]"
          disabled={!consent || redeemMut.isPending || otherAccount !== null}
          onClick={() => redeemMut.mutate(account?.user_id ?? null)}
        >
          {redeemMut.isPending ? (
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
          ) : null}
          {t('interviewInvite.begin')}
        </Button>

        {redeemMut.isError && (
          <p role="alert" className="mt-3 text-center text-[14px] text-[var(--ui-danger)]">
            {errorText(redeemMut.error, t('interviewInvite.startError'))}
          </p>
        )}

        {/* Help footer — support contact only. This is an HR-issued invite
            link; the applicant has NO account, so there is deliberately no
            login/sign-in affordance anywhere in this flow. */}
        <p className="mt-4 text-center text-[13px] text-[var(--ui-soft)]">
          {t('interviewInvite.needHelp')}{' '}
          <a
            href="mailto:support@intants.com"
            className="font-medium text-[var(--ui-info)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            support@intants.com
          </a>
        </p>
      </div>
    </PageWrap>
  );
}
