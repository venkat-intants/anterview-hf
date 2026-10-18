// TaskEditor — PH4-D4. HR's authoring surface for a `job_simulation` or
// `portfolio` round: the brief a candidate reads (with optional Hindi/Telugu
// translations), the items they answer, a portfolio's artifact settings, and
// reference materials.
//
// Frozen the same way an exam-backed round's questions are (D1): while the
// workflow version is published, archived, in review or approved. `editable`
// here is the SAME flag RoundInspector already computes for that — no
// separate lock lookup, so this never drifts from what the exam picker next
// to it already enforces.
//
// An item's answer is written text, a link, or a file. A file answer is
// uploaded through `POST /task/artifacts` with the item's key (see
// PublicTask.tsx's FileItemField). A portfolio with "Accept files" off cannot
// have a file item — the server refuses every file there, an item's included
// — so Save is blocked on that combination, mirroring validate_config.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addRoundTaskMaterial,
  downloadRoundTaskMaterial,
  getRoundTask,
  listRoundTaskMaterials,
  putRoundTask,
  removeRoundTaskMaterial,
  DEFAULT_LINK_DOMAINS,
  type TaskConfig,
  type TaskItem,
  type TaskKind,
  type TaskResponseType,
} from '@/api/jobTasks';
import { downloadUrl } from '@/lib/safeUrl';
import { toast } from '@/lib/toast';
import { Plus, Trash2, Upload } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

const MAX_ITEMS = 20;
const ITEM_KEY_RE = /^[a-z0-9_]{1,80}$/;
const MAX_MATERIAL_BYTES = 10 * 1024 * 1024;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function blankItem(n: number): TaskItem {
  return { key: `item_${n}`, prompt: '', response_type: 'text', required: true, max_chars: null };
}

// Native <select>/<textarea> in the same look as the shared Input.
const inputCls =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground ' +
  'placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-[var(--accent)] ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30 disabled:opacity-60';
const selectCls = `${inputCls} h-11 py-0`;
const labelCls = 'mb-1.5 block text-[13px] font-semibold text-foreground';
const helpCls = 'mt-1.5 text-[12.5px] text-muted-foreground';
// Hairline-divided group with a 15px bold title — the same rhythm as the
// candidate drawer's sections, since this renders in the round's side panel.
const GROUP = 'border-t border-border pt-5';
const GROUP_TITLE = 'text-[15px] font-bold text-foreground';
const checkCls = 'flex min-h-10 items-center gap-2.5 text-[14px] text-foreground';

/* ── Items builder ─────────────────────────────────────────────────────── */

function ItemRow({
  item,
  editable,
  duplicateKey,
  onChange,
  onRemove,
}: {
  item: TaskItem;
  editable: boolean;
  duplicateKey: boolean;
  onChange: (next: TaskItem) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-[16px] border border-border p-3.5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block">
          <span className={labelCls}>Key</span>
          <Input
            value={item.key}
            disabled={!editable}
            onChange={(e) => onChange({ ...item, key: e.target.value })}
            placeholder="lowercase_with_underscores"
          />
          {!ITEM_KEY_RE.test(item.key) ? (
            <p className="mt-1.5 text-[12.5px] text-[var(--ui-warn)]">
              Lowercase letters, digits and underscores only.
            </p>
          ) : duplicateKey ? (
            <p className="mt-1.5 text-[12.5px] text-[var(--ui-warn)]">This key is used twice.</p>
          ) : null}
        </label>
        <label className="block">
          <span className={labelCls}>Answer type</span>
          <select
            value={item.response_type}
            disabled={!editable}
            onChange={(e) =>
              onChange({ ...item, response_type: e.target.value as TaskResponseType })
            }
            className={selectCls}
          >
            <option value="text">Written answer</option>
            <option value="link">Link</option>
            <option value="file">File upload (PDF, JPEG or PNG)</option>
          </select>
        </label>
      </div>
      <label className="block">
        <span className={labelCls}>Prompt</span>
        <textarea
          value={item.prompt}
          disabled={!editable}
          onChange={(e) => onChange({ ...item, prompt: e.target.value })}
          maxLength={4000}
          rows={2}
          className={`${inputCls} resize-y`}
        />
      </label>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className={checkCls}>
          <input
            type="checkbox"
            checked={item.required}
            disabled={!editable}
            onChange={(e) => onChange({ ...item, required: e.target.checked })}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Required
        </label>
        {item.response_type === 'text' ? (
          <label className="flex items-center gap-2 text-[14px] text-[var(--ui-soft)]">
            Max characters
            <Input
              type="number"
              min={1}
              max={20000}
              disabled={!editable}
              value={item.max_chars ?? ''}
              placeholder="20000"
              onChange={(e) =>
                onChange({ ...item, max_chars: e.target.value ? Number(e.target.value) : null })
              }
              className="w-28"
            />
          </label>
        ) : null}
        {/* Kept apart, on the far edge, from the fields it would discard. */}
        {editable ? (
          <Button
            type="button"
            variant="outline"
            onClick={onRemove}
            className="ml-auto border-[var(--ui-danger)]/40 text-[var(--ui-danger)] hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]"
          >
            <Trash2 aria-hidden="true" />
            Remove
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/* ── Materials ─────────────────────────────────────────────────────────── */

function MaterialsEditor({ roundId, editable }: { roundId: string; editable: boolean }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState('');
  const [error, setError] = useState<string | null>(null);

  const materials = useQuery({
    queryKey: ['hr', 'round-task-materials', roundId],
    queryFn: () => listRoundTaskMaterials(roundId),
  });

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ['hr', 'round-task-materials', roundId] });

  const addMut = useMutation({
    mutationFn: (file: File) => addRoundTaskMaterial(roundId, file, title.trim() || file.name),
    onSuccess: () => {
      setTitle('');
      setError(null);
      invalidate();
    },
    onError: (e: unknown) => setError(errText(e, 'Could not add that material')),
  });

  const removeMut = useMutation({
    mutationFn: (materialId: string) => removeRoundTaskMaterial(roundId, materialId),
    onSuccess: invalidate,
    onError: (e: unknown) => toast.error(errText(e, 'Could not remove that material')),
  });

  const downloadMut = useMutation({
    mutationFn: (materialId: string) => downloadRoundTaskMaterial(roundId, materialId),
    onSuccess: (res) => {
      const url = downloadUrl(res.url);
      if (!url) {
        toast.error('That download link could not be opened.');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not open this material')),
  });

  function pick(file: File | null): void {
    setError(null);
    if (!file) return;
    if (file.size > MAX_MATERIAL_BYTES) {
      setError('That file is over 10 MB. Please upload a smaller one.');
      return;
    }
    addMut.mutate(file);
  }

  return (
    <div className="flex flex-col gap-2">
      {materials.isLoading ? (
        <div aria-busy="true">
          <span className="sr-only">Loading materials…</span>
          <Skeleton className="h-11 rounded-[12px]" />
        </div>
      ) : materials.isError ? (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-[13px] text-[var(--ui-danger)]">Could not load reference materials.</p>
          <Button type="button" variant="outline" onClick={() => void materials.refetch()}>
            Try again
          </Button>
        </div>
      ) : (materials.data ?? []).length === 0 ? (
        <p className="text-[13px] text-muted-foreground">
          No reference materials attached.
          {editable ? ' Add a PDF or image below if candidates need one.' : ''}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {(materials.data ?? []).map((m) => (
            <li
              key={m.id}
              className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-[12px] border border-border py-1 pl-3.5 pr-1.5 text-[14px]"
            >
              <span className="min-w-0 flex-1 break-words text-foreground">{m.title}</span>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => downloadMut.mutate(m.id)}
                  aria-label={`Download ${m.title}`}
                  className="text-[var(--ui-info)]"
                >
                  Download
                </Button>
                {editable ? (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => removeMut.mutate(m.id)}
                    disabled={removeMut.isPending}
                    aria-label={`Remove ${m.title}`}
                    className="text-[var(--ui-danger)] hover:text-[var(--ui-danger)]"
                  >
                    Remove
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {editable ? (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title"
            aria-label="Material title"
            className="w-full sm:w-48"
          />
          <label className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-full border border-input bg-card px-5 text-[14px] font-semibold text-foreground transition-colors hover:border-[var(--ui-line-strong)] focus-within:ring-2 focus-within:ring-[var(--accent)]">
            <Upload className="h-4 w-4" aria-hidden="true" />
            {addMut.isPending ? 'Uploading…' : 'Add a file (PDF/JPEG/PNG)'}
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png"
              className="sr-only"
              disabled={addMut.isPending}
              onChange={(e) => pick(e.target.files?.[0] ?? null)}
            />
          </label>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-[13px] text-[var(--ui-danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/* ── The editor ────────────────────────────────────────────────────────── */

interface FormState {
  brief: string;
  briefHi: string;
  briefTe: string;
  items: TaskItem[];
  minArtifacts: string;
  maxArtifacts: string;
  allowFiles: boolean;
  allowLinks: boolean;
  allowedDomains: string;
}

function fromConfig(cfg: TaskConfig | null): FormState {
  return {
    brief: cfg?.brief ?? '',
    briefHi: cfg?.brief_translations?.hi ?? '',
    briefTe: cfg?.brief_translations?.te ?? '',
    items: cfg?.items ?? [],
    minArtifacts: cfg?.min_artifacts != null ? String(cfg.min_artifacts) : '0',
    maxArtifacts: cfg?.max_artifacts != null ? String(cfg.max_artifacts) : '5',
    allowFiles: cfg?.allow_files ?? true,
    allowLinks: cfg?.allow_links ?? true,
    allowedDomains: (cfg?.allowed_link_domains ?? []).join(', '),
  };
}

export default function TaskEditor({
  roundId,
  kind,
  editable,
}: {
  roundId: string;
  kind: TaskKind;
  editable: boolean;
}): JSX.Element {
  const qc = useQueryClient();
  const config = useQuery({
    queryKey: ['hr', 'round-task', roundId],
    queryFn: () => getRoundTask(roundId),
  });

  const [form, setForm] = useState<FormState>(() => fromConfig(null));
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);
  useEffect(() => {
    if (config.data !== undefined && hydratedFor !== roundId) {
      setHydratedFor(roundId);
      setForm(fromConfig(config.data));
    }
  }, [config.data, hydratedFor, roundId]);

  const saveMut = useMutation({
    mutationFn: () =>
      putRoundTask(roundId, {
        brief: form.brief,
        brief_translations:
          form.briefHi.trim() || form.briefTe.trim()
            ? {
                ...(form.briefHi.trim() ? { hi: form.briefHi.trim() } : {}),
                ...(form.briefTe.trim() ? { te: form.briefTe.trim() } : {}),
              }
            : null,
        items: form.items,
        ...(kind === 'portfolio'
          ? {
              min_artifacts: Number(form.minArtifacts) || 0,
              max_artifacts: Number(form.maxArtifacts) || 0,
              allow_files: form.allowFiles,
              allow_links: form.allowLinks,
              allowed_link_domains: form.allowedDomains.trim()
                ? form.allowedDomains
                    .split(',')
                    .map((d) => d.trim())
                    .filter(Boolean)
                : null,
            }
          : {}),
      }),
    onSuccess: () => {
      toast.success('Task saved');
      void qc.invalidateQueries({ queryKey: ['hr', 'round-task', roundId] });
    },
    onError: (e) => toast.error(errText(e, 'Could not save this task')),
  });

  const keys = form.items.map((i) => i.key);
  const atCap = form.items.length >= MAX_ITEMS;

  // Mirrors the server's own checks (app.job_tasks.validate_config) so a
  // click on Save never round-trips into a bare 422: an invalid key, an
  // empty prompt, a duplicate key, a job simulation with no items, a
  // portfolio's minimum outrunning its maximum, or a file item on a portfolio
  // that does not accept files.
  const hasInvalidItem = form.items.some((it) => !ITEM_KEY_RE.test(it.key) || !it.prompt.trim());
  const hasDuplicateKey = new Set(keys).size !== keys.length;
  const needsAtLeastOneItem = kind === 'job_simulation' && form.items.length === 0;
  const minMaxInvalid =
    kind === 'portfolio' && Number(form.minArtifacts) > Number(form.maxArtifacts);
  const fileItemWithoutFiles =
    kind === 'portfolio' &&
    !form.allowFiles &&
    form.items.some((it) => it.response_type === 'file');
  const canSave =
    Boolean(form.brief.trim()) &&
    !hasInvalidItem &&
    !hasDuplicateKey &&
    !needsAtLeastOneItem &&
    !minMaxInvalid &&
    !fileItemWithoutFiles;

  // The saved task could not be read. Showing the empty form instead would let
  // a Save overwrite a configuration that exists, so say so and offer a retry.
  if (config.isLoading) {
    return (
      <div className="flex flex-col gap-3" aria-busy="true">
        <span className="sr-only">Loading…</span>
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-32 rounded-[12px]" />
        <Skeleton className="h-20 rounded-[12px]" />
      </div>
    );
  }
  if (config.isError) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3">
        <p className="text-[13.5px] text-[var(--ui-danger)]">
          {errText(config.error, 'Could not load this task.')}
        </p>
        <Button type="button" variant="outline" onClick={() => void config.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3">
        <h3 className={GROUP_TITLE}>Candidate brief</h3>
        <div>
          <label className={labelCls} htmlFor="task-brief">
            Brief
          </label>
          <textarea
            id="task-brief"
            value={form.brief}
            disabled={!editable}
            onChange={(e) => setForm({ ...form, brief: e.target.value })}
            maxLength={20000}
            rows={5}
            placeholder="What the candidate is asked to do, and why."
            className={`${inputCls} resize-y`}
          />
        </div>

        <details className="rounded-[12px] border border-border px-3.5">
          <summary className="flex min-h-10 cursor-pointer items-center text-[13.5px] text-[var(--ui-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
            Add a Hindi or Telugu translation of the brief (optional)
          </summary>
          <div className="flex flex-col gap-3 pb-3.5 pt-2">
            <label className="block">
              <span className={labelCls}>Hindi</span>
              <textarea
                value={form.briefHi}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, briefHi: e.target.value })}
                maxLength={20000}
                rows={3}
                className={`${inputCls} resize-y`}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Telugu</span>
              <textarea
                value={form.briefTe}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, briefTe: e.target.value })}
                maxLength={20000}
                rows={3}
                className={`${inputCls} resize-y`}
              />
            </label>
          </div>
        </details>
      </div>

      <div className={GROUP}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className={GROUP_TITLE}>Items ({form.items.length})</h4>
          {editable ? (
            <Button
              type="button"
              variant="outline"
              disabled={atCap}
              onClick={() =>
                setForm({ ...form, items: [...form.items, blankItem(form.items.length + 1)] })
              }
            >
              <Plus aria-hidden="true" />
              Add item
            </Button>
          ) : null}
        </div>
        {kind === 'job_simulation' && form.items.length === 0 ? (
          <p className="mt-2 text-[13px] text-[var(--ui-warn)]">
            A job simulation needs at least one item before it can be published.
          </p>
        ) : null}
        {atCap ? <p className={helpCls}>A task has at most {MAX_ITEMS} items.</p> : null}
        <div className="mt-3 flex flex-col gap-3">
          {form.items.map((item, idx) => (
            <ItemRow
              key={idx}
              item={item}
              editable={editable}
              duplicateKey={keys.filter((k) => k === item.key).length > 1}
              onChange={(next) =>
                setForm({ ...form, items: form.items.map((it, i) => (i === idx ? next : it)) })
              }
              onRemove={() => setForm({ ...form, items: form.items.filter((_, i) => i !== idx) })}
            />
          ))}
        </div>
      </div>

      {kind === 'portfolio' ? (
        <div className={`${GROUP} flex flex-col gap-4`}>
          <h4 className={GROUP_TITLE}>Portfolio settings</h4>
          <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2">
            <label className="block">
              <span className={labelCls}>Minimum artifacts</span>
              <Input
                type="number"
                min={0}
                max={20}
                disabled={!editable}
                value={form.minArtifacts}
                onChange={(e) => setForm({ ...form, minArtifacts: e.target.value })}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Maximum artifacts</span>
              <Input
                type="number"
                min={0}
                max={20}
                disabled={!editable}
                value={form.maxArtifacts}
                onChange={(e) => setForm({ ...form, maxArtifacts: e.target.value })}
              />
            </label>
          </div>
          {minMaxInvalid ? (
            <p role="alert" className="text-[13px] text-[var(--ui-warn)]">
              The minimum cannot be more than the maximum.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-x-6">
            <label className={checkCls}>
              <input
                type="checkbox"
                checked={form.allowFiles}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, allowFiles: e.target.checked })}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Accept files
            </label>
            <label className={checkCls}>
              <input
                type="checkbox"
                checked={form.allowLinks}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, allowLinks: e.target.checked })}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Accept links
            </label>
          </div>
          {fileItemWithoutFiles ? (
            <p role="alert" className="text-[13px] text-[var(--ui-warn)]">
              An item asks for a file, so this portfolio must accept files.
            </p>
          ) : null}
          <label className="block">
            <span className={labelCls}>Approved link domains (comma-separated)</span>
            <Input
              value={form.allowedDomains}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, allowedDomains: e.target.value })}
              placeholder={DEFAULT_LINK_DOMAINS.join(', ')}
            />
            <p className={helpCls}>
              Leave blank for the default list: {DEFAULT_LINK_DOMAINS.join(', ')}.
            </p>
          </label>
        </div>
      ) : null}

      <div className={GROUP}>
        <h4 className={`${GROUP_TITLE} mb-3`}>Reference materials</h4>
        <MaterialsEditor roundId={roundId} editable={editable} />
      </div>

      {editable ? (
        <div className={GROUP}>
          <Button
            type="button"
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending || !canSave}
          >
            {saveMut.isPending ? 'Saving…' : 'Save task'}
          </Button>
        </div>
      ) : (
        <p className="text-[13px] text-muted-foreground">
          This workflow version is fixed - its task configuration cannot change here.
        </p>
      )}
    </div>
  );
}
