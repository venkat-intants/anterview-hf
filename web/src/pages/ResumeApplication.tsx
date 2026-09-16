// Pick up an application you saved — PH3-B4c, with PH3-B5's confirmation step.
//
// Reached from a link the candidate keeps. There is no login: the token is the
// only credential, which is why this page shows nothing until the server has
// said it opens something, and why an invalid or expired one gets a single
// uniform message rather than a diagnosis.
//
// The token rides in the URL FRAGMENT and is sent in the X-Draft-Token header.
// A fragment never reaches a server, so the credential stays out of access
// logs, edge logs and cross-origin Referer headers — the same rule /exam and
// /interview-invite follow.
//
// WHY CONSENT IS NOT ASKED FOR HERE
// It was taken when the draft was created — that was the first moment this
// person's email was stored, and the server refuses a draft without it. Asking
// again would imply the first answer had not counted.
//
// THE CONFIRMATION STEP IS THE POINT OF THE PAGE
// What the CV parser read is shown as editable values, not as a verdict. The
// candidate's correction wins: a name read out of a PDF is a guess, and theirs
// is not. A field the parser could not produce renders as an empty box and
// never blocks anything — the only thing required here is what the application
// itself requires.

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  confirmDraft,
  deleteDraft,
  getDraft,
  saveDraft,
  submitDraft,
  uploadDraftResume,
  type ApplicationDraft,
  type DraftFields,
} from '@/api/publicApply';
import { GlassCard, Pill } from '@/design/components/primitives';
import { AlertCircle, CheckCircle2, Loader2, Upload } from '@/design/components/icons';

const INPUT =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[14px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none';
const LABEL = 'mb-1.5 block text-[12.5px] font-medium text-[#b8babf]';
const MAX_BYTES = 5 * 1024 * 1024;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** Whether the parser actually produced anything worth showing. */
function parsedAnything(draft: ApplicationDraft): boolean {
  return Boolean(draft.parsed.full_name || draft.parsed.email);
}

export default function ResumeApplication(): JSX.Element {
  // SECURITY: the token comes from window.location.hash — NOT from the path.
  // A fragment is never sent to a server, so the credential stays out of access
  // logs, edge logs and any cross-origin Referer. Same rule PublicExam and
  // InterviewInvite follow; read once so a later navigation cannot swap it.
  const [token] = useState(() => window.location.hash.replace(/^#/, '').trim());
  const { t } = useTranslation();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<DraftFields>({});
  const [fileError, setFileError] = useState('');
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [discarded, setDiscarded] = useState(false);

  const draft = useQuery({
    queryKey: ['apply', 'draft', token],
    queryFn: () => getDraft(token),
    enabled: Boolean(token),
    retry: false,
  });

  // Seed the form from the draft when the SERVER'S VALUES CHANGE, rather than
  // on every new `draft.data` object identity.
  //
  // Be precise about what this does and does not fix, because the first version
  // of this comment overclaimed. React Query's structural sharing means a
  // refetch returning deeply-equal data hands back the SAME reference, and
  // DraftOut carries no per-fetch-changing field (expires_at is fixed at
  // creation), so the app-wide `refetchOnWindowFocus: true` in main.tsx does
  // NOT currently discard a candidate's typing. I asserted that it did; it does
  // not, and the test written to prove it passed with this guard removed.
  //
  // What this is, then: making the seed idempotent rather than
  // identity-triggered. It costs one string compare and removes a whole class
  // of "some upstream change churns the object and the form silently resets",
  // on the one screen whose entire purpose is letting somebody correct what the
  // parser got wrong. The behaviour that matters is preserved — after an upload
  // re-parses the CV or a save normalises a value, the payload really differs,
  // so the new reading still lands.
  //
  // Prompted by a genuinely flaky test: the same commit passed one CI run and
  // failed the next. That flake is NOT root-caused, and this guard is not
  // claimed to fix it — see the test's own note.
  const seeded = useRef<string>('');
  useEffect(() => {
    if (!draft.data) return;
    const d = draft.data;
    const signature = JSON.stringify([
      d.full_name,
      d.parsed.full_name,
      d.phone,
      d.years_experience,
      d.current_company,
      d.current_title,
      d.linkedin_url,
      d.github_url,
    ]);
    if (signature === seeded.current) return;
    seeded.current = signature;
    setForm({
      // The parser's reading is the STARTING POINT, not the answer: it fills
      // the box only where the candidate has not already given us something.
      full_name: d.full_name ?? d.parsed.full_name ?? '',
      phone: d.phone ?? '',
      years_experience: d.years_experience,
      current_company: d.current_company ?? '',
      current_title: d.current_title ?? '',
      linkedin_url: d.linkedin_url ?? '',
      github_url: d.github_url ?? '',
    });
  }, [draft.data]);

  function refresh(): void {
    void client.invalidateQueries({ queryKey: ['apply', 'draft', token] });
  }

  const save = useMutation({
    mutationFn: () => saveDraft(token, form),
    onSuccess: () => refresh(),
  });

  const confirm = useMutation({
    mutationFn: () => confirmDraft(token, form),
    onSuccess: () => refresh(),
  });

  const upload = useMutation({
    mutationFn: (file: File) => uploadDraftResume(token, file),
    onSuccess: () => {
      setFileError('');
      refresh();
    },
    onError: (e: unknown) => setFileError(errText(e, t('resumeApply.errUpload'))),
  });

  const send = useMutation({
    mutationFn: () => submitDraft(token),
    onSuccess: (r) => setSubmitted(r.message),
  });

  // DPDP gives a data principal the right to ACT, not merely to be forgotten on
  // a schedule. Without a control here the right existed in the API and not in
  // the product — and the guest account this draft hangs off has no password,
  // so there is nothing to sign in to and ask from.
  const discard = useMutation({
    mutationFn: () => deleteDraft(token),
    onSuccess: () => setDiscarded(true),
  });

  function set<K extends keyof DraftFields>(key: K, value: DraftFields[K]): void {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function pickFile(file: File | null): void {
    setFileError('');
    if (!file) return;
    // Checked here as well as server-side so nobody uploads 30 MB over a phone
    // connection to be told no at the end of it.
    if (file.size > MAX_BYTES) {
      setFileError(t('resumeApply.errTooBig'));
      return;
    }
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setFileError(t('resumeApply.errNotPdf'));
      return;
    }
    upload.mutate(file);
  }

  // ── States before the form ───────────────────────────────────────────
  if (draft.isLoading) {
    return (
      <Shell>
        <p className="flex items-center gap-2 text-[14px] text-[#888b91]">
          <Loader2 size={15} aria-hidden="true" className="animate-spin" />
          {t('resumeApply.loading')}
        </p>
      </Shell>
    );
  }

  if (draft.isError || !draft.data) {
    // One message for expired, submitted, revoked and never-existed. Telling
    // them apart would be a free oracle on a page anyone can reach.
    return (
      <Shell>
        <GlassCard className="p-6">
          <h1 className="text-[17px] font-semibold text-white">
            {t('resumeApply.deadLinkTitle')}
          </h1>
          <p className="mt-2 text-[13.5px] text-[#b8babf]">{t('resumeApply.deadLinkDesc')}</p>
        </GlassCard>
      </Shell>
    );
  }

  if (discarded) {
    return (
      <Shell>
        <GlassCard className="p-6 text-center">
          <CheckCircle2 size={28} aria-hidden="true" className="mx-auto text-[#6fbf8d]" />
          <h1 className="mt-3 text-[18px] font-semibold text-white">
            {t('resumeApply.deletedTitle')}
          </h1>
          <p className="mt-2 text-[14px] text-[#b8babf]">{t('resumeApply.deletedDesc')}</p>
        </GlassCard>
      </Shell>
    );
  }

  if (submitted) {
    return (
      <Shell>
        <GlassCard className="p-6 text-center">
          <CheckCircle2 size={28} aria-hidden="true" className="mx-auto text-[#6fbf8d]" />
          <h1 className="mt-3 text-[18px] font-semibold text-white">
            {draft.data.title}
          </h1>
          <p className="mt-2 text-[14px] text-[#b8babf]">{submitted}</p>
        </GlassCard>
      </Shell>
    );
  }

  const d = draft.data;
  const expires = new Date(d.expires_at);

  return (
    <Shell>
      <header className="mb-5">
        <h1 className="text-[20px] font-semibold text-white">{d.title}</h1>
        <p className="mt-0.5 text-[13.5px] text-[#888b91]">
          {t('resumeApply.applyingAs', { company: d.company_name, email: d.email })}
        </p>
        <p className="mt-2 text-[12px] text-[#6f7379]">
          {t('resumeApply.progressSaved', {
            date: Number.isNaN(expires.getTime())
              ? t('resumeApply.itExpires')
              : expires.toLocaleDateString(),
          })}
        </p>
      </header>

      {/* ── CV ─────────────────────────────────────────────────────── */}
      <GlassCard className="p-5">
        <h2 className="text-[15px] font-semibold text-white">{t('resumeApply.cvTitle')}</h2>
        {d.has_resume ? (
          <p className="mt-2 flex items-center gap-2 text-[13.5px] text-[#b8babf]">
            <CheckCircle2 size={14} aria-hidden="true" className="text-[#6fbf8d]" />
            {d.resume_filename ?? t('resumeApply.cvUploaded')}
          </p>
        ) : (
          <p className="mt-2 text-[13.5px] text-[#888b91]">{t('resumeApply.cvPrompt')}</p>
        )}
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf"
          className="sr-only"
          aria-label={t('resumeApply.cvUploadAria')}
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          className="mt-3 flex items-center gap-2 rounded-[10px] border border-white/[0.1] px-3 py-2 text-[13px] text-[#b8babf] hover:text-white disabled:opacity-40"
        >
          <Upload size={14} aria-hidden="true" />
          {upload.isPending
            ? t('resumeApply.cvReading')
            : d.has_resume
              ? t('resumeApply.cvReplace')
              : t('resumeApply.cvUpload')}
        </button>
        {d.has_resume ? (
          <p className="mt-2 text-[11.5px] text-[#6f7379]">{t('resumeApply.cvReplaceNote')}</p>
        ) : null}
        {fileError ? (
          <p className="mt-2 flex items-center gap-1.5 text-[12px] text-[#e6714f]">
            <AlertCircle size={12} aria-hidden="true" />
            {fileError}
          </p>
        ) : null}
      </GlassCard>

      {/* ── PH3-B5: check what we read ─────────────────────────────── */}
      <GlassCard className="mt-4 p-5">
        <h2 className="text-[15px] font-semibold text-white">{t('resumeApply.checkTitle')}</h2>
        <p className="mt-1 text-[13px] text-[#888b91]">
          {d.has_resume && parsedAnything(d)
            ? t('resumeApply.checkFromCv')
            : t('resumeApply.checkFillIn')}
        </p>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label htmlFor="c-name" className={LABEL}>
              {t('resumeApply.fullName')}
            </label>
            <input
              id="c-name"
              value={form.full_name ?? ''}
              onChange={(e) => set('full_name', e.target.value)}
              className={INPUT}
            />
            {d.parsed.full_name && d.parsed.full_name !== (form.full_name ?? '') ? (
              <p className="mt-1 text-[11.5px] text-[#6f7379]">
                {t('resumeApply.cvSays', { value: d.parsed.full_name })}
              </p>
            ) : null}
          </div>
          <div>
            <label htmlFor="c-phone" className={LABEL}>
              {t('resumeApply.phone')}{' '}
              <span className="text-[#5a5f66]">{t('resumeApply.optional')}</span>
            </label>
            <input
              id="c-phone"
              value={form.phone ?? ''}
              onChange={(e) => set('phone', e.target.value)}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="c-years" className={LABEL}>
              {t('resumeApply.years')}{' '}
              <span className="text-[#5a5f66]">{t('resumeApply.optional')}</span>
            </label>
            <input
              id="c-years"
              type="number"
              min={0}
              max={60}
              value={form.years_experience ?? ''}
              onChange={(e) =>
                set('years_experience', e.target.value === '' ? null : Number(e.target.value))
              }
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="c-company" className={LABEL}>
              {t('resumeApply.employer')}{' '}
              <span className="text-[#5a5f66]">{t('resumeApply.optional')}</span>
            </label>
            <input
              id="c-company"
              value={form.current_company ?? ''}
              onChange={(e) => set('current_company', e.target.value)}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="c-title" className={LABEL}>
              {t('resumeApply.role')}{' '}
              <span className="text-[#5a5f66]">{t('resumeApply.optional')}</span>
            </label>
            <input
              id="c-title"
              value={form.current_title ?? ''}
              onChange={(e) => set('current_title', e.target.value)}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="c-linkedin" className={LABEL}>
              {t('resumeApply.linkedin')}{' '}
              <span className="text-[#5a5f66]">{t('resumeApply.optional')}</span>
            </label>
            <input
              id="c-linkedin"
              value={form.linkedin_url ?? ''}
              onChange={(e) => set('linkedin_url', e.target.value)}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="c-github" className={LABEL}>
              {t('resumeApply.github')}{' '}
              <span className="text-[#5a5f66]">{t('resumeApply.optional')}</span>
            </label>
            <input
              id="c-github"
              value={form.github_url ?? ''}
              onChange={(e) => set('github_url', e.target.value)}
              className={INPUT}
            />
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="rounded-[10px] border border-white/[0.1] px-3.5 py-2 text-[13px] text-[#b8babf] hover:text-white disabled:opacity-40"
          >
            {save.isPending ? t('resumeApply.saving') : t('resumeApply.saveLater')}
          </button>
          <Pill
            onClick={() => confirm.mutate()}
            disabled={confirm.isPending || !(form.full_name ?? '').trim()}
            className="px-4 py-2"
          >
            {confirm.isPending
              ? t('resumeApply.confirming')
              : d.confirmed
                ? t('resumeApply.confirmedUpdate')
                : t('resumeApply.detailsCorrect')}
          </Pill>
          {d.confirmed ? (
            <span className="flex items-center gap-1.5 text-[12.5px] text-[#6fbf8d]">
              <CheckCircle2 size={13} aria-hidden="true" />
              {t('resumeApply.confirmed')}
            </span>
          ) : null}
        </div>
        {confirm.isError ? (
          <p className="mt-2 text-[12px] text-[#e6714f]">
            {errText(confirm.error, t('resumeApply.errConfirm'))}
          </p>
        ) : null}
      </GlassCard>

      {/* ── Submit ─────────────────────────────────────────────────── */}
      <GlassCard className="mt-4 p-5">
        <h2 className="text-[15px] font-semibold text-white">{t('resumeApply.sendTitle')}</h2>
        <ul className="mt-3 flex flex-col gap-1.5 text-[13px]">
          <Requirement met={d.has_resume} label={t('resumeApply.reqCv')} />
          <Requirement met={d.confirmed} label={t('resumeApply.reqDetails')} />
        </ul>
        <div className="mt-4">
          <Pill
            onClick={() => send.mutate()}
            disabled={send.isPending || !d.has_resume || !d.confirmed}
            className="px-5 py-2.5"
          >
            {send.isPending ? t('resumeApply.sending') : t('resumeApply.submit')}
          </Pill>
        </div>
        {send.isError ? (
          <p className="mt-2 flex items-start gap-1.5 text-[12.5px] text-[#e6714f]">
            <AlertCircle size={13} aria-hidden="true" className="mt-0.5 shrink-0" />
            {errText(send.error, t('resumeApply.errSend'))}
          </p>
        ) : null}
      </GlassCard>

      {/* Erasure, exercised by the person themselves. Two steps, because it
          destroys their work and cannot be undone — not to discourage it. */}
      <GlassCard className="mt-4 p-5">
        <h2 className="text-[15px] font-semibold text-white">{t('resumeApply.deleteTitle')}</h2>
        <p className="mt-1 text-[13px] text-[#888b91]">{t('resumeApply.deleteDesc')}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={() => (discarding ? discard.mutate() : setDiscarding(true))}
            disabled={discard.isPending}
            className="rounded-[10px] border border-[#e6714f]/40 px-3.5 py-2 text-[13px] text-[#e6714f] hover:bg-[#e6714f]/[0.08] disabled:opacity-40"
          >
            {discard.isPending
              ? t('resumeApply.deleting')
              : discarding
                ? t('resumeApply.deleteConfirm')
                : t('resumeApply.deleteCta')}
          </button>
          {discarding && !discard.isPending ? (
            <button
              type="button"
              onClick={() => setDiscarding(false)}
              className="text-[12.5px] text-[#6f7379] hover:text-[#b8babf]"
            >
              {t('resumeApply.keepIt')}
            </button>
          ) : null}
        </div>
        {discard.isError ? (
          <p className="mt-2 text-[12px] text-[#e6714f]">
            {errText(discard.error, t('resumeApply.errDelete'))}
          </p>
        ) : null}
      </GlassCard>
    </Shell>
  );
}

function Requirement({ met, label }: { met: boolean; label: string }): JSX.Element {
  const { t } = useTranslation();
  return (
    <li className="flex items-center gap-2">
      {met ? (
        <CheckCircle2 size={14} aria-hidden="true" className="text-[#6fbf8d]" />
      ) : (
        <AlertCircle size={14} aria-hidden="true" className="text-[#d6a23d]" />
      )}
      {/* Never colour alone — the words say which it is. */}
      <span className={met ? 'text-[#b8babf]' : 'text-[#d6a23d]'}>
        {label}
        {met ? '' : t('resumeApply.stillNeeded')}
      </span>
    </li>
  );
}

function Shell({ children }: { children: React.ReactNode }): JSX.Element {
  return <div className="mx-auto w-full max-w-[720px] px-4 py-8">{children}</div>;
}
