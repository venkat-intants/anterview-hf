// StageSettings — PH4-O1. Who owns a stage and how long they have.
//
// A stage is one round of this version, or the final decision after the last
// one (`roundId: null`). Editable even on a LIVE version, deliberately — same
// reasoning as the interview kit (RoundInspector's KitEditor): owners and SLAs
// are operational, not part of the frozen rubric a candidate is assessed
// against, and the candidates already inside this version are exactly who
// they are for.
//
// Raising, resolving or reassigning an SLA changes nothing about a candidate's
// status — said here too, not just on the exceptions surface, because a
// number turning red reads as a verdict if nothing says otherwise.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from '@/design/components/icons';
import { toast } from '@/lib/toast';
import { getStages, listStageOwners, setStage, type StageSetting } from '@/api/stageSla';

interface Props {
  workflowId: string;
  /** null for the final-decision stage. */
  roundId: string | null;
  /** The stage's own name, for labels and the save toast. */
  label: string;
}

export default function StageSettings({ workflowId, roundId, label }: Props): JSX.Element {
  const qc = useQueryClient();
  const key = roundId ?? '__decision__';

  const stages = useQuery({
    queryKey: ['hr', 'workflow', workflowId, 'stages'],
    queryFn: () => getStages(workflowId),
  });
  const owners = useQuery({
    queryKey: ['hr', 'stage-owners'],
    queryFn: listStageOwners,
    staleTime: 5 * 60_000,
  });

  const stage = stages.data?.find((s) => s.round_id === roundId);

  const [ownerId, setOwnerId] = useState('');
  const [slaHours, setSlaHours] = useState('');
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);
  if (stage && hydratedFor !== key) {
    setHydratedFor(key);
    setOwnerId(stage.owner_user_id ?? '');
    setSlaHours(stage.sla_hours != null ? String(stage.sla_hours) : '');
  }

  const saveMut = useMutation({
    mutationFn: () =>
      setStage(workflowId, {
        round_id: roundId,
        owner_user_id: ownerId || null,
        sla_hours: slaHours.trim() === '' ? null : Number(slaHours),
      }),
    onSuccess: (next: StageSetting[]) => {
      qc.setQueryData(['hr', 'workflow', workflowId, 'stages'], next);
      toast.success(`Saved for ${label}`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not save this stage'),
  });

  if (stages.isLoading || owners.isLoading) {
    return (
      <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Loading stage settings…
      </span>
    );
  }
  if (stages.isError || owners.isError) {
    return (
      <p className="text-[12px] text-[var(--ui-danger)]">Could not load the stage owner or SLA.</p>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label
            htmlFor={`stage-owner-${key}`}
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            Owner
          </label>
          <select
            id={`stage-owner-${key}`}
            value={ownerId}
            onChange={(e) => setOwnerId(e.target.value)}
            className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          >
            <option value="">Unassigned</option>
            {(owners.data ?? []).map((o) => (
              <option key={o.user_id} value={o.user_id}>
                {o.full_name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor={`stage-sla-${key}`}
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            SLA (hours)
          </label>
          <input
            id={`stage-sla-${key}`}
            type="number"
            min={1}
            max={8760}
            placeholder="none"
            value={slaHours}
            onChange={(e) => setSlaHours(e.target.value)}
            className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          />
        </div>
      </div>
      <p className="text-[11.5px] leading-relaxed text-muted-foreground">
        Who is answerable for this stage, and how long before it is overdue. This never moves a
        candidate — it only tells your team where to look.
      </p>
      <button
        type="button"
        onClick={() => saveMut.mutate()}
        disabled={saveMut.isPending}
        className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {saveMut.isPending ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}
