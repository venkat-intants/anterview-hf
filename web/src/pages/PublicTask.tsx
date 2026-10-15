// PublicTask — the candidate's own job simulation / portfolio task (PH4-D4),
// reached with no login from the emailed link. EN/HI/TE with a language
// switch, like the other public pages (candidate-facing, per CLAUDE.md).
//
// SECURITY (follows the PublicOffer/PublicApply precedent):
// - The token comes from the URL #FRAGMENT, never a path param or query
//   string — read once into state, then stripped from the address bar with
//   history.replaceState so it never lingers in browser history. It is sent
//   as the X-Task-Token header on every /task call (api/publicTask.ts) —
//   never a URL.
// - Every failure the server can produce for a bad/expired/withdrawn link
//   reads as the SAME sentence (NOT_AVAILABLE, no oracle) — this page shows
//   it verbatim rather than inventing separate "expired"/"withdrawn" screens
//   it cannot actually distinguish.
// - The candidate's own text and links are rendered as TEXT, never HTML —
//   no dangerouslySetInnerHTML anywhere on this page.
// - After submitting, the candidate sees "Submitted — with the hiring team"
//   and NOTHING else: no evaluation, no reviewer, no score, no note. There is
//   nothing in this file that could show one — the server never sends it.
//
// CONSENT (security review, PH4-D4 — read this before touching the flow):
// Nothing is ever autosaved or uploaded before the candidate has consented.
// Consent is therefore given at START, not at submit: the brief, the due
// date and the time limit are shown up front, but the ITEMS and MATERIALS
// stay hidden — and nothing can be typed or uploaded — until the candidate
// ticks the consent checkbox and starts the task. That single click both
// begins the clock (so a time limit, and any D2 extra time, is real rather
// than advisory) and is the moment `dpdp_consent_ledger` gets its entry.
// Submitting afterwards is a confirmation step, never a second consent gate.
// The candidate can withdraw that consent (`withdrawTaskConsent`, POST
// /task/consent/withdraw) either while the task is in progress OR after it
// has been submitted (PH4-D4 wave 5) — `GET /task` reports which, via
// `consent_withdrawn`. In progress, this deletes nothing already saved; it
// only refuses any further save/upload/submit. After submitting, it deletes
// nothing either, but the hiring team can no longer see the work and it
// cannot be used to pass the round.

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, isTransientApiError } from '@/api/client';
import {
  addTaskArtifact,
  downloadTaskMaterial,
  removeTaskArtifact,
  saveTaskResponse,
  startTask,
  submitTask,
  viewTask,
  withdrawTaskConsent,
  type PublicTask as PublicTaskShape,
} from '@/api/publicTask';
import {
  DEFAULT_LINK_DOMAINS,
  type TaskItem,
  type TaskLinkKind,
  type TaskResponseOut,
} from '@/api/jobTasks';
import { downloadUrl } from '@/lib/safeUrl';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Button } from '@/components/ui/button';
import { PublicFrame, PublicNotice } from '@/components/PublicFrame';
import { PublicNext } from '@/components/PublicNext';
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  Loader2,
  Trash2,
  Upload,
} from '@/design/components/icons';

const MAX_MATERIAL_BYTES = 10 * 1024 * 1024;
const ACCEPTED_ARTIFACT_TYPES = ['application/pdf', 'image/jpeg', 'image/png'];
const LINK_KINDS: TaskLinkKind[] = [
  'repository',
  'design',
  'document',
  'video',
  'website',
  'other',
];

/** The server's own sentence, shown exactly as written — see PublicOffer.tsx
 *  for why: a candidate reading it and calling the hiring team should
 *  describe precisely what they saw. */
function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function fmtDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number): string => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

/* ── Shared page chrome — the same frame PublicOffer.tsx uses ──────────────── */

const INPUT =
  'min-h-11 rounded-[12px] border border-input bg-card px-3 py-2 text-base text-foreground placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20 disabled:opacity-60 md:text-sm';
const CHIP_BUTTON =
  'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-full border border-[var(--ui-line-strong)] px-4 text-[13.5px] font-medium text-foreground hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50';
const DANGER_BUTTON =
  'inline-flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-full border border-[var(--ui-danger)]/50 px-4 text-[13.5px] font-medium text-[var(--ui-danger)] hover:bg-[var(--ui-danger-wash)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50';
const TEXT_BUTTON =
  'inline-flex min-h-11 items-center text-[13.5px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

function PageWrap({
  children,
  company,
  step,
}: {
  children: React.ReactNode;
  company?: string | null;
  step?: React.ReactNode;
}) {
  return (
    <PublicFrame company={company} width="default" privateLink step={step}>
      <div className="flex w-full flex-col gap-4">{children}</div>
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
  return <PublicNotice icon={icon} tone={tone} title={title} description={desc} />;
}

/** The brief, the due date and the time limit — the only things shown before
 *  consent. Shared between the pre-start gate and the working page so the
 *  candidate reads the same header in both. */
function TaskHeader({ data }: { data: PublicTaskShape }) {
  const { t } = useTranslation();
  return (
    <GlassCard className="w-full p-5 sm:p-6">
      <StatusTag tone="electric" dot>
        {t(`task.kind.${data.kind}`)}
      </StatusTag>
      <h1 className="mt-3 text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground">
        {data.round_title}
      </h1>
      {/* The company's name sits in the frame's header, so it is not repeated here. */}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13.5px] text-muted-foreground">
        {data.due_at ? (
          <span className="inline-flex items-center gap-1.5">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            {t('task.dueBy', { date: new Date(data.due_at).toLocaleString() })}
          </span>
        ) : null}
        {data.time_limit_seconds && !data.started_at ? (
          <span>{t('task.timeLimitNotice', { time: fmtDuration(data.time_limit_seconds) })}</span>
        ) : null}
      </div>

      {data.adjustments.extra_time_seconds || data.adjustments.deadline_extended ? (
        <p className="mt-2 text-[13px] text-[var(--ui-info)]">{t('task.adjustmentNotice')}</p>
      ) : null}

      <p className="mt-4 whitespace-pre-wrap text-[15px] leading-relaxed text-[var(--ui-soft)]">
        {data.brief}
      </p>
    </GlassCard>
  );
}

/* ── Before start: brief + consent ONLY — no items, no materials ──────────── */

function PreStart({
  token,
  data,
  onStarted,
}: {
  token: string;
  data: PublicTaskShape;
  onStarted: (next: PublicTaskShape) => void;
}) {
  const { t } = useTranslation();
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startMut = useMutation({
    mutationFn: () => startTask(token, true),
    onSuccess: (next) => {
      setError(null);
      onStarted(next);
    },
    onError: (e: unknown) => setError(errText(e, t('task.beginError'))),
  });

  return (
    <>
      <TaskHeader data={data} />
      <GlassCard className="w-full p-5 sm:p-6">
        <p className="text-[14px] leading-relaxed text-[var(--ui-soft)]">
          {t('task.consentNotice')}
        </p>
        <label className="mt-4 flex cursor-pointer items-start gap-3 text-[14px] text-foreground">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            className="mt-0.5 h-5 w-5 flex-none accent-[var(--accent)]"
          />
          {t('task.consentLabel')}
        </label>
        {error ? (
          <p role="alert" className="mt-3 text-[13px] text-[var(--ui-danger)]">
            {error}
          </p>
        ) : null}
        <Button
          className="mt-4 h-12 w-full sm:w-auto"
          disabled={!consent || startMut.isPending}
          onClick={() => {
            setError(null);
            startMut.mutate();
          }}
        >
          {startMut.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : null}
          {t('task.beginButton')}
        </Button>
      </GlassCard>
    </>
  );
}

/* ── Reference materials — only shown once started ────────────────────────── */

function MaterialRow({
  token,
  material,
}: {
  token: string;
  material: PublicTaskShape['materials'][number];
}) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);

  const downloadMut = useMutation({
    mutationFn: () => downloadTaskMaterial(token, material.id),
    onSuccess: (res) => {
      const url = downloadUrl(res.url);
      if (!url) {
        setError(t('task.downloadError'));
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    onError: (e: unknown) => setError(errText(e, t('task.downloadError'))),
  });

  return (
    <li className="flex items-center justify-between gap-2 rounded-[10px] border border-border px-3 py-2">
      <span className="min-w-0 truncate text-[13px] text-foreground">{material.title}</span>
      <div className="flex shrink-0 items-center gap-2">
        {error ? <span className="text-[12.5px] text-[var(--ui-danger)]">{error}</span> : null}
        <button
          type="button"
          onClick={() => {
            setError(null);
            downloadMut.mutate();
          }}
          disabled={downloadMut.isPending}
          className="inline-flex items-center gap-1.5 rounded-[8px] border border-[var(--ui-line-strong)] px-2.5 py-1 text-[12px] text-foreground hover:border-[var(--accent)]/60"
        >
          {downloadMut.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {t('task.download')}
        </button>
      </div>
    </li>
  );
}

/* ── One item's answer ────────────────────────────────────────────────────── */

function TextItemField({
  token,
  item,
  response,
  disabled,
  onSaved,
}: {
  token: string;
  item: TaskItem;
  response: TaskResponseOut | undefined;
  disabled: boolean;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(response?.text_value ?? '');
  const [savedValue, setSavedValue] = useState(response?.text_value ?? '');
  const [error, setError] = useState<string | null>(null);
  const limit = item.max_chars ?? 20000;

  const saveMut = useMutation({
    mutationFn: () => saveTaskResponse(token, item.key, { text_value: value }),
    onSuccess: () => {
      setSavedValue(value);
      setError(null);
      onSaved();
    },
    onError: (e: unknown) => setError(errText(e, t('task.saveError'))),
  });

  return (
    <div className="flex flex-col gap-1.5">
      <textarea
        id={`item-${item.key}`}
        value={value}
        disabled={disabled}
        maxLength={limit}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => {
          if (value !== savedValue) saveMut.mutate();
        }}
        rows={5}
        placeholder={t('task.textPlaceholder')}
        className={`w-full resize-y ${INPUT}`}
      />
      <div className="flex items-center justify-between text-[12.5px] text-muted-foreground">
        <span>{t('task.charsRemaining', { count: Math.max(0, limit - value.length) })}</span>
        <span className={error ? 'text-[var(--ui-danger)]' : undefined}>
          {saveMut.isPending
            ? t('task.saving')
            : error
              ? error
              : value === savedValue && savedValue
                ? t('task.saved')
                : ''}
        </span>
      </div>
    </div>
  );
}

function LinkItemField({
  token,
  item,
  response,
  allowedDomains,
  disabled,
  onSaved,
}: {
  token: string;
  item: TaskItem;
  response: TaskResponseOut | undefined;
  allowedDomains: string[];
  disabled: boolean;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(response?.link_url ?? '');
  const [savedValue, setSavedValue] = useState(response?.link_url ?? '');
  const [error, setError] = useState<string | null>(null);

  const saveMut = useMutation({
    mutationFn: () => saveTaskResponse(token, item.key, { link_url: value }),
    onSuccess: () => {
      setSavedValue(value);
      setError(null);
      onSaved();
    },
    onError: (e: unknown) => setError(errText(e, t('task.saveError'))),
  });

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={`item-${item.key}`} className="sr-only">
        {t('task.linkLabel')}
      </label>
      <input
        id={`item-${item.key}`}
        type="url"
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => {
          if (value !== savedValue) saveMut.mutate();
        }}
        placeholder={t('task.linkPlaceholder')}
        className={`w-full ${INPUT}`}
      />
      <p className="text-[12.5px] text-muted-foreground">
        {t('task.linkDomainsHint', { domains: allowedDomains.join(', ') })}
      </p>
      {saveMut.isPending ? (
        <span className="text-[12.5px] text-muted-foreground">{t('task.saving')}</span>
      ) : error ? (
        <span className="text-[12.5px] text-[var(--ui-danger)]">{error}</span>
      ) : value === savedValue && savedValue ? (
        <span className="text-[12.5px] text-[var(--ui-ok)]">{t('task.saved')}</span>
      ) : null}
    </div>
  );
}

/**
 * A "file" item's answer. `PUT /task/responses/{item_key}` never carries a
 * file, so this goes through `POST /task/artifacts` with the item's key
 * (`addTaskArtifact`'s `itemKey`), which ties the upload to THIS item rather
 * than the free-form portfolio list. Uploading again replaces the earlier
 * file server-side, so there is only ever one answer per item. Removing it
 * uses the same DELETE as a portfolio artifact, and stays available after
 * consent is withdrawn for the same reason ArtifactRow's does: a wrong
 * upload must always be retractable, and the server never gates a removal
 * on consent.
 */
function FileItemField({
  token,
  item,
  response,
  disabled,
  onSaved,
}: {
  token: string;
  item: TaskItem;
  response: TaskResponseOut | undefined;
  disabled: boolean;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const current = response?.response_type === 'file' ? response : undefined;

  const uploadMut = useMutation({
    mutationFn: (file: File) => addTaskArtifact(token, { kind: 'file', file, itemKey: item.key }),
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (e: unknown) => setError(errText(e, t('task.addError'))),
  });

  const removeMut = useMutation({
    mutationFn: (responseId: string) => removeTaskArtifact(token, responseId),
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (e: unknown) => setError(errText(e, t('task.removeError'))),
  });

  function pickFile(input: HTMLInputElement): void {
    const file = input.files?.[0] ?? null;
    // Cleared so choosing the SAME file again (after fixing whatever the
    // server refused) still fires onChange.
    input.value = '';
    setError(null);
    if (!file) return;
    if (file.size > MAX_MATERIAL_BYTES) {
      setError(t('task.tooLarge'));
      return;
    }
    if (!ACCEPTED_ARTIFACT_TYPES.includes(file.type)) {
      setError(t('task.wrongType'));
      return;
    }
    uploadMut.mutate(file);
  }

  const busy = uploadMut.isPending || removeMut.isPending;

  return (
    <div className="flex flex-col gap-1.5">
      {current ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-border px-3 py-2">
          <span className="min-w-0 truncate text-[13px] text-foreground">
            {current.original_name}
          </span>
          <button
            type="button"
            onClick={() => removeMut.mutate(current.id)}
            disabled={busy}
            aria-label={`${t('task.remove')} ${current.original_name ?? ''}`}
            className={DANGER_BUTTON}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            {t('task.remove')}
          </button>
        </div>
      ) : null}
      {!disabled ? (
        <label className={`${CHIP_BUTTON} w-fit cursor-pointer focus-within:ring-2 focus-within:ring-[var(--accent)]`}>
          {uploadMut.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {uploadMut.isPending
            ? t('task.uploading')
            : current
              ? t('task.replaceFile')
              : t('task.uploadFile')}
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            className="sr-only"
            disabled={busy}
            onChange={(e) => pickFile(e.target)}
          />
        </label>
      ) : null}
      <p className="text-[12.5px] text-muted-foreground">{t('task.fileItemHint')}</p>
      {error ? <p className="text-[12.5px] text-[var(--ui-danger)]">{error}</p> : null}
    </div>
  );
}

function ItemCard({
  token,
  item,
  response,
  allowedDomains,
  disabled,
  onSaved,
}: {
  token: string;
  item: TaskItem;
  response: TaskResponseOut | undefined;
  allowedDomains: string[];
  disabled: boolean;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  return (
    <GlassCard className="w-full p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="whitespace-pre-wrap text-[13.5px] text-foreground">{item.prompt}</p>
        <span className="shrink-0 text-[12px] text-[var(--ui-soft)]">
          {item.required ? t('task.itemRequired') : t('task.itemOptional')}
        </span>
      </div>
      <div className="mt-3">
        {item.response_type === 'text' ? (
          <TextItemField
            token={token}
            item={item}
            response={response}
            disabled={disabled}
            onSaved={onSaved}
          />
        ) : item.response_type === 'link' ? (
          <LinkItemField
            token={token}
            item={item}
            response={response}
            allowedDomains={allowedDomains}
            disabled={disabled}
            onSaved={onSaved}
          />
        ) : (
          <FileItemField
            token={token}
            item={item}
            response={response}
            disabled={disabled}
            onSaved={onSaved}
          />
        )}
      </div>
    </GlassCard>
  );
}

/* ── Portfolio artifacts ───────────────────────────────────────────────────── */

function ArtifactRow({
  token,
  artifact,
  disabled,
  onRemoved,
}: {
  token: string;
  artifact: TaskResponseOut;
  disabled: boolean;
  onRemoved: () => void;
}) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);

  const removeMut = useMutation({
    mutationFn: () => removeTaskArtifact(token, artifact.id),
    onSuccess: onRemoved,
    onError: (e: unknown) => setError(errText(e, t('task.removeError'))),
  });

  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-foreground">
            {artifact.title || artifact.original_name || artifact.link_url || t('task.linkLabel')}
          </p>
          {artifact.link_url ? (
            <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
              {artifact.link_url}
            </p>
          ) : null}
          {artifact.description ? (
            <p className="mt-1 text-[12px] text-[var(--ui-soft)]">{artifact.description}</p>
          ) : null}
        </div>
        {/* A candidate can withdraw an artifact they uploaded by mistake (an
            ID scan instead of a portfolio PDF) any time before submitting —
            security review, PH4-D4. */}
        {!disabled ? (
          <button
            type="button"
            onClick={() => removeMut.mutate()}
            disabled={removeMut.isPending}
            aria-label={`${t('task.remove')} ${artifact.title || artifact.original_name || artifact.link_url || ''}`}
            className={DANGER_BUTTON}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            {t('task.remove')}
          </button>
        ) : null}
      </div>
      {error ? <p className="mt-1.5 text-[12.5px] text-[var(--ui-danger)]">{error}</p> : null}
    </li>
  );
}

function AddArtifactForm({
  token,
  allowFiles,
  allowLinks,
  onAdded,
}: {
  token: string;
  allowFiles: boolean;
  allowLinks: boolean;
  onAdded: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'file' | 'link' | null>(null);
  const [linkUrl, setLinkUrl] = useState('');
  const [linkKind, setLinkKind] = useState<TaskLinkKind>('other');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setMode(null);
    setLinkUrl('');
    setTitle('');
    setDescription('');
    setError(null);
  };

  const addMut = useMutation({
    mutationFn: (file: File | null) =>
      file
        ? addTaskArtifact(token, {
            kind: 'file',
            file,
            title: title.trim() || undefined,
            description: description.trim() || undefined,
          })
        : addTaskArtifact(token, {
            kind: 'link',
            link_url: linkUrl.trim(),
            link_kind: linkKind,
            title: title.trim() || undefined,
            description: description.trim() || undefined,
          }),
    onSuccess: () => {
      reset();
      onAdded();
    },
    onError: (e: unknown) => setError(errText(e, t('task.addError'))),
  });

  function pickFile(file: File | null): void {
    setError(null);
    if (!file) return;
    if (file.size > MAX_MATERIAL_BYTES) {
      setError(t('task.tooLarge'));
      return;
    }
    if (!ACCEPTED_ARTIFACT_TYPES.includes(file.type)) {
      setError(t('task.wrongType'));
      return;
    }
    addMut.mutate(file);
  }

  if (mode === null) {
    return (
      <div className="flex flex-wrap gap-2">
        {allowFiles ? (
          <Button variant="outline" className="h-11" onClick={() => setMode('file')}>
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            {t('task.addFile')}
          </Button>
        ) : null}
        {allowLinks ? (
          <Button variant="outline" className="h-11" onClick={() => setMode('link')}>
            {t('task.addLink')}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      {mode === 'link' ? (
        <>
          <label className="block text-[13px] font-medium text-foreground">
            {t('task.linkLabel')}
            <input
              type="url"
              value={linkUrl}
              onChange={(e) => setLinkUrl(e.target.value)}
              placeholder={t('task.linkPlaceholder')}
              className={`mt-1 w-full ${INPUT}`}
            />
          </label>
          <label className="block text-[13px] font-medium text-foreground">
            {t('task.linkKindLabel')}
            <select
              value={linkKind}
              onChange={(e) => setLinkKind(e.target.value as TaskLinkKind)}
              className={`mt-1 w-full ${INPUT}`}
            >
              {LINK_KINDS.map((k) => (
                <option key={k} value={k}>
                  {t(`task.linkKinds.${k}`)}
                </option>
              ))}
            </select>
          </label>
        </>
      ) : (
        <label className={`${CHIP_BUTTON} w-fit cursor-pointer focus-within:ring-2 focus-within:ring-[var(--accent)]`}>
          <Upload className="h-3.5 w-3.5" aria-hidden="true" />
          {addMut.isPending ? t('task.uploading') : t('task.addFile')}
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            className="sr-only"
            disabled={addMut.isPending}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </label>
      )}
      <label className="block text-[13px] font-medium text-foreground">
        {t('task.artifactTitleLabel')}
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className={`mt-1 w-full ${INPUT}`}
        />
      </label>
      <label className="block text-[13px] font-medium text-foreground">
        {t('task.artifactDescLabel')}
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className={`mt-1 w-full resize-y ${INPUT}`}
        />
      </label>
      {error ? <p className="text-[12px] text-[var(--ui-danger)]">{error}</p> : null}
      <div className="flex gap-2">
        {mode === 'link' ? (
          <button
            type="button"
            onClick={() => addMut.mutate(null)}
            disabled={addMut.isPending || !linkUrl.trim()}
            className="inline-flex min-h-11 items-center justify-center rounded-full bg-primary px-5 text-[14px] font-semibold text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 disabled:opacity-50"
          >
            {addMut.isPending ? t('task.uploading') : t('task.addLink')}
          </button>
        ) : null}
        <button
          type="button"
          onClick={reset}
          className={CHIP_BUTTON}
        >
          {t('task.cancel')}
        </button>
      </div>
    </div>
  );
}

/* ── The working page — items, materials, portfolio, submit ──────────────── */

function TaskWorkspace({
  token,
  data,
  onChanged,
}: {
  token: string;
  data: PublicTaskShape;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Consent withdrawal (security review, PH4-D4 wave 5) — mirrors
  // PublicOffer.tsx's document-consent withdrawal: a plain link, a two-step
  // confirm, then a done state. `withdrawn` is DERIVED, never just local
  // state: it is true once either the server's own `consent_withdrawn`
  // says so (code review — a refetch that surfaces a withdrawal made
  // elsewhere, another tab or a retried request, must never be ignored) OR
  // this mutation has just succeeded (so the UI updates at once, without
  // waiting on the refetch `onChanged()` below triggers). Consent, once
  // withdrawn, never goes back — a plain OR is enough, never a reset.
  const [withdrawAsking, setWithdrawAsking] = useState(false);
  const [locallyWithdrawn, setLocallyWithdrawn] = useState(false);
  const withdrawn = locallyWithdrawn || data.consent_withdrawn;
  const [withdrawError, setWithdrawError] = useState<string | null>(null);

  const withdrawMut = useMutation({
    mutationFn: () => withdrawTaskConsent(token),
    onSuccess: () => {
      setLocallyWithdrawn(true);
      setWithdrawAsking(false);
      setWithdrawError(null);
      onChanged();
    },
    onError: (e: unknown) => setWithdrawError(errText(e, t('task.errorGeneric'))),
  });

  const responsesByItem = new Map(
    data.responses.filter((r) => r.item_key).map((r) => [r.item_key as string, r]),
  );
  const artifacts = data.responses.filter((r) => !r.item_key);
  const allowedDomains = data.allowed_link_domains ?? [...DEFAULT_LINK_DOMAINS];

  const requiredUnmet = data.items.filter((i) => {
    if (!i.required) return false;
    const r = responsesByItem.get(i.key);
    if (i.response_type === 'text') return !r?.text_value;
    if (i.response_type === 'link') return !r?.link_url;
    return r?.response_type !== 'file';
  });
  const minArtifactsUnmet =
    data.kind === 'portfolio' &&
    data.min_artifacts != null &&
    artifacts.length < data.min_artifacts;
  const ready = requiredUnmet.length === 0 && !minArtifactsUnmet;

  // Consent was already given when this task was started — submitting is a
  // confirmation ("Send this to the hiring team?"), never a second consent
  // gate (security review, PH4-D4).
  const submitMut = useMutation({
    mutationFn: () => submitTask(token),
    onSuccess: () => {
      setConfirming(false);
      onChanged();
    },
    onError: (e: unknown) => {
      setConfirming(false);
      setSubmitError(errText(e, t('task.submitError')));
    },
  });

  const deadlineMs =
    data.time_limit_seconds && data.started_at
      ? new Date(data.started_at).getTime() + data.time_limit_seconds * 1000
      : null;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (deadlineMs === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [deadlineMs]);

  return (
    <>
      <TaskHeader data={data} />

      {deadlineMs !== null ? (
        <p className="w-full text-right text-[12.5px] text-muted-foreground">
          {t('task.timeRemaining', { time: fmtDuration((deadlineMs - now) / 1000) })}
        </p>
      ) : null}

      {data.materials.length > 0 ? (
        <GlassCard className="w-full p-5">
          <h2 className="text-[14px] font-semibold text-foreground">
            {t('task.materialsHeading')}
          </h2>
          <ul className="mt-3 flex flex-col gap-2">
            {data.materials.map((m) => (
              <MaterialRow key={m.id} token={token} material={m} />
            ))}
          </ul>
        </GlassCard>
      ) : null}

      {data.items.map((item) => (
        <ItemCard
          key={item.key}
          token={token}
          item={item}
          response={responsesByItem.get(item.key)}
          allowedDomains={allowedDomains}
          disabled={withdrawn}
          onSaved={onChanged}
        />
      ))}

      {data.kind === 'portfolio' ? (
        <GlassCard className="w-full p-5">
          <h2 className="text-[14px] font-semibold text-foreground">
            {t('task.portfolioHeading')}
          </h2>
          {data.min_artifacts != null && data.max_artifacts != null ? (
            <p className="mt-1 text-[12px] text-muted-foreground">
              {t('task.portfolioRange', { min: data.min_artifacts, max: data.max_artifacts })}
            </p>
          ) : null}
          {artifacts.length > 0 ? (
            <ul className="mt-3 flex flex-col gap-2">
              {artifacts.map((a) => (
                <ArtifactRow
                  key={a.id}
                  token={token}
                  artifact={a}
                  disabled={false}
                  onRemoved={onChanged}
                />
              ))}
            </ul>
          ) : null}
          {/* Adding an artifact is a write POST /task/artifacts refuses once
              consent is withdrawn — the same reason the item fields above go
              read-only. Removing one stays available either way (see
              ArtifactRow): the server never gates that on consent. */}
          {!withdrawn && (data.max_artifacts == null || artifacts.length < data.max_artifacts) ? (
            <div className="mt-3">
              <AddArtifactForm
                token={token}
                allowFiles={data.allow_files}
                allowLinks={data.allow_links}
                onAdded={onChanged}
              />
            </div>
          ) : null}
        </GlassCard>
      ) : null}

      <GlassCard className="w-full p-5">
        {withdrawn ? (
          <p className="text-[12.5px] text-muted-foreground">{t('task.withdrawConsentDone')}</p>
        ) : (
          <>
            {!ready ? (
              <p className="text-[12px] text-[var(--ui-warn)]">
                {requiredUnmet.length > 0 ? t('task.notReadyRequired') : null}
                {minArtifactsUnmet
                  ? t('task.notReadyArtifacts', { count: data.min_artifacts ?? 0 })
                  : null}
              </p>
            ) : null}

            {submitError ? <p className="mt-2 text-[12.5px] text-[var(--ui-danger)]">{submitError}</p> : null}

            {confirming ? (
              <div className="mt-3 flex flex-col gap-2 rounded-[10px] border border-border bg-[var(--ui-inset)] p-3">
                <p className="text-[13px] font-medium text-foreground">
                  {t('task.confirmSubmitTitle')}
                </p>
                <p className="text-[12.5px] text-[var(--ui-soft)]">{t('task.confirmSubmitDesc')}</p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => submitMut.mutate()}
                    disabled={submitMut.isPending}
                    className="inline-flex min-h-11 items-center justify-center rounded-full bg-primary px-5 text-[14px] font-semibold text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 disabled:opacity-50"
                  >
                    {submitMut.isPending ? t('task.submitting') : t('task.confirmYes')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className={CHIP_BUTTON}
                  >
                    {t('task.cancel')}
                  </button>
                </div>
              </div>
            ) : (
              <Button
                className="mt-1 h-12 w-full sm:w-auto"
                disabled={!ready}
                onClick={() => {
                  setSubmitError(null);
                  setConfirming(true);
                }}
              >
                {t('task.submit')}
              </Button>
            )}
          </>
        )}

        {/* Withdraw this task's consent (security review, PH4-D4 wave 5) —
            mirrors PublicOffer.tsx's document-consent withdrawal: a plain
            link, a two-step confirm, then a done state. Available while the
            task is in progress; once withdrawn there is nothing more to ask
            for here. */}
        {!withdrawn ? (
          <div className="mt-4 border-t border-border pt-3">
            {withdrawAsking ? (
              <div className="flex flex-col gap-2">
                <p className="text-[12.5px] text-foreground">{t('task.withdrawConsentConfirm')}</p>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={withdrawMut.isPending}
                    onClick={() => withdrawMut.mutate()}
                    className={DANGER_BUTTON}
                  >
                    {t('task.withdrawConsentYes')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setWithdrawAsking(false)}
                    className={`${TEXT_BUTTON} text-[var(--ui-soft)] hover:text-foreground`}
                  >
                    {t('task.cancel')}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setWithdrawAsking(true)}
                className={`${TEXT_BUTTON} text-[var(--ui-info)] hover:underline`}
              >
                {t('task.withdrawConsent')}
              </button>
            )}
            {withdrawError ? <p className="mt-2 text-[12px] text-[var(--ui-danger)]">{withdrawError}</p> : null}
          </div>
        ) : null}
      </GlassCard>
    </>
  );
}

/* ── After submitting: status message, plus a still-available withdrawal ──── */

/**
 * "Submitted — with the hiring team" is still the whole story for what
 * happened to the work (never an evaluation, reviewer, score or note — see
 * the file header). Below it, PH4-D4 wave 5 adds the one thing a candidate
 * can still do here: withdraw consent for work already sent. Same two-step
 * shape as TaskWorkspace's own control, with confirm/done wording written
 * for work that has already left the candidate's hands — the hiring team
 * loses the ability to see or use it, nothing is deleted by this alone.
 */
function SubmittedScreen({
  token,
  data,
  onChanged,
}: {
  token: string;
  data: PublicTaskShape;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [withdrawAsking, setWithdrawAsking] = useState(false);
  const [withdrawError, setWithdrawError] = useState<string | null>(null);

  const withdrawMut = useMutation({
    mutationFn: () => withdrawTaskConsent(token),
    onSuccess: () => {
      setWithdrawAsking(false);
      setWithdrawError(null);
      onChanged();
    },
    onError: (e: unknown) => setWithdrawError(errText(e, t('task.errorGeneric'))),
  });

  return (
    <>
      <CenteredMessage
        icon={<CheckCircle2 className="h-6 w-6" aria-hidden="true" />}
        title={t('task.submittedTitle')}
        desc={t('task.submittedDesc')}
        tone="ok"
      />
      <PublicNext>{t('public.reviewNext')}</PublicNext>
      <GlassCard className="w-full p-5">
        {data.consent_withdrawn ? (
          <p className="text-[12.5px] text-muted-foreground">
            {t('task.withdrawConsentSubmittedDone')}
          </p>
        ) : withdrawAsking ? (
          <div className="flex flex-col gap-2">
            <p className="text-[12.5px] text-foreground">
              {t('task.withdrawConsentSubmittedConfirm')}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={withdrawMut.isPending}
                onClick={() => withdrawMut.mutate()}
                className={DANGER_BUTTON}
              >
                {t('task.withdrawConsentYes')}
              </button>
              <button
                type="button"
                onClick={() => setWithdrawAsking(false)}
                className={`${TEXT_BUTTON} text-[var(--ui-soft)] hover:text-foreground`}
              >
                {t('task.cancel')}
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setWithdrawAsking(true)}
            className={`${TEXT_BUTTON} text-[var(--ui-info)] hover:underline`}
          >
            {t('task.withdrawConsent')}
          </button>
        )}
        {withdrawError ? <p className="mt-2 text-[12px] text-[var(--ui-danger)]">{withdrawError}</p> : null}
      </GlassCard>
    </>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function PublicTask(): JSX.Element {
  const { t } = useTranslation();
  const qc = useQueryClient();

  // SECURITY: token from the URL #fragment — never a path param or query
  // string. Read once, then the fragment is stripped so it never lingers in
  // browser history; the token lives only in this component's memory.
  const [token] = useState(() => window.location.hash.replace(/^#/, '').trim());
  useEffect(() => {
    if (token) window.history.replaceState(null, '', window.location.pathname);
  }, [token]);

  const view = useQuery({
    queryKey: ['public-task', token],
    queryFn: () => viewTask(token),
    enabled: token.length > 0,
    retry: (failureCount, error) => isTransientApiError(error) && failureCount < 2,
    staleTime: 0,
  });

  if (token && view.isError && isTransientApiError(view.error)) {
    return (
      <PageWrap>
        <CenteredMessage
          icon={<AlertCircle className="h-6 w-6" aria-hidden="true" />}
          title={t('task.networkTitle')}
          desc={t('task.networkDesc')}
        />
        <Button className="h-12 w-full" onClick={() => void view.refetch()}>{t('task.retry')}</Button>
      </PageWrap>
    );
  }

  if (!token || view.isError) {
    return (
      <PageWrap>
        <CenteredMessage
          icon={<AlertCircle className="h-6 w-6" aria-hidden="true" />}
          title={t('task.invalidTitle')}
          desc={
            // The server's own sentence when there is one — every failure it
            // can produce for a bad link reads the same, on purpose (no
            // oracle), so this is not paraphrased into a more specific claim.
            view.error instanceof ApiError && view.error.message
              ? view.error.message
              : t('task.invalidDesc')
          }
        />
      </PageWrap>
    );
  }

  if (view.isLoading || !view.data) {
    return (
      <PageWrap>
        <div role="status" className="flex flex-col items-center gap-3 py-16">
          <Loader2 className="h-8 w-8 animate-spin text-[var(--accent)]" aria-hidden="true" />
          <p className="text-[14px] text-muted-foreground">{t('task.loading')}</p>
        </div>
      </PageWrap>
    );
  }

  if (view.data.status === 'submitted') {
    return (
      <PageWrap company={view.data.company} step={t('public.step.task')}>
        <SubmittedScreen
          token={token}
          data={view.data}
          onChanged={() => void qc.invalidateQueries({ queryKey: ['public-task', token] })}
        />
      </PageWrap>
    );
  }

  // 'assigned': not started, so consent has not been given yet — brief only,
  // no items, no materials (security review, PH4-D4). 'in_progress': the
  // full workspace, since starting IS consenting.
  if (view.data.status === 'assigned') {
    return (
      <PageWrap company={view.data.company} step={t('public.step.task')}>
        <PreStart
          token={token}
          data={view.data}
          onStarted={(next) => qc.setQueryData(['public-task', token], next)}
        />
      </PageWrap>
    );
  }

  return (
    <PageWrap company={view.data.company} step={t('public.step.task')}>
      <TaskWorkspace
        token={token}
        data={view.data}
        onChanged={() => void qc.invalidateQueries({ queryKey: ['public-task', token] })}
      />
    </PageWrap>
  );
}
