// OpeningDetails — who owns an opening, how many hires it wants, and when it
// closes (B1). Edited here rather than only at creation because all three
// change: an owner goes on leave, a target doubles, a deadline slips.
//
// The owner list is the company's active HR users, the same rule the server
// enforces — someone not on it cannot be saved as the owner.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  closingDateToIso,
  listTeam,
  updateRequisition,
  type Requisition,
} from '@/api/requisitions';
import { SectionCard } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';

export default function OpeningDetails({ requisition: req }: { requisition: Requisition }) {
  const qc = useQueryClient();
  // Silent on failure by design: the picker still offers "No owner" and the
  // current owner is kept, so nothing is lost; the next mount retries.
  const team = useQuery({ queryKey: ['hr', 'team'], queryFn: listTeam, staleTime: 60_000 });
  const [owner, setOwner] = useState(req.owner_user_id ?? '');
  const [target, setTarget] = useState(req.target_hires ? String(req.target_hires) : '');
  const [closes, setCloses] = useState(req.closes_at ? req.closes_at.slice(0, 10) : '');

  // A refetch after someone else's edit replaces what is shown.
  useEffect(() => {
    setOwner(req.owner_user_id ?? '');
    setTarget(req.target_hires ? String(req.target_hires) : '');
    setCloses(req.closes_at ? req.closes_at.slice(0, 10) : '');
  }, [req.owner_user_id, req.target_hires, req.closes_at]);

  const changed = {
    ...(owner !== (req.owner_user_id ?? '') ? { owner_user_id: owner || null } : {}),
    ...(target !== (req.target_hires ? String(req.target_hires) : '')
      ? { target_hires: target ? Number(target) : null }
      : {}),
    ...(closes !== (req.closes_at ? req.closes_at.slice(0, 10) : '')
      ? { closes_at: closingDateToIso(closes) }
      : {}),
  };
  const dirty = Object.keys(changed).length > 0;

  const save = useMutation({
    mutationFn: () => updateRequisition(req.id, changed),
    onSuccess: () => {
      toast.success('Opening updated');
      void qc.invalidateQueries({ queryKey: ['hr'] });
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update the opening.')),
  });

  return (
    <SectionCard title="Opening details">
      <div className="grid gap-3 sm:grid-cols-3">
        <FormField label="Owner">
          {(p) => (
            <SelectControl {...p} value={owner} onChange={(e) => setOwner(e.target.value)}>
              <option value="">No owner</option>
              {(team.data ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.full_name || m.email}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <FormField label="Hires wanted">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={1}
              max={10000}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="-"
            />
          )}
        </FormField>
        <FormField label="Closes on">
          {(p) => (
            <Input
              {...p}
              type="date"
              min={new Date().toISOString().slice(0, 10)}
              value={closes}
              onChange={(e) => setCloses(e.target.value)}
            />
          )}
        </FormField>
      </div>
      <div className="mt-3 flex justify-end">
        <Button
          type="button"
          variant="outline"
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
        >
          Save
        </Button>
      </div>
    </SectionCard>
  );
}
