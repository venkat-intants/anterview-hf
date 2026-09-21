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
// While the task is in progress the candidate can withdraw that consent
// (`withdrawTaskConsent`, POST /task/consent/withdraw) — this deletes
// nothing already saved, it only refuses any further save/upload/submit.

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
import { AuroraField } from '@/design/components/AuroraField';
import { GlassCard, Pill, StatusTag } from '@/design/components/primitives';
import LanguageSwitcher from '@/components/LanguageSwitcher';
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

/* ── Shared page chrome — matches PublicOffer.tsx's PageWrap exactly ──────── */

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
      <div className="relative z-10 mt-16 flex w-full max-w-[640px] flex-1 flex-col items-center gap-4">
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

/** The brief, the due date and the time limit — the only things shown before
 *  consent. Shared between the pre-start gate and the working page so the
 *  candidate reads the same header in both. */
function TaskHeader({ data }: { data: PublicTaskShape }) {
  const { t } = useTranslation();
  return (
    <GlassCard className="w-full p-6">
      <StatusTag tone="electric" dot>
        {t(`task.kind.${data.kind}`)}
      </StatusTag>
      <h1 className="mt-3 text-[22px] font-semibold tracking-[-0.5px] text-foreground">
        {data.round_title}
      </h1>
      {data.company ? (
        <p className="mt-1 text-[13px] text-muted-foreground">{data.company}</p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted-foreground">
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
        <p className="mt-2 text-[12px] text-[var(--ui-info)]">{t('task.adjustmentNotice')}</p>
      ) : null}

      <p className="mt-4 whitespace-pre-wrap text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
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
      <GlassCard className="w-full p-6">
        <p className="text-[13px] leading-relaxed text-[var(--ui-soft)]">
          {t('task.consentNotice')}
        </p>
        <label className="mt-4 flex items-start gap-2.5 text-[13px] text-foreground">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
          />
          {t('task.consentLabel')}
        </label>
        {error ? <p className="mt-3 text-[12.5px] text-ember">{error}</p> : null}
        <Pill
          className="mt-4"
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
        </Pill>
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
        {error ? <span className="text-[11.5px] text-ember">{error}</span> : null}
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
        className="w-full resize-y rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
      />
      <div className="flex items-center justify-between text-[11.5px] text-muted-foreground">
        <span>{t('task.charsRemaining', { count: Math.max(0, limit - value.length) })}</span>
        <span className={error ? 'text-ember' : undefined}>
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
        className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
      />
      <p className="text-[11.5px] text-muted-foreground">
        {t('task.linkDomainsHint', { domains: allowedDomains.join(', ') })}
      </p>
      {saveMut.isPending ? (
        <span className="text-[11.5px] text-muted-foreground">{t('task.saving')}</span>
      ) : error ? (
        <span className="text-[11.5px] text-ember">{error}</span>
      ) : value === savedValue && savedValue ? (
        <span className="text-[11.5px] text-[var(--ui-ok)]">{t('task.saved')}</span>
      ) : null}
    </div>
  );
}

/**
 * An item the server accepts (`response_type` in the schema) but neither
 * candidate endpoint can actually fulfil: `PUT /task/responses/{item_key}`
 * refuses a "file" item outright ("upload it as an artifact instead"), and
 * `POST /task/artifacts` — the only endpoint that stores a file — never takes
 * an `item_key`, so it can only ever create a free-form (portfolio) artifact.
 * There is today no backend path that attaches a file to a specific item.
 * Rather than build a control that always ends in a bare 422, this says so.
 */
function FileItemUnavailable() {
  const { t } = useTranslation();
  return <p className="text-[12.5px] text-muted-foreground">{t('task.fileItemUnavailable')}</p>;
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
        <span className="shrink-0 text-[11px] text-[var(--ui-faint)]">
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
          <FileItemUnavailable />
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
            <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
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
            className="inline-flex shrink-0 items-center gap-1 rounded-[8px] border border-[var(--ui-danger)]/30 px-2 py-1 text-[11.5px] text-[var(--ui-danger)] hover:bg-[var(--ui-danger)]/10 disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            {t('task.remove')}
          </button>
        ) : null}
      </div>
      {error ? <p className="mt-1.5 text-[11.5px] text-ember">{error}</p> : null}
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
          <Pill variant="outline" onClick={() => setMode('file')}>
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            {t('task.addFile')}
          </Pill>
        ) : null}
        {allowLinks ? (
          <Pill variant="outline" onClick={() => setMode('link')}>
            {t('task.addLink')}
          </Pill>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      {mode === 'link' ? (
        <>
          <label className="block text-[12px] font-medium text-[var(--ui-soft)]">
            {t('task.linkLabel')}
            <input
              type="url"
              value={linkUrl}
              onChange={(e) => setLinkUrl(e.target.value)}
              placeholder={t('task.linkPlaceholder')}
              className="mt-1 w-full rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
            />
          </label>
          <label className="block text-[12px] font-medium text-[var(--ui-soft)]">
            {t('task.linkKindLabel')}
            <select
              value={linkKind}
              onChange={(e) => setLinkKind(e.target.value as TaskLinkKind)}
              className="mt-1 w-full rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
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
        <label className="inline-flex w-fit cursor-pointer items-center gap-2 rounded-[9px] border border-[var(--ui-line-strong)] px-3 py-1.5 text-[12.5px] text-foreground hover:border-[var(--accent)]/60">
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
      <label className="block text-[12px] font-medium text-[var(--ui-soft)]">
        {t('task.artifactTitleLabel')}
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-1 w-full rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
        />
      </label>
      <label className="block text-[12px] font-medium text-[var(--ui-soft)]">
        {t('task.artifactDescLabel')}
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="mt-1 w-full resize-y rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
        />
      </label>
      {error ? <p className="text-[12px] text-ember">{error}</p> : null}
      <div className="flex gap-2">
        {mode === 'link' ? (
          <button
            type="button"
            onClick={() => addMut.mutate(null)}
            disabled={addMut.isPending || !linkUrl.trim()}
            className="rounded-[8px] bg-primary px-3.5 py-1.5 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
          >
            {addMut.isPending ? t('task.uploading') : t('task.addLink')}
          </button>
        ) : null}
        <button
          type="button"
          onClick={reset}
          className="rounded-[8px] border border-border px-3.5 py-1.5 text-[12.5px] text-foreground"
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
  // confirm, then a done state. `withdrawn` is local to this render only
  // (the server has no field on `GET /task` that says consent was pulled —
  // it just starts refusing save/upload/submit with a 409), the same trade
  // DocumentsSection already makes.
  const [withdrawAsking, setWithdrawAsking] = useState(false);
  const [withdrawn, setWithdrawn] = useState(false);
  const [withdrawError, setWithdrawError] = useState<string | null>(null);

  const withdrawMut = useMutation({
    mutationFn: () => withdrawTaskConsent(token),
    onSuccess: () => {
      setWithdrawn(true);
      setWithdrawAsking(false);
      setWithdrawError(null);
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
    return false; // 'file' items can never be answered — see FileItemUnavailable
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

            {submitError ? <p className="mt-2 text-[12.5px] text-ember">{submitError}</p> : null}

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
                    className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
                  >
                    {submitMut.isPending ? t('task.submitting') : t('task.confirmYes')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)]"
                  >
                    {t('task.cancel')}
                  </button>
                </div>
              </div>
            ) : (
              <Pill
                className="mt-1"
                disabled={!ready}
                onClick={() => {
                  setSubmitError(null);
                  setConfirming(true);
                }}
              >
                {t('task.submit')}
              </Pill>
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
                    className="rounded-[9px] border border-[var(--ui-danger)]/40 px-3 py-1.5 text-[12.5px] font-medium text-[var(--ui-danger)] disabled:opacity-40"
                  >
                    {t('task.withdrawConsentYes')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setWithdrawAsking(false)}
                    className="text-[12.5px] text-muted-foreground hover:text-foreground"
                  >
                    {t('task.cancel')}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setWithdrawAsking(true)}
                className="text-[12.5px] text-[var(--ui-info)] hover:underline"
              >
                {t('task.withdrawConsent')}
              </button>
            )}
            {withdrawError ? <p className="mt-2 text-[12px] text-ember">{withdrawError}</p> : null}
          </div>
        ) : null}
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
        <Pill onClick={() => void view.refetch()}>{t('task.retry')}</Pill>
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
        <Loader2 className="h-8 w-8 animate-spin text-electric" aria-hidden="true" />
        <p className="text-[13px] text-muted-foreground">{t('task.loading')}</p>
      </PageWrap>
    );
  }

  if (view.data.status === 'submitted') {
    return (
      <PageWrap>
        <CenteredMessage
          icon={<CheckCircle2 className="h-6 w-6" aria-hidden="true" />}
          title={t('task.submittedTitle')}
          desc={t('task.submittedDesc')}
          tone="ok"
        />
      </PageWrap>
    );
  }

  // 'assigned': not started, so consent has not been given yet — brief only,
  // no items, no materials (security review, PH4-D4). 'in_progress': the
  // full workspace, since starting IS consenting.
  if (view.data.status === 'assigned') {
    return (
      <PageWrap>
        <PreStart
          token={token}
          data={view.data}
          onStarted={(next) => qc.setQueryData(['public-task', token], next)}
        />
      </PageWrap>
    );
  }

  return (
    <PageWrap>
      <TaskWorkspace
        token={token}
        data={view.data}
        onChanged={() => void qc.invalidateQueries({ queryKey: ['public-task', token] })}
      />
    </PageWrap>
  );
}
