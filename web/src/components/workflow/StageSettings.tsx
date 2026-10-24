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
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import { getStages, listStageOwners, setStage, type StageSetting } from '@/api/stageSla';
import { errorMessage } from '@/lib/errorMessage';

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
    onError: (e) => toast.error(errorMessage(e, 'Could not save this stage.')),
  });

  if (stages.isError || owners.isError) {
    return (
      <QueryErrorPanel
        title="Could not load the stage owner or SLA."
        error={stages.error ?? owners.error}
        onRetry={() => {
          if (stages.isError) void stages.refetch();
          if (owners.isError) void owners.refetch();
        }}
        retrying={stages.isFetching || owners.isFetching}
      />
    );
  }
  if (stages.data === undefined || owners.data === undefined) {
    return (
      <div className="flex flex-col gap-2.5" aria-busy="true">
        <span className="sr-only">Loading stage settings…</span>
        <div className="grid grid-cols-2 gap-2.5">
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
        </div>
        <Skeleton className="h-9 w-20 rounded-full" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      <div className="grid grid-cols-2 gap-2.5">
        <FormField id={`stage-owner-${key}`} label="Owner">
          {(p) => (
            <SelectControl
              {...p}
              size="sm"
              value={ownerId}
              onChange={(e) => setOwnerId(e.target.value)}
            >
              <option value="">Unassigned</option>
              {owners.data.map((o) => (
                <option key={o.user_id} value={o.user_id}>
                  {o.full_name}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <FormField id={`stage-sla-${key}`} label="SLA (hours)">
          {(p) => (
            <Input
              {...p}
              size="sm"
              type="number"
              min={1}
              max={8760}
              placeholder="none"
              value={slaHours}
              onChange={(e) => setSlaHours(e.target.value)}
            />
          )}
        </FormField>
      </div>
      <p className="text-[12px] leading-relaxed text-muted-foreground">
        Who is answerable for this stage, and how long before it is overdue. This never moves a
        candidate - it only tells your team where to look.
      </p>
      {/* Outline: a secondary save inside the inspector, beside the round's own
          primary action (one primary per view). */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => saveMut.mutate()}
        disabled={saveMut.isPending}
        className="self-start"
      >
        {saveMut.isPending ? 'Saving…' : 'Save'}
      </Button>
    </div>
  );
}
