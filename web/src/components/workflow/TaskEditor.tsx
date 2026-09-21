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
// Response type is offered as text or link ONLY. The server's schema also
// allows "file" on an item, but `PUT /task/responses/{item_key}` refuses a
// file-type item outright ("upload it as an artifact instead") and
// `POST /task/artifacts` — the only endpoint that stores a file — never
// takes an `item_key`. An item built here as "file" could never be answered
// by a candidate, so it is not offered (see PublicTask.tsx's
// FileItemUnavailable for the candidate-side half of this note).

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
import { Loader2, Plus, Trash2, Upload } from '@/design/components/icons';

const MAX_ITEMS = 20;
const ITEM_KEY_RE = /^[a-z0-9_]{1,80}$/;
const MAX_MATERIAL_BYTES = 10 * 1024 * 1024;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function blankItem(n: number): TaskItem {
  return { key: `item_${n}`, prompt: '', response_type: 'text', required: true, max_chars: null };
}

const inputCls =
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground ' +
  'placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

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
    <div className="flex flex-col gap-2 rounded-[10px] border border-border p-3">
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className={labelCls}>Key</span>
          <input
            value={item.key}
            disabled={!editable}
            onChange={(e) => onChange({ ...item, key: e.target.value })}
            placeholder="lowercase_with_underscores"
            className={inputCls}
          />
          {!ITEM_KEY_RE.test(item.key) ? (
            <p className="mt-1 text-[11px] text-[var(--ui-warn)]">
              Lowercase letters, digits and underscores only.
            </p>
          ) : duplicateKey ? (
            <p className="mt-1 text-[11px] text-[var(--ui-warn)]">This key is used twice.</p>
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
            className={inputCls}
          >
            <option value="text">Written answer</option>
            <option value="link">Link</option>
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
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-[12.5px] text-foreground">
          <input
            type="checkbox"
            checked={item.required}
            disabled={!editable}
            onChange={(e) => onChange({ ...item, required: e.target.checked })}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
          Required
        </label>
        {item.response_type === 'text' ? (
          <label className="flex items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
            Max characters
            <input
              type="number"
              min={1}
              max={20000}
              disabled={!editable}
              value={item.max_chars ?? ''}
              placeholder="20000"
              onChange={(e) =>
                onChange({ ...item, max_chars: e.target.value ? Number(e.target.value) : null })
              }
              className="w-24 rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
            />
          </label>
        ) : null}
        {editable ? (
          <button
            type="button"
            onClick={onRemove}
            className="ml-auto inline-flex items-center gap-1 text-[12px] text-[var(--ui-danger)] hover:underline"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            Remove
          </button>
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
        <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading materials…
        </span>
      ) : materials.isError ? (
        <p className="text-[12px] text-[var(--ui-danger)]">Could not load reference materials.</p>
      ) : (materials.data ?? []).length === 0 ? (
        <p className="text-[12px] text-muted-foreground">No reference materials attached.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {(materials.data ?? []).map((m) => (
            <li
              key={m.id}
              className="flex items-center justify-between gap-2 rounded-[8px] border border-border px-2.5 py-1.5 text-[12.5px]"
            >
              <span className="min-w-0 truncate text-foreground">{m.title}</span>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => downloadMut.mutate(m.id)}
                  className="text-[11.5px] text-[var(--ui-info)] hover:underline"
                >
                  Download
                </button>
                {editable ? (
                  <button
                    type="button"
                    onClick={() => removeMut.mutate(m.id)}
                    disabled={removeMut.isPending}
                    className="text-[11.5px] text-[var(--ui-danger)] hover:underline"
                  >
                    Remove
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {editable ? (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title"
            className="w-40 rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          />
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-[8px] border border-[var(--ui-line-strong)] px-2.5 py-1 text-[11.5px] text-foreground hover:border-[var(--accent)]/60">
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
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
      {error ? <p className="text-[11.5px] text-[var(--ui-danger)]">{error}</p> : null}
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
  // empty prompt, a duplicate key, a job simulation with no items, or a
  // portfolio's minimum outrunning its maximum.
  const hasInvalidItem = form.items.some(
    (it) => !ITEM_KEY_RE.test(it.key) || !it.prompt.trim(),
  );
  const hasDuplicateKey = new Set(keys).size !== keys.length;
  const needsAtLeastOneItem = kind === 'job_simulation' && form.items.length === 0;
  const minMaxInvalid =
    kind === 'portfolio' && Number(form.minArtifacts) > Number(form.maxArtifacts);
  const canSave =
    Boolean(form.brief.trim()) &&
    !hasInvalidItem &&
    !hasDuplicateKey &&
    !needsAtLeastOneItem &&
    !minMaxInvalid;

  return (
    <div className="flex flex-col gap-4">
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
          className={`${inputCls} mt-1 resize-y`}
        />
      </div>

      <details className="rounded-[10px] border border-border p-3">
        <summary className="cursor-pointer text-[12.5px] text-[var(--ui-soft)]">
          Add a Hindi or Telugu translation of the brief (optional)
        </summary>
        <div className="mt-3 flex flex-col gap-3">
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

      <div>
        <div className="flex items-center justify-between">
          <h4 className="text-[12px] font-medium text-[var(--ui-soft)]">
            Items ({form.items.length})
          </h4>
          {editable ? (
            <button
              type="button"
              disabled={atCap}
              onClick={() =>
                setForm({ ...form, items: [...form.items, blankItem(form.items.length + 1)] })
              }
              className="inline-flex items-center gap-1 text-[12px] text-[var(--accent)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Add item
            </button>
          ) : null}
        </div>
        {kind === 'job_simulation' && form.items.length === 0 ? (
          <p className="mt-1 text-[11.5px] text-[var(--ui-warn)]">
            A job simulation needs at least one item before it can be published.
          </p>
        ) : null}
        {atCap ? (
          <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
            A task has at most {MAX_ITEMS} items.
          </p>
        ) : null}
        <div className="mt-2 flex flex-col gap-2">
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
        <div className="flex flex-col gap-3 rounded-[10px] border border-border p-3">
          <h4 className="text-[12px] font-medium text-[var(--ui-soft)]">Portfolio settings</h4>
          <div className="grid grid-cols-2 gap-2.5">
            <label className="block">
              <span className={labelCls}>Minimum artifacts</span>
              <input
                type="number"
                min={0}
                max={20}
                disabled={!editable}
                value={form.minArtifacts}
                onChange={(e) => setForm({ ...form, minArtifacts: e.target.value })}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className={labelCls}>Maximum artifacts</span>
              <input
                type="number"
                min={0}
                max={20}
                disabled={!editable}
                value={form.maxArtifacts}
                onChange={(e) => setForm({ ...form, maxArtifacts: e.target.value })}
                className={inputCls}
              />
            </label>
          </div>
          {minMaxInvalid ? (
            <p className="text-[11.5px] text-[var(--ui-warn)]">
              The minimum cannot be more than the maximum.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-[12.5px] text-foreground">
              <input
                type="checkbox"
                checked={form.allowFiles}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, allowFiles: e.target.checked })}
                className="h-3.5 w-3.5 accent-[var(--accent)]"
              />
              Accept files
            </label>
            <label className="flex items-center gap-2 text-[12.5px] text-foreground">
              <input
                type="checkbox"
                checked={form.allowLinks}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, allowLinks: e.target.checked })}
                className="h-3.5 w-3.5 accent-[var(--accent)]"
              />
              Accept links
            </label>
          </div>
          <label className="block">
            <span className={labelCls}>Approved link domains (comma-separated)</span>
            <input
              value={form.allowedDomains}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, allowedDomains: e.target.value })}
              placeholder={DEFAULT_LINK_DOMAINS.join(', ')}
              className={inputCls}
            />
            <p className="mt-1 text-[11px] text-[var(--ui-faint)]">
              Leave blank for the default list: {DEFAULT_LINK_DOMAINS.join(', ')}.
            </p>
          </label>
        </div>
      ) : null}

      <div>
        <h4 className="mb-1.5 text-[12px] font-medium text-[var(--ui-soft)]">
          Reference materials
        </h4>
        <MaterialsEditor roundId={roundId} editable={editable} />
      </div>

      {editable ? (
        <button
          type="button"
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending || !canSave}
          className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
        >
          {saveMut.isPending ? 'Saving…' : 'Save task'}
        </button>
      ) : (
        <p className="text-[11.5px] text-[var(--ui-faint)]">
          This workflow version is fixed — its task configuration cannot change here.
        </p>
      )}
    </div>
  );
}
