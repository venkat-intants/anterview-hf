// DocumentRequirementsSection — PH4-A4. What documents this opening asks
// candidates for during preboarding: name, kind, mandatory or optional,
// whether it needs an expiry date, and a description candidates see.
//
// Lives on the opening's own dashboard, next to Opening details — the same
// place its owner, hire target and closing date are edited (§B1) — because
// this too is a setting of the opening rather than of any one candidate.

import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addDocumentRequirement,
  deleteDocumentRequirement,
  listDocumentRequirements,
  updateDocumentRequirement,
  type DocType,
  type DocumentRequirement,
} from '@/api/offers';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';

const inputCls =
  'mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

const DOC_TYPES: DocType[] = [
  'identity',
  'address',
  'education',
  'employment',
  'tax',
  'bank',
  'photo',
  'medical',
  'other',
];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function AddRequirementForm({
  requisitionId,
  nextPosition,
  onDone,
}: {
  requisitionId: string;
  nextPosition: number;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const [name, setName] = useState('');
  const [docType, setDocType] = useState<DocType>('identity');
  const [description, setDescription] = useState('');
  const [mandatory, setMandatory] = useState(true);
  const [requiresExpiry, setRequiresExpiry] = useState(false);

  const addMut = useMutation({
    mutationFn: () =>
      addDocumentRequirement(requisitionId, {
        name: name.trim(),
        doc_type: docType,
        description: description.trim() || undefined,
        mandatory,
        requires_expiry: requiresExpiry,
        position: nextPosition,
      }),
    onSuccess: () => {
      toast.success('Document requirement added');
      void qc.invalidateQueries({
        queryKey: ['hr', 'requisition', requisitionId, 'document-requirements'],
      });
      onDone();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not add this requirement')),
  });

  return (
    <div className="mt-3 flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      <div>
        <label htmlFor={`${uid}-name`} className={labelCls}>
          Name
        </label>
        <input
          id={`${uid}-name`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. PAN card"
          className={inputCls}
        />
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label htmlFor={`${uid}-type`} className={labelCls}>
            Kind
          </label>
          <select
            id={`${uid}-type`}
            value={docType}
            onChange={(e) => setDocType(e.target.value as DocType)}
            className={inputCls}
          >
            {DOC_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col justify-end gap-1.5 pb-0.5">
          <label className="flex items-center gap-2 text-[12.5px] text-foreground">
            <input
              type="checkbox"
              checked={mandatory}
              onChange={(e) => setMandatory(e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            Mandatory
          </label>
          <label className="flex items-center gap-2 text-[12.5px] text-foreground">
            <input
              type="checkbox"
              checked={requiresExpiry}
              onChange={(e) => setRequiresExpiry(e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            Needs an expiry date
          </label>
        </div>
      </div>
      <div>
        <label htmlFor={`${uid}-desc`} className={labelCls}>
          Description candidates see (optional)
        </label>
        <input
          id={`${uid}-desc`}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className={inputCls}
        />
      </div>
      <button
        type="button"
        disabled={!name.trim() || addMut.isPending}
        onClick={() => addMut.mutate()}
        className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {addMut.isPending ? 'Adding…' : 'Add requirement'}
      </button>
    </div>
  );
}

function RequirementRow({
  requisitionId,
  requirement,
}: {
  requisitionId: string;
  requirement: DocumentRequirement;
}) {
  const qc = useQueryClient();
  const invalidate = () =>
    qc.invalidateQueries({
      queryKey: ['hr', 'requisition', requisitionId, 'document-requirements'],
    });

  const toggleMut = useMutation({
    mutationFn: (fields: { mandatory?: boolean; requires_expiry?: boolean }) =>
      updateDocumentRequirement(requirement.id, fields),
    onSuccess: () => void invalidate(),
    onError: (e: unknown) => toast.error(errText(e, 'Could not update this requirement')),
  });

  const removeMut = useMutation({
    mutationFn: () => deleteDocumentRequirement(requirement.id),
    onSuccess: () => {
      toast.success('Requirement removed');
      void invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not remove this requirement')),
  });

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-border p-3">
      <div className="min-w-0">
        <p className="truncate text-[13px] font-medium text-foreground">{requirement.name}</p>
        <p className="mt-0.5 text-[11.5px] text-muted-foreground">
          {requirement.doc_type}
          {requirement.description ? ` · ${requirement.description}` : ''}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusTag tone={requirement.mandatory ? 'amber' : 'neutral'}>
          {requirement.mandatory ? 'Mandatory' : 'Optional'}
        </StatusTag>
        {requirement.requires_expiry ? <StatusTag tone="lavender">Needs expiry</StatusTag> : null}
        <button
          type="button"
          onClick={() => toggleMut.mutate({ mandatory: !requirement.mandatory })}
          disabled={toggleMut.isPending}
          className="text-[12px] text-[var(--ui-info)] hover:underline disabled:opacity-40"
        >
          Make {requirement.mandatory ? 'optional' : 'mandatory'}
        </button>
        <ConfirmDeleteButton
          title={`Remove ${requirement.name}`}
          pending={removeMut.isPending}
          onConfirm={() => removeMut.mutate()}
        />
      </div>
    </li>
  );
}

export default function DocumentRequirementsSection({
  requisitionId,
}: {
  requisitionId: string;
}) {
  const [adding, setAdding] = useState(false);

  const list = useQuery({
    queryKey: ['hr', 'requisition', requisitionId, 'document-requirements'],
    queryFn: () => listDocumentRequirements(requisitionId),
  });

  const rows = list.data ?? [];

  return (
    <GlassCard className="mb-5 p-5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">
            Preboarding documents{rows.length > 0 ? ` (${rows.length})` : ''}
          </h2>
          <p className="mt-1 text-[12px] text-muted-foreground">
            What a candidate who accepts an offer for this opening must upload.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className="text-[12.5px] text-[var(--ui-info)] hover:underline"
        >
          {adding ? 'Close' : 'Add requirement'}
        </button>
      </div>

      {adding ? (
        <AddRequirementForm
          requisitionId={requisitionId}
          nextPosition={rows.length}
          onDone={() => setAdding(false)}
        />
      ) : null}

      {list.isLoading ? (
        <p className="mt-3 text-[12.5px] text-muted-foreground">Loading…</p>
      ) : list.isError ? (
        <p className="mt-3 text-[12.5px] text-muted-foreground">
          Could not load this opening&apos;s document requirements.
        </p>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-muted-foreground">
          No documents required yet — candidates who accept an offer will see nothing to upload.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {rows.map((r) => (
            <RequirementRow key={r.id} requisitionId={requisitionId} requirement={r} />
          ))}
        </ul>
      )}
    </GlassCard>
  );
}
