// DecisionReasons (/superadmin/decision-reasons) — O4. The company's own
// structured hire/reject reason taxonomy, configured by its super admin.
//
// Retiring a reason only removes it from HR's dropdown going forward —
// decisions already recorded with it keep the label they were recorded with
// (StageHistoryEntry.reason_label), unaffected by anything done here.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ClipboardCheck, Plus } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import {
  createDecisionReason,
  listCompanyDecisionReasons,
  updateDecisionReason,
  type CompanyDecisionReason,
} from '@/api/hr';
import type { DecisionOutcome } from '@/api/scorecards';

const APPLIES_TO_LABEL: Record<DecisionOutcome, string> = {
  hired: 'Hire only',
  rejected: 'Reject only',
  both: 'Hire or reject',
};

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export default function DecisionReasons(): JSX.Element {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<{ code: string; label: string } | null>(null);
  const [label, setLabel] = useState('');
  const [appliesTo, setAppliesTo] = useState<DecisionOutcome>('both');
  const [requiresExplanation, setRequiresExplanation] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['admin', 'decision-reasons'],
    queryFn: listCompanyDecisionReasons,
  });

  const createMut = useMutation({
    mutationFn: () =>
      createDecisionReason({
        label: label.trim(),
        applies_to: appliesTo,
        requires_explanation: requiresExplanation,
      }),
    onSuccess: () => {
      toast.success('Reason added');
      setLabel('');
      setAppliesTo('both');
      setRequiresExplanation(false);
      void qc.invalidateQueries({ queryKey: ['admin', 'decision-reasons'] });
    },
    onError: (e) => toast.error(errText(e, 'Could not add this reason')),
  });

  const toggleMut = useMutation({
    mutationFn: (row: CompanyDecisionReason) => updateDecisionReason(row.code, { active: !row.active }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin', 'decision-reasons'] }),
    onError: (e) => toast.error(errText(e, 'Could not update this reason')),
  });

  // Once a reason has been used in a decision, the server accepts only case and
  // spacing changes — anything else would change what past decisions are
  // counted as — and its refusal says so; it is shown as-is.
  const renameMut = useMutation({
    mutationFn: ({ code, label: next }: { code: string; label: string }) =>
      updateDecisionReason(code, { label: next }),
    onSuccess: (_res, { code }) => {
      toast.success('Reason renamed');
      // Close only the editor this rename came from. If the super admin has
      // moved on to another row while it was in flight, that form is theirs.
      setEditing((current) => (current?.code === code ? null : current));
      void qc.invalidateQueries({ queryKey: ['admin', 'decision-reasons'] });
    },
    onError: (e) => toast.error(errText(e, 'Could not rename this reason')),
  });

  const rows = data ?? [];

  return (
    <div className="mx-auto max-w-[860px] px-0 py-2 space-y-6">
      <Reveal>
        <div>
          <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">
            Decision reasons
          </h1>
          <p className="mt-1 text-[14px] text-muted-foreground">
            The reasons your HR managers choose from when they hire or reject a candidate.
            Retiring one never changes a decision already recorded with it.
          </p>
        </div>
      </Reveal>

      <Reveal delay={0.05}>
        <GlassCard className="p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
            <Plus size={16} aria-hidden="true" />
            Add a reason
          </h3>
          <form
            className="grid gap-2.5 sm:grid-cols-[1fr_auto_auto_auto] sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (!label.trim()) {
                toast.error('A label is required.');
                return;
              }
              createMut.mutate();
            }}
          >
            <div className="flex flex-col gap-1.5">
              <label htmlFor="reason-label" className="text-[12px] font-medium text-[var(--ui-soft)]">
                Label
              </label>
              <input
                id="reason-label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. Not enough experience"
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="reason-applies" className="text-[12px] font-medium text-[var(--ui-soft)]">
                Applies to
              </label>
              <select
                id="reason-applies"
                value={appliesTo}
                onChange={(e) => setAppliesTo(e.target.value as DecisionOutcome)}
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              >
                <option value="both">Hire or reject</option>
                <option value="hired">Hire only</option>
                <option value="rejected">Reject only</option>
              </select>
            </div>
            <label className="flex items-center gap-2 whitespace-nowrap text-[12.5px] text-[var(--ui-soft)]">
              <input
                type="checkbox"
                checked={requiresExplanation}
                onChange={(e) => setRequiresExplanation(e.target.checked)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Needs a written explanation
            </label>
            <button
              type="submit"
              disabled={createMut.isPending}
              className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
            >
              {createMut.isPending ? 'Adding…' : 'Add'}
            </button>
          </form>
        </GlassCard>
      </Reveal>

      <Reveal delay={0.08}>
        <GlassCard className="p-5">
          <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-foreground">
            <ClipboardCheck size={17} aria-hidden="true" />
            Reasons
          </h3>
          {isLoading ? (
            <p className="text-[13px] text-muted-foreground">Loading…</p>
          ) : isError ? (
            <p className="text-[13px] text-[var(--ui-danger)]">Could not load decision reasons.</p>
          ) : rows.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">
              No reasons yet — add your first one above.
            </p>
          ) : (
            <div role="list" aria-label="Decision reasons">
              {rows.map((r) => (
                <div
                  key={r.code}
                  role="listitem"
                  className="flex items-center justify-between rounded-[14px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2.5 mb-2 last:mb-0"
                >
                  <div className="min-w-0 flex-1">
                    {editing?.code === r.code ? (
                      <form
                        className="flex items-center gap-2"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const next = editing.label.trim();
                          if (next.length < 2) {
                            toast.error('A label is at least 2 characters.');
                            return;
                          }
                          renameMut.mutate({ code: r.code, label: next });
                        }}
                      >
                        <input
                          aria-label={`New label for ${r.label}`}
                          value={editing.label}
                          maxLength={120}
                          onChange={(e) => setEditing({ code: r.code, label: e.target.value })}
                          className="min-w-0 flex-1 rounded-[9px] border border-border bg-secondary px-2.5 py-1.5 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                        />
                        <button
                          type="submit"
                          disabled={renameMut.isPending && renameMut.variables?.code === r.code}
                          className="rounded-[9px] bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground disabled:opacity-50"
                        >
                          {renameMut.isPending && renameMut.variables?.code === r.code ? 'Saving…' : 'Save'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditing(null)}
                          className="rounded-[9px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                        >
                          Cancel
                        </button>
                      </form>
                    ) : (
                    <p className="text-[13.5px] font-medium text-foreground truncate">
                      {r.label}
                      {r.is_default ? (
                        <span className="ml-2 text-[11px] text-[var(--ui-faint)]">default</span>
                      ) : null}
                    </p>
                    )}
                    <p className="text-[12px] text-muted-foreground">
                      {APPLIES_TO_LABEL[r.applies_to]}
                      {r.requires_explanation ? ' · needs an explanation' : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <StatusTag tone={r.active ? 'forest' : 'neutral'}>
                      {r.active ? 'active' : 'retired'}
                    </StatusTag>
                    {editing?.code === r.code ? null : (
                      <button
                        type="button"
                        onClick={() => setEditing({ code: r.code, label: r.label })}
                        aria-label={`Rename ${r.label}`}
                        className="rounded-[9px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                      >
                        Rename
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => toggleMut.mutate(r)}
                      disabled={toggleMut.isPending && toggleMut.variables?.code === r.code}
                      className="rounded-[9px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground disabled:opacity-50"
                    >
                      {r.active ? 'Retire' : 'Reactivate'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          <p className="mt-3 text-[11.5px] text-muted-foreground">
            Retiring a reason only stops it appearing in the dropdown — past decisions keep
            whatever reason they were recorded with. Once a reason has been used, a rename can
            only fix its case or spacing; to mean something different, retire it and add a new one.
          </p>
        </GlassCard>
      </Reveal>
    </div>
  );
}
