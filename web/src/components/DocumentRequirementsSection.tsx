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
import { StatusTag } from '@/design/components/primitives';
import { EmptyState, SectionCard } from '@/design/components/page';
import { queryKeys } from '@/lib/queryKeys';
import { FileText } from '@/design/components/icons';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

const labelCls = 'text-[13px] font-semibold text-foreground';
const selectCls =
  'mt-1.5 h-11 w-full rounded-[12px] border border-input bg-card px-3.5 text-[14px] text-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';
const checkLabelCls = 'flex min-h-10 items-center gap-2.5 text-[14px] text-foreground';

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
        queryKey: queryKeys.requisitionDocumentRequirements(requisitionId),
      });
      onDone();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not add this requirement')),
  });

  return (
    <div className="mb-4 flex flex-col gap-4 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4">
      <div>
        <label htmlFor={`${uid}-name`} className={labelCls}>
          Name
        </label>
        <Input
          id={`${uid}-name`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. PAN card"
          className="mt-1.5"
        />
      </div>
      <div>
        <label htmlFor={`${uid}-type`} className={labelCls}>
          Kind
        </label>
        <select
          id={`${uid}-type`}
          value={docType}
          onChange={(e) => setDocType(e.target.value as DocType)}
          className={selectCls}
        >
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={`${uid}-desc`} className={labelCls}>
          Description candidates see (optional)
        </label>
        <Input
          id={`${uid}-desc`}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="mt-1.5"
        />
      </div>
      <fieldset>
        <legend className="sr-only">Requirement settings</legend>
        <label className={checkLabelCls}>
          <input
            type="checkbox"
            checked={mandatory}
            onChange={(e) => setMandatory(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Mandatory
        </label>
        <label className={checkLabelCls}>
          <input
            type="checkbox"
            checked={requiresExpiry}
            onChange={(e) => setRequiresExpiry(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Needs an expiry date
        </label>
      </fieldset>
      <Button
        type="button"
        disabled={!name.trim() || addMut.isPending}
        onClick={() => addMut.mutate()}
        className="self-start"
      >
        {addMut.isPending ? 'Adding…' : 'Add requirement'}
      </Button>
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
      queryKey: queryKeys.requisitionDocumentRequirements(requisitionId),
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
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-[16px] border border-border p-3.5">
      <div className="min-w-0 flex-1 basis-48">
        <p className="break-words text-[14px] font-semibold text-foreground">{requirement.name}</p>
        <p className="mt-0.5 break-words text-[13px] text-muted-foreground">
          {requirement.doc_type}
          {requirement.description ? ` · ${requirement.description}` : ''}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusTag tone={requirement.mandatory ? 'amber' : 'neutral'}>
          {requirement.mandatory ? 'Mandatory' : 'Optional'}
        </StatusTag>
        {requirement.requires_expiry ? <StatusTag tone="lavender">Needs expiry</StatusTag> : null}
        <Button
          type="button"
          variant="outline"
          onClick={() => toggleMut.mutate({ mandatory: !requirement.mandatory })}
          disabled={toggleMut.isPending}
        >
          Make {requirement.mandatory ? 'optional' : 'mandatory'}
        </Button>
        <ConfirmDeleteButton
          title={`Remove ${requirement.name}`}
          pending={removeMut.isPending}
          onConfirm={() => removeMut.mutate()}
        />
      </div>
    </li>
  );
}

export default function DocumentRequirementsSection({ requisitionId }: { requisitionId: string }) {
  const [adding, setAdding] = useState(false);

  const list = useQuery({
    queryKey: queryKeys.requisitionDocumentRequirements(requisitionId),
    queryFn: () => listDocumentRequirements(requisitionId),
  });

  const rows = list.data ?? [];

  return (
    <SectionCard
      title={`Preboarding documents${rows.length > 0 ? ` (${rows.length})` : ''}`}
      description="What a candidate who accepts an offer for this opening must upload."
      action={
        <Button type="button" variant="outline" onClick={() => setAdding((v) => !v)}>
          {adding ? 'Close' : 'Add requirement'}
        </Button>
      }
    >
      {adding ? (
        <AddRequirementForm
          requisitionId={requisitionId}
          nextPosition={rows.length}
          onDone={() => setAdding(false)}
        />
      ) : null}

      {list.isLoading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <span className="sr-only">Loading…</span>
          <Skeleton className="h-16 rounded-[16px]" />
          <Skeleton className="h-16 rounded-[16px]" />
        </div>
      ) : list.isError ? (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-[13.5px] text-muted-foreground">
            Could not load this opening&apos;s document requirements.
          </p>
          <Button type="button" variant="outline" onClick={() => void list.refetch()}>
            Try again
          </Button>
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No documents required yet - candidates who accept an offer will see nothing to upload."
          description="Add the documents you need, such as an ID or a degree certificate."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((r) => (
            <RequirementRow key={r.id} requisitionId={requisitionId} requirement={r} />
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
