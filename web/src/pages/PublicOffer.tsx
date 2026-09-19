// PublicOffer — the candidate's own offer, reached with no login from the
// emailed link. EN/HI/TE (candidate-facing, per CLAUDE.md) with a language
// switch, like the other public pages.
//
// SECURITY (this page was the subject of a security review):
// - The token comes from the URL #FRAGMENT, never a path param or query
//   string — read once into state, then stripped from the address bar with
//   history.replaceState so it never lingers in browser history. It lives in
//   memory for the rest of the page's life and is sent as the X-Offer-Token
//   header on every /offer call (see api/publicOffer.ts) — never a URL.
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
  openDocumentsSession,
  requestDocumentsCode,
  requestOfferCode,
  uploadMyDocument,
  viewOffer,
  type CodePurpose,
  type PublicChecklistItem,
  type PublicOffer as PublicOfferShape,
} from '@/api/publicOffer';
import { AuroraField } from '@/design/components/AuroraField';
import { GlassCard, Pill, StatusTag } from '@/design/components/primitives';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { AlertCircle, CheckCircle2, Loader2, Lock, Upload } from '@/design/components/icons';

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

function PageWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col items-center bg-midnight px-6 py-12 font-sans text-foreground">
      <AuroraField />
      <div className="absolute left-6 top-6 z-10 flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[linear-gradient(135deg,#112d72,#a887dc)]">
          <span className="h-2.5 w-2.5 rounded-full bg-primary" />
        </span>
        <span className="text-[15px] font-semibold text-foreground">AntHire</span>
      </div>
      <div className="absolute right-6 top-6 z-10">
        <LanguageSwitcher />
      </div>
      <div className="relative z-10 mt-16 flex w-full max-w-[560px] flex-1 flex-col items-center gap-4">
        {children}
      </div>
    </div>
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
  const bg =
    tone === 'ok'
      ? 'bg-[rgba(39,201,63,0.15)] text-vivid-mint'
      : tone === 'danger'
        ? 'bg-[rgba(230,113,79,0.15)] text-ember'
        : 'bg-[rgba(255,183,100,0.15)] text-amber-glow';
  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <span className={`inline-flex h-12 w-12 items-center justify-center rounded-[9px] ${bg}`}>
        {icon}
      </span>
      <h1 className="text-[20px] font-semibold text-foreground">{title}</h1>
      <p className="max-w-sm text-[14px] text-muted-foreground">{desc}</p>
    </div>
  );
}

/* ── Offer summary ────────────────────────────────────────────────────────── */

function OfferSummary({ offer, locale }: { offer: PublicOfferShape; locale: string }) {
  const { t } = useTranslation();
  const pay = money(offer, locale);
  return (
    <GlassCard className="w-full p-6">
      <StatusTag tone="electric" dot>
        {t('offer.title')}
      </StatusTag>
      <h1 className="mt-3 text-[24px] font-semibold tracking-[-0.6px] text-foreground">
        {offer.job_title}
      </h1>
      {offer.company ? (
        <p className="mt-1 text-[13.5px] text-muted-foreground">{offer.company}</p>
      ) : null}

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5 text-[13px]">
        {pay ? (
          <>
            <dt className="text-muted-foreground">{t('offer.compensation')}</dt>
            <dd className="text-foreground">
              {pay}
              {offer.pay_period ? ` / ${offer.pay_period}` : ''}
            </dd>
          </>
        ) : null}
        {offer.employment_type ? (
          <>
            <dt className="text-muted-foreground">{t('offer.employmentType')}</dt>
            <dd className="text-foreground">
              {t(`offer.employmentTypes.${offer.employment_type}`)}
            </dd>
          </>
        ) : null}
        {offer.start_date ? (
          <>
            <dt className="text-muted-foreground">{t('offer.startDate')}</dt>
            <dd className="text-foreground">
              {new Date(offer.start_date).toLocaleDateString(locale, {
                year: 'numeric',
                month: 'long',
                day: 'numeric',
              })}
            </dd>
          </>
        ) : null}
        {offer.location ? (
          <>
            <dt className="text-muted-foreground">{t('offer.location')}</dt>
            <dd className="text-foreground">{offer.location}</dd>
          </>
        ) : null}
        {offer.probation_months != null ? (
          <>
            <dt className="text-muted-foreground">{t('offer.probation')}</dt>
            <dd className="text-foreground">
              {t('offer.months', { count: offer.probation_months })}
            </dd>
          </>
        ) : null}
        {offer.notice_period_days != null ? (
          <>
            <dt className="text-muted-foreground">{t('offer.noticePeriod')}</dt>
            <dd className="text-foreground">
              {t('offer.days', { count: offer.notice_period_days })}
            </dd>
          </>
        ) : null}
      </dl>

      {offer.bonus ? (
        <p className="mt-3 text-[13px] text-[var(--ui-soft)]">
          <span className="text-muted-foreground">{t('offer.bonus')}: </span>
          {offer.bonus}
        </p>
      ) : null}
      {offer.equity ? (
        <p className="mt-1 text-[13px] text-[var(--ui-soft)]">
          <span className="text-muted-foreground">{t('offer.equity')}: </span>
          {offer.equity}
        </p>
      ) : null}
      {offer.benefits ? (
        <p className="mt-3 text-[13px] leading-relaxed text-[var(--ui-soft)]">{offer.benefits}</p>
      ) : null}
      {offer.terms ? (
        <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">{offer.terms}</p>
      ) : null}
    </GlassCard>
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
    <GlassCard className="w-full p-6">
      <h2 className="text-[16px] font-semibold text-foreground">
        {purpose === 'accept' ? t('offer.accept') : t('offer.decline')}
      </h2>

      {!codeSent ? (
        <>
          <p className="mt-2 text-[13px] text-muted-foreground">
            {purpose === 'accept' ? t('offer.acceptCodeIntro') : t('offer.declineCodeIntro')}
          </p>
          <Pill className="mt-4" disabled={codeMut.isPending} onClick={() => codeMut.mutate()}>
            {codeMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            {t('offer.sendCode')}
          </Pill>
        </>
      ) : (
        <>
          <p className="mt-2 text-[13px] text-[var(--ui-ok)]">
            {t('offer.codeSent', { minutes: minutes ?? '' })}
          </p>
          <div className="mt-4 flex flex-col gap-3">
            <label className="block text-[12.5px] font-medium text-[var(--ui-soft)]">
              {t('offer.codeLabel')}
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                className="mt-1.5 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[14px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </label>
            {purpose === 'accept' ? (
              <label className="block text-[12.5px] font-medium text-[var(--ui-soft)]">
                {t('offer.fullNameLabel')}
                <input
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder={t('offer.fullNamePlaceholder')}
                  className="mt-1.5 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[14px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
            ) : (
              <label className="block text-[12.5px] font-medium text-[var(--ui-soft)]">
                {t('offer.reasonLabel')}
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="mt-1.5 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[14px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
            )}
            {purpose === 'accept' ? (
              // What accepting agrees to (DPDP): said here, before they do it.
              <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                {t('offer.acceptConsent')}
              </p>
            ) : null}
            <Pill
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
            </Pill>
          </div>
        </>
      )}

      {error ? (
        <p role="alert" className="mt-3 text-[13px] text-ember">
          {error}
        </p>
      ) : null}

      <button
        type="button"
        onClick={onCancel}
        className="mt-4 text-[12.5px] text-muted-foreground hover:text-foreground"
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
    <li role="group" aria-label={item.name} className="rounded-[12px] border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[13.5px] font-medium text-foreground">{item.name}</p>
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">
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
          {t(`offer.documents.state.${item.state}`)}
        </StatusTag>
      </div>

      {item.description ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">{item.description}</p>
      ) : null}

      {item.state === 'expired' ? (
        <p className="mt-2 text-[12px] text-ember">{t('offer.documents.expiredHint')}</p>
      ) : null}

      {aadhaarLike(item) ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-[8px] border border-[var(--ui-warn)]/30 bg-[rgba(255,183,100,0.08)] p-2.5 text-[12px] text-[var(--ui-soft)]">
          <AlertCircle
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--ui-warn)]"
            aria-hidden="true"
          />
          {t('offer.documents.aadhaarNotice')}
        </p>
      ) : null}

      {item.document?.review_note &&
      (item.state === 'rejected' || item.state === 'replacement_requested') ? (
        <p className="mt-2 rounded-[8px] border border-border bg-[var(--ui-inset-soft)] p-2.5 text-[12px] text-[var(--ui-soft)]">
          {t('offer.documents.rejectedReason', { reason: item.document.review_note })}
        </p>
      ) : null}

      {canUpload(item) ? (
        <div className="mt-3 flex flex-col gap-2">
          {item.requires_expiry ? (
            <label className="block text-[12px] font-medium text-[var(--ui-soft)]">
              {t('offer.documents.expiryLabel')}
              <input
                type="date"
                value={expiresOn}
                onChange={(e) => setExpiresOn(e.target.value)}
                min={new Date(Date.now() + 86_400_000).toISOString().slice(0, 10)}
                className="mt-1 block rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </label>
          ) : null}
          <label className="inline-flex w-fit cursor-pointer items-center gap-2 rounded-[9px] border border-[var(--ui-line-strong)] px-3 py-1.5 text-[12.5px] text-foreground hover:border-[var(--accent)]/60">
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
          <p className="text-[11px] text-[var(--ui-faint)]">{t('offer.documents.emailNotice')}</p>
        </div>
      ) : null}

      {error ? <p className="mt-2 text-[12px] text-ember">{error}</p> : null}
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
      <GlassCard className="w-full p-6">
        <h2 className="flex items-center gap-2 text-[16px] font-semibold text-foreground">
          <CheckCircle2 className="h-4 w-4 text-vivid-mint" aria-hidden="true" />
          {t('offer.documents.heading')}
        </h2>
        <p className="mt-2 text-[13px] text-muted-foreground">{t('offer.documents.complete')}</p>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="w-full p-6">
      <h2 className="text-[16px] font-semibold text-foreground">{t('offer.documents.heading')}</h2>
      <p className="mt-1 text-[13px] text-muted-foreground">{t('offer.documents.intro')}</p>

      {!session ? (
        <div className="mt-4">
          {!codeSent ? (
            <>
              <p className="text-[13px] text-muted-foreground">{t('offer.documents.codeIntro')}</p>
              <Pill className="mt-3" disabled={codeMut.isPending} onClick={() => codeMut.mutate()}>
                {codeMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                {t('offer.documents.getCode')}
              </Pill>
            </>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-[13px] text-[var(--ui-ok)]">
                {t('offer.codeSent', { minutes: minutes ?? '' })}
              </p>
              <label className="block text-[12.5px] font-medium text-[var(--ui-soft)]">
                {t('offer.codeLabel')}
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  inputMode="numeric"
                  className="mt-1.5 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[14px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
              <Pill
                disabled={sessionMut.isPending || code.trim().length < 4}
                onClick={() => sessionMut.mutate()}
              >
                {sessionMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                {t('offer.documents.openChecklist')}
              </Pill>
            </div>
          )}
          {error ? (
            <p role="alert" className="mt-3 text-[13px] text-ember">
              {error}
            </p>
          ) : null}
        </div>
      ) : checklist.isLoading ? (
        <p className="mt-4 text-[13px] text-muted-foreground">{t('offer.documents.loading')}</p>
      ) : checklist.isError ? (
        <p className="mt-4 text-[13px] text-ember">
          {isUnauthorized(checklist.error)
            ? t('offer.documents.sessionExpired')
            : errText(checklist.error, t('offer.documents.loadError'))}
        </p>
      ) : (checklist.data?.items ?? []).length === 0 ? (
        <p className="mt-4 text-[13px] text-muted-foreground">{t('offer.documents.none')}</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-3">
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
    return (
      <>
        <OfferSummary offer={offer} locale={locale} />
        <CenteredMessage
          icon={<CheckCircle2 className="h-6 w-6" aria-hidden="true" />}
          title={t('offer.acceptedTitle')}
          desc={t('offer.acceptedDesc')}
          tone="ok"
        />
        <DocumentsSection token={token} offer={offer} />
      </>
    );
  }

  // 'sent' — the only state left, and the only one an answer can still change.
  return (
    <>
      <OfferSummary offer={offer} locale={locale} />
      {offer.expires_at ? (
        <p className="text-[12.5px] text-muted-foreground">
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
        <div className="flex w-full flex-wrap gap-3">
          <Pill className="flex-1" onClick={() => setAnswering('accept')}>
            {t('offer.accept')}
          </Pill>
          <Pill variant="outline" className="flex-1" onClick={() => setAnswering('decline')}>
            {t('offer.decline')}
          </Pill>
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
  // it never lingers in browser history; the token itself lives only in this
  // component's memory for the rest of the page's life.
  const [token] = useState(() => window.location.hash.replace(/^#/, '').trim());
  useEffect(() => {
    if (token) window.history.replaceState(null, '', window.location.pathname);
  }, [token]);

  const [locked, setLocked] = useState<string | null>(null);

  const view = useQuery({
    queryKey: ['public-offer', token],
    queryFn: () => viewOffer(token),
    enabled: token.length > 0,
    retry: (failureCount, error) => isTransientApiError(error) && failureCount < 2,
    staleTime: 0,
  });

  if (locked) {
    return (
      <PageWrap>
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
        <Pill onClick={() => void view.refetch()}>{t('offer.retry')}</Pill>
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
      </PageWrap>
    );
  }

  if (view.isLoading || !view.data) {
    return (
      <PageWrap>
        <Loader2 className="h-8 w-8 animate-spin text-electric" aria-hidden="true" />
        <p className="text-[13px] text-muted-foreground">{t('offer.loading')}</p>
      </PageWrap>
    );
  }

  return (
    <PageWrap>
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
