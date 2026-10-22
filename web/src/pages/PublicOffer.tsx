// PublicOffer — the candidate's own offer, reached with no login from the
// emailed link. EN/HI/TE (candidate-facing, per CLAUDE.md) with a language
// switch, like the other public pages.
//
// SECURITY (this page was the subject of a security review):
// - The token comes from the URL #FRAGMENT, never a path param or query
//   string — read once, then stripped from the address bar with
//   history.replaceState so it never lingers in browser history. It is kept in
//   memory and in THIS TAB's sessionStorage (useFragmentToken's per-tab mode,
//   the /exam pattern), so a reload while reading the offer or waiting for a
//   code does not land on "this link isn't valid" (audit CA-H3). Never
//   localStorage, a cookie or a URL, and dropped once the offer reaches a
//   terminal state. It is sent as the X-Offer-Token header on every /offer
//   call (see api/publicOffer.ts) — never a URL.
// - Answering needs a 6-digit code emailed on request. Documents need a
//   SECOND code, traded for an hour-long session token that is ALSO kept in
//   memory only — never localStorage, sessionStorage, or a URL — and sent as
//   X-Offer-Session alongside the offer token.
// - There is deliberately no candidate download of an uploaded document.
//
// Every terminal state (expired, withdrawn, already declined, locked, or an
// invalid link) gets a plain full-page message rather than a form that quietly
// does nothing — except a lock met inside the documents step, which is shown
// there, in place, with the same words.

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, isTransientApiError } from '@/api/client';
import {
  acceptOffer,
  declineOffer,
  getMyDocuments,
  withdrawDocumentsConsent,
  openDocumentsSession,
  requestDocumentsCode,
  requestOfferCode,
  uploadMyDocument,
  viewOffer,
  type CodePurpose,
  type PublicChecklistItem,
  type PublicOffer as PublicOfferShape,
} from '@/api/publicOffer';
import type { PayPeriod } from '@/api/offers';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { DataList, Stepper } from '@/design/components/page';
import type { DataItem } from '@/design/components/page';
import { WhatNext } from '@/design/components/next-step';
import { Button } from '@/components/ui/button';
import { PublicFrame, PublicNotice } from '@/components/PublicFrame';
import {
  documentsRemaining,
  offerStep,
  type OfferStepKey,
} from '@/lib/offerSteps';
import { AlertCircle, CheckCircle2, Loader2, Lock, Upload } from '@/design/components/icons';
import {
  FRAGMENT_TOKEN_KEYS,
  forgetFragmentToken,
  useFragmentToken,
} from '@/hooks/useFragmentToken';

/** Offer states in which the link can do nothing more. */
const TERMINAL_OFFER_STATUSES: ReadonlySet<string> = new Set(['expired', 'withdrawn', 'declined']);

const PAY_PERIOD_KEY: Record<PayPeriod, string> = {
  annual: 'offer.payPerYear',
  monthly: 'offer.payPerMonth',
  hourly: 'offer.payPerHour',
};

const MAX_DOC_BYTES = 10 * 1024 * 1024;
const ACCEPTED_TYPES = ['application/pdf', 'image/jpeg', 'image/png'];

function localeFor(lang: string): string {
  if (lang.startsWith('hi')) return 'hi-IN';
  if (lang.startsWith('te')) return 'te-IN';
  return 'en-IN';
}

/** The server's own sentence, shown exactly as written — never paraphrased
 *  or translated, so a candidate reading it and calling the hiring team
 *  describes precisely what they saw. */
function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function isLocked(e: unknown): e is ApiError {
  return e instanceof ApiError && e.status === 423;
}

function isUnauthorized(e: unknown): e is ApiError {
  return e instanceof ApiError && e.status === 401;
}

function money(offer: PublicOfferShape, locale: string): string | null {
  if (!offer.base_salary || !offer.currency) return null;
  const amount = Number(offer.base_salary);
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: offer.currency,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${offer.currency} ${amount.toLocaleString(locale)}`;
  }
}

/* ── Shared page chrome ───────────────────────────────────────────────────── */

const FIELD =
  'mt-1.5 flex min-h-11 w-full rounded-[12px] border border-input bg-card px-3.5 py-2 text-base text-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20 md:text-sm';
const FIELD_LABEL = 'block text-[13px] font-medium text-foreground';

function PageWrap({ children, company }: { children: React.ReactNode; company?: string | null }) {
  return (
    <PublicFrame company={company} width="narrow" privateLink>
      <div className="flex w-full flex-col gap-6">{children}</div>
    </PublicFrame>
  );
}

function CenteredMessage({
  icon,
  title,
  desc,
  tone = 'warn',
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  tone?: 'warn' | 'ok' | 'danger';
}) {
  return (
    <PublicNotice
      icon={icon}
      tone={tone}
      title={title}
      description={desc}
    />
  );
}

/* ── Offer summary ────────────────────────────────────────────────────────── */

/** The offer's facts. With `hideTitle` it leaves out the tag and heading, for
 *  when the page already names the offer (the collapsed "Offer accepted" row). */
function OfferFacts({
  offer,
  locale,
  hideTitle = false,
}: {
  offer: PublicOfferShape;
  locale: string;
  hideTitle?: boolean;
}) {
  const { t } = useTranslation();
  const pay = money(offer, locale);
  // Only the facts this offer actually states; an absent one is left out rather
  // than shown as an empty row.
  const facts: DataItem[] = [];
  if (pay) {
    facts.push({
      label: t('offer.compensation'),
      // The period in words, not the stored code ("/ annual"; audit CA-L10).
      value: offer.pay_period ? t(PAY_PERIOD_KEY[offer.pay_period], { pay }) : pay,
    });
  }
  if (offer.employment_type) {
    facts.push({
      label: t('offer.employmentType'),
      value: t(`offer.employmentTypes.${offer.employment_type}`),
    });
  }
  if (offer.start_date) {
    facts.push({
      label: t('offer.startDate'),
      value: new Date(offer.start_date).toLocaleDateString(locale, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      }),
    });
  }
  if (offer.location) facts.push({ label: t('offer.location'), value: offer.location });
  if (offer.probation_months != null) {
    facts.push({ label: t('offer.probation'), value: t('offer.months', { count: offer.probation_months }) });
  }
  if (offer.notice_period_days != null) {
    facts.push({ label: t('offer.noticePeriod'), value: t('offer.days', { count: offer.notice_period_days }) });
  }
  return (
    <>
      {hideTitle ? null : (
        <>
          <StatusTag tone="electric" dot>
            {t('offer.title')}
          </StatusTag>
          <h1 className="mt-3 text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground">
            {offer.job_title}
          </h1>
        </>
      )}
      {/* The company's name sits in the frame's header, so it is not repeated here. */}

      <DataList
        className={hideTitle ? undefined : 'mt-4 border-t border-border pt-4'}
        columns={2}
        items={facts}
      />

      {offer.bonus ? (
        <p className="mt-3 text-[14px] text-[var(--ui-soft)]">
          <span className="text-muted-foreground">{t('offer.bonus')}: </span>
          {offer.bonus}
        </p>
      ) : null}
      {offer.equity ? (
        <p className="mt-1 text-[14px] text-[var(--ui-soft)]">
          <span className="text-muted-foreground">{t('offer.equity')}: </span>
          {offer.equity}
        </p>
      ) : null}
      {offer.benefits ? (
        <p className="mt-3 text-[14px] leading-relaxed text-[var(--ui-soft)]">{offer.benefits}</p>
      ) : null}
      {offer.terms ? (
        <p className="mt-4 rounded-[12px] bg-[var(--ui-inset-soft)] p-4 text-[13.5px] leading-relaxed text-muted-foreground">
          {offer.terms}
        </p>
      ) : null}
    </>
  );
}

/** The offer as a sent offer is read: one raised region holding the facts. */
function OfferSummary({ offer, locale }: { offer: PublicOfferShape; locale: string }) {
  return (
    <GlassCard className="w-full p-5 sm:p-6">
      <OfferFacts offer={offer} locale={locale} />
    </GlassCard>
  );
}

/** Review -> Accept -> Joining documents. Real steps, driven by the offer's
 *  status and `preboarding_completed_at`. The compact line (below `sm`) names
 *  the step in words. Declined, expired and withdrawn offers draw no steps. */
function OfferSteps({ current }: { current: OfferStepKey }) {
  const { t } = useTranslation();
  const steps: { key: OfferStepKey; label: string }[] = [
    { key: 'review', label: t('public.offer.review') },
    { key: 'accept', label: t('public.offer.accept') },
    { key: 'documents', label: t('public.offer.documents') },
  ];
  const index = Math.max(0, steps.findIndex((s) => s.key === current));
  return (
    <Stepper
      steps={steps}
      current={current}
      label={t('public.offer.stepsLabel')}
      compactLabel={`${t('pageBlocks.stepOf', { n: index + 1, total: steps.length })} · ${steps[index].label}`}
    />
  );
}

/* ── Accept / decline, with the emailed code ─────────────────────────────── */

function AnswerPanel({
  token,
  purpose,
  onAnswered,
  onLocked,
  onCancel,
}: {
  token: string;
  purpose: CodePurpose;
  onAnswered: () => void;
  onLocked: (message: string) => void;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [codeSent, setCodeSent] = useState(false);
  const [minutes, setMinutes] = useState<number | null>(null);
  const [code, setCode] = useState('');
  const [fullName, setFullName] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const codeMut = useMutation({
    mutationFn: () => requestOfferCode(token, purpose),
    onSuccess: (res) => {
      setCodeSent(true);
      setMinutes(res.minutes);
      setError(null);
    },
    onError: (e: unknown) => {
      if (isLocked(e)) return onLocked(errText(e, ''));
      setError(errText(e, t('offer.errorGeneric')));
    },
  });

  const answerMut = useMutation({
    mutationFn: () =>
      purpose === 'accept'
        ? acceptOffer(token, code, fullName, pageLanguage(i18n.language))
        : declineOffer(token, code, reason || undefined),
    onSuccess: () => onAnswered(),
    onError: (e: unknown) => {
      if (isLocked(e)) return onLocked(errText(e, ''));
      setError(errText(e, t('offer.errorGeneric')));
    },
  });

  return (
    <GlassCard className="w-full p-5 sm:p-6">
      <h2 className="text-[17px] font-bold text-foreground">
        {purpose === 'accept' ? t('offer.accept') : t('offer.decline')}
      </h2>

      {!codeSent ? (
        <>
          <p className="mt-2 text-[14px] text-muted-foreground">
            {purpose === 'accept' ? t('offer.acceptCodeIntro') : t('offer.declineCodeIntro')}
          </p>
          <Button className="mt-4 h-12 w-full sm:w-auto" disabled={codeMut.isPending} onClick={() => codeMut.mutate()}>
            {codeMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            {t('offer.sendCode')}
          </Button>
        </>
      ) : (
        <>
          <p className="mt-2 text-[13px] text-[var(--ui-ok)]">
            {t('offer.codeSent', { minutes: minutes ?? '' })}
          </p>
          <div className="mt-4 flex flex-col gap-3">
            <label className={FIELD_LABEL}>
              {t('offer.codeLabel')}
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric" autoComplete="one-time-code"
                className={FIELD}
              />
            </label>
            {purpose === 'accept' ? (
              <label className={FIELD_LABEL}>
                {t('offer.fullNameLabel')}
                <input
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder={t('offer.fullNamePlaceholder')}
                  className={FIELD}
                />
              </label>
            ) : (
              <label className={FIELD_LABEL}>
                {t('offer.reasonLabel')}
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className={FIELD}
                />
              </label>
            )}
            {purpose === 'accept' ? (
              <>
                {/* What accepting agrees to (DPDP): said here, before they do it. */}
                <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                  {t('offer.acceptConsent')}
                </p>
                {/* PH5 wave 1 follow-up (D) — the 90-day check-in is HR-recorded,
                    aggregate-only, and never a decision; disclosed here for the
                    same reason acceptConsent is: before they accept, not after. */}
                <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                  {t('offer.checkinNotice')}
                </p>
              </>
            ) : null}
            <Button
              className="h-12 w-full"
              variant={purpose === 'accept' ? 'default' : 'destructive'}
              disabled={
                answerMut.isPending ||
                code.trim().length < 4 ||
                (purpose === 'accept' && fullName.trim().length < 2)
              }
              onClick={() => answerMut.mutate()}
            >
              {answerMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              {purpose === 'accept' ? t('offer.confirmAccept') : t('offer.confirmDecline')}
            </Button>
          </div>
        </>
      )}

      {error ? (
        <p role="alert" className="mt-3 text-[13px] text-[var(--ui-danger)]">
          {error}
        </p>
      ) : null}

      <button
        type="button"
        onClick={onCancel}
        className="mt-4 inline-flex min-h-11 items-center text-[14px] font-medium text-[var(--ui-soft)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        {t('offer.cancel')}
      </button>
    </GlassCard>
  );
}

/* ── Documents ────────────────────────────────────────────────────────────── */

/** The page's language as the server takes it: en, hi or te. */
function pageLanguage(lng: string | undefined): 'en' | 'hi' | 'te' {
  const two = (lng ?? 'en').slice(0, 2);
  return two === 'hi' || two === 'te' ? two : 'en';
}

function aadhaarLike(item: PublicChecklistItem): boolean {
  return item.doc_type === 'identity' || /aadhaar/i.test(item.name);
}

/** Matches the server (app/preboarding.py `upload`): a document can be sent
 *  when none is there yet, when HR rejected it or asked for another, or when a
 *  verified one has passed its expiry date. */
function canUpload(item: PublicChecklistItem): boolean {
  return (
    item.state === 'outstanding' ||
    item.state === 'rejected' ||
    item.state === 'replacement_requested' ||
    item.state === 'expired'
  );
}

function DocumentRow({
  token,
  session,
  item,
  onUploaded,
  onSessionExpired,
}: {
  token: string;
  session: string;
  item: PublicChecklistItem;
  onUploaded: () => void;
  onSessionExpired: () => void;
}) {
  const { t } = useTranslation();
  const [expiresOn, setExpiresOn] = useState('');
  const [error, setError] = useState<string | null>(null);

  const uploadMut = useMutation({
    mutationFn: (file: File) =>
      uploadMyDocument(token, session, item.requirement_id, file, expiresOn || undefined),
    onSuccess: () => {
      setError(null);
      onUploaded();
    },
    onError: (e: unknown) => {
      if (isUnauthorized(e)) return onSessionExpired();
      setError(errText(e, t('offer.documents.uploadError')));
    },
  });

  function pick(file: File | null): void {
    setError(null);
    if (!file) return;
    if (file.size > MAX_DOC_BYTES) {
      setError(t('offer.documents.tooLarge'));
      return;
    }
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError(t('offer.documents.wrongType'));
      return;
    }
    if (item.requires_expiry && !expiresOn) {
      setError(t('offer.documents.needExpiry'));
      return;
    }
    uploadMut.mutate(file);
  }

  return (
    <li role="group" aria-label={item.name} className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[14.5px] font-semibold text-foreground">{item.name}</p>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">
            {item.mandatory ? t('offer.documents.mandatory') : t('offer.documents.optional')}
          </p>
        </div>
        <StatusTag
          tone={
            item.state === 'verified'
              ? 'forest'
              : item.state === 'rejected' || item.state === 'expired'
                ? 'ember'
                : item.state === 'outstanding'
                  ? 'neutral'
                  : 'amber'
          }
          dot
        >
          {t(`docs.state.${item.state}`)}
        </StatusTag>
      </div>

      {item.description ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">{item.description}</p>
      ) : null}

      {item.state === 'expired' ? (
        <p className="mt-2 text-[13px] text-[var(--ui-danger)]">{t('offer.documents.expiredHint')}</p>
      ) : null}

      {aadhaarLike(item) ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-[12px] bg-[var(--ui-warn-wash)] p-2.5 text-[12px] text-[var(--ui-soft)]">
          <AlertCircle
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--ui-warn)]"
            aria-hidden="true"
          />
          {t('offer.documents.aadhaarNotice')}
        </p>
      ) : null}

      {item.document?.review_note &&
      (item.state === 'rejected' || item.state === 'replacement_requested') ? (
        <p className="mt-2 rounded-[12px] bg-[var(--ui-inset-soft)] p-2.5 text-[12.5px] text-[var(--ui-soft)]">
          {t('offer.documents.rejectedReason', { reason: item.document.review_note })}
        </p>
      ) : null}

      {canUpload(item) ? (
        <div className="mt-3 flex flex-col gap-2">
          {item.requires_expiry ? (
            <label className={FIELD_LABEL}>
              {t('offer.documents.expiryLabel')}
              <input
                type="date"
                value={expiresOn}
                onChange={(e) => setExpiresOn(e.target.value)}
                min={new Date(Date.now() + 86_400_000).toISOString().slice(0, 10)}
                className="mt-1 flex min-h-11 rounded-[12px] border border-input bg-card px-3 text-base text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20 md:text-sm"
              />
            </label>
          ) : null}
          <label className="inline-flex min-h-11 w-fit cursor-pointer items-center gap-2 rounded-full border border-[var(--ui-line-strong)] px-4 py-2 text-[13.5px] font-medium text-foreground hover:bg-[var(--ui-inset)] focus-within:ring-2 focus-within:ring-[var(--accent)]">
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            {uploadMut.isPending
              ? t('offer.documents.uploading')
              : item.state === 'outstanding'
                ? t('offer.documents.upload')
                : t('offer.documents.reupload')}
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png"
              className="sr-only"
              disabled={uploadMut.isPending}
              onChange={(e) => pick(e.target.files?.[0] ?? null)}
            />
          </label>
          <p className="text-[12px] text-[var(--ui-soft)]">{t('offer.documents.emailNotice')}</p>
        </div>
      ) : null}

      {error ? <p className="mt-2 text-[13px] text-[var(--ui-danger)]">{error}</p> : null}
    </li>
  );
}

function DocumentsSection({ token, offer }: { token: string; offer: PublicOfferShape }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [session, setSession] = useState<string | null>(null);
  const [codeSent, setCodeSent] = useState(false);
  const [minutes, setMinutes] = useState<number | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [withdrawn, setWithdrawn] = useState(false);

  const withdrawMut = useMutation({
    mutationFn: () => withdrawDocumentsConsent(token, session as string),
    onSuccess: () => {
      setWithdrawn(true);
      setAsking(false);
      void qc.invalidateQueries({ queryKey: ['public-offer-documents', token, session] });
    },
    onError: (e: unknown) => setError(errText(e, t('offer.errorGeneric'))),
  });

  const codeMut = useMutation({
    mutationFn: () => requestDocumentsCode(token),
    onSuccess: (res) => {
      setCodeSent(true);
      setMinutes(res.minutes);
      setError(null);
    },
    onError: (e: unknown) => setError(errText(e, t('offer.errorGeneric'))),
  });

  const sessionMut = useMutation({
    mutationFn: () => openDocumentsSession(token, code),
    onSuccess: (res) => {
      // In memory ONLY — never localStorage, sessionStorage, or a URL.
      setSession(res.session_token);
      setError(null);
    },
    onError: (e: unknown) => setError(errText(e, t('offer.errorGeneric'))),
  });

  const checklist = useQuery({
    queryKey: ['public-offer-documents', token, session],
    queryFn: () => getMyDocuments(token, session as string),
    enabled: Boolean(session),
    retry: false,
  });

  function sessionExpired(): void {
    setSession(null);
    setCodeSent(false);
    setCode('');
    setError(t('offer.documents.sessionExpired'));
  }

  useEffect(() => {
    if (checklist.isError && isUnauthorized(checklist.error)) sessionExpired();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checklist.isError]);

  if (offer.preboarding_completed_at) {
    return (
      <GlassCard className="w-full p-5 sm:p-6">
        <h2 className="flex items-center gap-2 text-[17px] font-bold text-foreground">
          <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
          {t('offer.documents.heading')}
        </h2>
        <p className="mt-2 text-[13px] text-muted-foreground">{t('offer.documents.complete')}</p>
      </GlassCard>
    );
  }

  const remaining =
    checklist.data && checklist.data.items.length > 0 ? documentsRemaining(checklist.data) : null;

  return (
    <GlassCard className="w-full p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[17px] font-bold text-foreground">{t('offer.documents.heading')}</h2>
        {remaining ? (
          <p data-testid="docs-remaining" className="text-[13.5px] font-semibold text-foreground">
            {remaining.n > 0
              ? t('docs.remaining', { n: remaining.n, total: remaining.total })
              : t('docs.allVerified')}
          </p>
        ) : null}
      </div>
      <p className="mt-1 text-[13px] text-muted-foreground">{t('offer.documents.intro')}</p>
      {remaining && remaining.n > 0 && remaining.inReview > 0 ? (
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          {t('docs.inReview', { count: remaining.inReview })}
        </p>
      ) : null}

      {!session ? (
        <div className="mt-4">
          {/* Why a second code: documents have their own, separate from the
              one that confirmed the offer. Said once, before they are asked. */}
          <p className="mb-3 text-[13px] text-muted-foreground">{t('docs.whyAgain')}</p>
          {!codeSent ? (
            <>
              <p className="text-[13px] text-muted-foreground">{t('offer.documents.codeIntro')}</p>
              <Button className="mt-3 h-12 w-full sm:w-auto" disabled={codeMut.isPending} onClick={() => codeMut.mutate()}>
                {codeMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                {t('offer.documents.getCode')}
              </Button>
            </>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-[13px] text-[var(--ui-ok)]">
                {t('offer.codeSent', { minutes: minutes ?? '' })}
              </p>
              <label className={FIELD_LABEL}>
                {t('offer.codeLabel')}
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  inputMode="numeric" autoComplete="one-time-code"
                  className={FIELD}
                />
              </label>
              <Button
                className="h-12 w-full sm:w-auto"
                disabled={sessionMut.isPending || code.trim().length < 4}
                onClick={() => sessionMut.mutate()}
              >
                {sessionMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                {t('offer.documents.openChecklist')}
              </Button>
            </div>
          )}
          {error ? (
            <p role="alert" className="mt-3 text-[13px] text-[var(--ui-danger)]">
              {error}
            </p>
          ) : null}
        </div>
      ) : checklist.isLoading ? (
        <p className="mt-4 text-[13px] text-muted-foreground">{t('offer.documents.loading')}</p>
      ) : checklist.isError ? (
        <p className="mt-4 text-[13px] text-[var(--ui-danger)]">
          {isUnauthorized(checklist.error)
            ? t('offer.documents.sessionExpired')
            : errText(checklist.error, t('offer.documents.loadError'))}
        </p>
      ) : (checklist.data?.items ?? []).length === 0 ? (
        <p className="mt-4 text-[13px] text-muted-foreground">{t('offer.documents.none')}</p>
      ) : (
        <ul className="mt-4 divide-y divide-border">
          {(checklist.data?.items ?? []).map((item) => (
            <DocumentRow
              key={item.requirement_id}
              token={token}
              session={session}
              item={item}
              onUploaded={() =>
                void qc.invalidateQueries({ queryKey: ['public-offer-documents', token, session] })
              }
              onSessionExpired={sessionExpired}
            />
          ))}
        </ul>
      )}

      {session && !checklist.isLoading && !checklist.isError ? (
        <div className="mt-5 border-t border-border pt-4">
          {withdrawn ? (
            <p className="text-[12.5px] text-muted-foreground">
              {t('offer.documents.withdrawnDone')}
            </p>
          ) : asking ? (
            <div className="flex flex-col gap-2">
              <p className="text-[14px] text-foreground">
                {t('offer.documents.withdrawConfirm')}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={withdrawMut.isPending}
                  onClick={() => withdrawMut.mutate()}
                  className="h-11 border-[var(--ui-danger)] text-[var(--ui-danger)] hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]"
                >
                  {t('offer.documents.withdrawYes')}
                </Button>
                <Button type="button" variant="ghost" className="h-11" onClick={() => setAsking(false)}>
                  {t('offer.cancel')}
                </Button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setAsking(true)}
              className="inline-flex min-h-11 items-center text-[13.5px] font-medium text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {t('offer.documents.withdraw')}
            </button>
          )}
        </div>
      ) : null}
    </GlassCard>
  );
}

/* ── The offer itself, by status ──────────────────────────────────────────── */

function OfferBody({
  token,
  offer,
  locale,
  onAnswered,
  onLocked,
}: {
  token: string;
  offer: PublicOfferShape;
  locale: string;
  onAnswered: () => void;
  onLocked: (message: string) => void;
}) {
  const { t } = useTranslation();
  const [answering, setAnswering] = useState<CodePurpose | null>(null);

  if (offer.status === 'expired') {
    return (
      <CenteredMessage
        icon={<AlertCircle className="h-6 w-6" aria-hidden="true" />}
        title={t('offer.expiredTitle')}
        desc={t('offer.expiredDesc')}
      />
    );
  }
  if (offer.status === 'withdrawn') {
    return (
      <CenteredMessage
        icon={<AlertCircle className="h-6 w-6" aria-hidden="true" />}
        title={t('offer.withdrawnTitle')}
        desc={t('offer.withdrawnDesc')}
      />
    );
  }
  if (offer.status === 'declined') {
    return (
      <CenteredMessage
        icon={<CheckCircle2 className="h-6 w-6" aria-hidden="true" />}
        title={t('offer.declinedTitle')}
        desc={t('offer.declinedDesc')}
        tone="ok"
      />
    );
  }

  if (offer.status === 'accepted') {
    const step = offerStep(offer.status, offer.preboarding_completed_at);
    return (
      <>
        {step ? <OfferSteps current={step.current} /> : null}
        {/* Once accepted the offer collapses to one row; the documents lead. */}
        <section
          aria-label={t('public.offer.accepted')}
          className="rounded-card border border-border bg-card px-4 py-3"
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <h1 className="text-[17px] font-bold text-foreground">{t('public.offer.accepted')}</h1>
            <p className="text-[14px] text-muted-foreground">{offer.job_title}</p>
          </div>
          <p className="mt-0.5 text-[13px] text-muted-foreground">{t('offer.acceptedDesc')}</p>
          <details className="mt-2">
            <summary className="inline-flex min-h-[44px] cursor-pointer items-center rounded-[6px] text-[13.5px] font-medium text-[var(--ui-info)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
              {t('public.offer.viewOffer')}
            </summary>
            <div className="border-t border-border pt-4">
              <OfferFacts offer={offer} locale={locale} hideTitle />
            </div>
          </details>
        </section>
        <DocumentsSection token={token} offer={offer} />
        <WhatNext title={t('public.whatNext')}>
          <p>
            {step?.complete ? t('public.offer.nextDone') : t('public.offer.nextDocs')}
          </p>
        </WhatNext>
      </>
    );
  }

  // 'sent' — the only state left, and the only one an answer can still change.
  const sentStep = offerStep(offer.status, offer.preboarding_completed_at, answering);
  return (
    <>
      {sentStep ? <OfferSteps current={sentStep.current} /> : null}
      <OfferSummary offer={offer} locale={locale} />
      {offer.expires_at ? (
        <p className="text-[13.5px] font-medium text-[var(--ui-soft)]">
          {t('offer.expiresOn', { date: new Date(offer.expires_at).toLocaleDateString(locale) })}
        </p>
      ) : null}

      {answering ? (
        <AnswerPanel
          token={token}
          purpose={answering}
          onAnswered={onAnswered}
          onLocked={onLocked}
          onCancel={() => setAnswering(null)}
        />
      ) : (
        <div className="flex w-full flex-col gap-3 sm:flex-row">
          <Button className="h-12 flex-1 text-[15px]" onClick={() => setAnswering('accept')}>
            {t('offer.accept')}
          </Button>
          <Button variant="outline" className="h-12 flex-1 text-[15px]" onClick={() => setAnswering('decline')}>
            {t('offer.decline')}
          </Button>
        </div>
      )}
    </>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function PublicOffer(): JSX.Element {
  const { t, i18n } = useTranslation();
  const locale = localeFor(i18n.language);

  // SECURITY: token from the URL #fragment — never a path param or query
  // string. Read once, then the fragment is stripped from the address bar so
  // it never lingers in browser history; kept for this tab only (see header).
  const token = useFragmentToken(FRAGMENT_TOKEN_KEYS.offer);

  const [locked, setLocked] = useState<string | null>(null);

  const view = useQuery({
    queryKey: ['public-offer', token],
    queryFn: () => viewOffer(token),
    enabled: token.length > 0,
    retry: (failureCount, error) => isTransientApiError(error) && failureCount < 2,
    staleTime: 0,
  });

  // A link that can do nothing more is a spent credential: drop it from this
  // tab's storage. An accepted offer is NOT terminal, since its documents step
  // still needs the token.
  const spent =
    locked !== null ||
    (view.isError && !isTransientApiError(view.error)) ||
    TERMINAL_OFFER_STATUSES.has(view.data?.status ?? '');
  useEffect(() => {
    if (spent) forgetFragmentToken(FRAGMENT_TOKEN_KEYS.offer);
  }, [spent]);

  if (locked) {
    return (
      <PageWrap company={view.data?.company}>
        <CenteredMessage
          icon={<Lock className="h-6 w-6" aria-hidden="true" />}
          title={t('offer.lockedTitle')}
          desc={locked}
          tone="danger"
        />
      </PageWrap>
    );
  }

  if (token && view.isError && isTransientApiError(view.error)) {
    return (
      <PageWrap>
        <CenteredMessage
          icon={<AlertCircle className="h-6 w-6" aria-hidden="true" />}
          title={t('offer.networkTitle')}
          desc={t('offer.networkDesc')}
        />
        <Button className="h-12 w-full" onClick={() => void view.refetch()}>
          {t('offer.retry')}
        </Button>
      </PageWrap>
    );
  }

  if (!token || view.isError) {
    return (
      <PageWrap>
        <CenteredMessage
          icon={<AlertCircle className="h-6 w-6" aria-hidden="true" />}
          title={t('offer.invalidTitle')}
          desc={t('offer.invalidDesc')}
        />
        <p className="text-center text-[13px] text-[var(--ui-soft)]">{t('publicFrame.reopenEmail')}</p>
      </PageWrap>
    );
  }

  if (view.isLoading || !view.data) {
    return (
      <PageWrap>
        <div role="status" className="flex flex-col items-center gap-3 py-16">
          <Loader2 className="h-8 w-8 animate-spin text-[var(--accent)]" aria-hidden="true" />
          <p className="text-[14px] text-muted-foreground">{t('offer.loading')}</p>
        </div>
      </PageWrap>
    );
  }

  return (
    <PageWrap company={view.data.company}>
      <OfferBody
        token={token}
        offer={view.data}
        locale={locale}
        onAnswered={() => void view.refetch()}
        onLocked={(message) => setLocked(message || t('offer.lockedDesc'))}
      />
    </PageWrap>
  );
}
