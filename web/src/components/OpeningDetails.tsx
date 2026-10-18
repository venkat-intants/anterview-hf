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
import { toast } from '@/lib/toast';

const field =
  'h-10 w-full rounded-[12px] border border-input bg-card px-3.5 text-[14px] text-foreground focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

export default function OpeningDetails({ requisition: req }: { requisition: Requisition }) {
  const qc = useQueryClient();
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
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not update the opening'),
  });

  return (
    <SectionCard title="Opening details">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-[13px] font-semibold text-foreground">
          Owner
          <select
            className={`mt-1.5 ${field}`}
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            aria-label="Owner"
          >
            <option value="">No owner</option>
            {(team.data ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.full_name || m.email}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-[13px] font-semibold text-foreground">
          Hires wanted
          <input
            className={`mt-1.5 ${field}`}
            type="number"
            min={1}
            max={10000}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="-"
            aria-label="Hires wanted"
          />
        </label>
        <label className="block text-[13px] font-semibold text-foreground">
          Closes on
          <input
            className={`mt-1.5 ${field}`}
            type="date"
            min={new Date().toISOString().slice(0, 10)}
            value={closes}
            onChange={(e) => setCloses(e.target.value)}
            aria-label="Closes on"
          />
        </label>
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
