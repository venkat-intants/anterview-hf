// TalentPools (/hr/pools, /hr/pools/:poolId) — PH5-E3. `hr_manager` ONLY
// (HRRoute; a company super_admin gets 403 server-side — a pool names
// candidates, and `candidate_pii` is `{hr_manager}` alone, CLAUDE.md).
//
// One component renders both routes, on the CorpusDocuments precedent: the
// list (name, members, created by, updated, and the pool-level actions) when
// there is no `:poolId`, the member table when there is.
//
// THE THREE RULES THIS SCREEN MUST NOT BREAK:
//  1. Deleting a pool takes its members with it — the confirm dialog names
//     how many (design §10, and the exact defect a missing count would be).
//  2. An INELIGIBLE member (`eligible: false`) shows its reason and offers
//     ONLY Remove — no evidence panel, no match explanation, no Invite.
//  3. `match_reason` is a FROZEN snapshot, labelled as one ("Added from a
//     rediscovery search on …") and never recomputed. No in-page evidence
//     editing of any kind — "Review evidence" only ever navigates to the
//     existing PH5-E5 screen (`/hr/enrolments/{id}/evidence`).
//
// The criterion-14 acknowledgement gate for a stale-evidence invite lives on
// the SERVER (422 `stale_evidence_unreviewed`) — this screen reacts to that
// specific failure rather than pre-computing "does this member need it?"; see
// `api/pools.ts::inviteMember`'s own comment.

import { Fragment, useEffect, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addPoolMembers,
  createPool,
  deletePool,
  getPool,
  inviteMember,
  isStaleEvidenceUnreviewed,
  listPools,
  markEvidenceReviewed,
  poolErrorMessage,
  removePoolMember,
  updatePool,
  type AddMemberSkipReason,
  type PoolMemberOut,
} from '@/api/pools';
import { listApplicants, type Applicant } from '@/api/applicants';
import { listRequisitions, type Requisition } from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Loader2,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  UserMinus,
  UserPlus,
  Users,
} from '@/design/components/icons';
import RediscoveryWhyPanel from '@/components/hr/RediscoveryWhyPanel';
import {
  FRESHNESS_TONE,
  fmtDate,
  freshnessChipLabel,
  ineligibleReasonLabel,
} from '@/components/hr/rediscoveryDisplay';

/** Debounce a fast-changing value (search box) — same shape as
 *  `Applicants.tsx`'s own local `useDebouncedValue`, kept local here too
 *  rather than shared, on that file's own precedent. */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export default function TalentPools(): JSX.Element {
  const { poolId } = useParams<{ poolId?: string }>();
  return poolId ? <PoolDetail poolId={poolId} /> : <PoolsList />;
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

function PoolsList(): JSX.Element {
  const qc = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const list = useQuery({
    queryKey: ['hr', 'pools', 'list', showArchived],
    queryFn: () => listPools(showArchived),
  });
  const invalidate = () => void qc.invalidateQueries({ queryKey: ['hr', 'pools'] });

  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const createMut = useMutation({
    mutationFn: () => createPool({ name: newName.trim(), description: newDesc.trim() || null }),
    onSuccess: () => {
      toast.success('Pool created');
      setNewOpen(false);
      setNewName('');
      setNewDesc('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not create this pool')),
  });

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const renameMut = useMutation({
    mutationFn: (id: string) => updatePool(id, { name: renameValue.trim() }),
    onSuccess: () => {
      toast.success('Pool renamed');
      setRenamingId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not rename this pool')),
  });

  const [editingDescId, setEditingDescId] = useState<string | null>(null);
  const [descValue, setDescValue] = useState('');
  const descMut = useMutation({
    mutationFn: (id: string) => updatePool(id, { description: descValue.trim() || null }),
    onSuccess: () => {
      toast.success('Description updated');
      setEditingDescId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not update the description')),
  });

  const archiveMut = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) => updatePool(id, { archived }),
    onSuccess: (_res, vars) => {
      toast.success(vars.archived ? 'Pool archived' : 'Pool restored');
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not update this pool')),
  });

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const deleteMut = useMutation({
    mutationFn: (id: string) => deletePool(id),
    onSuccess: (res) => {
      toast.success(
        res.members_removed > 0
          ? `Pool deleted, along with ${res.members_removed} member${res.members_removed === 1 ? '' : 's'}`
          : 'Pool deleted',
      );
      setConfirmDeleteId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not delete this pool')),
  });

  const pools = list.data?.pools ?? [];

  return (
    <div className="mx-auto max-w-[1080px] px-0 py-2 space-y-6">
      <Reveal>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">Talent pools</h1>
            <p className="mt-1 text-[14px] text-muted-foreground">
              Candidates you have chosen to keep in mind for future openings.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setNewOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-3.5 py-2 text-[13px] font-semibold text-primary-foreground"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            New pool
          </button>
        </div>
      </Reveal>

      {newOpen ? (
        <Reveal delay={0.05}>
          <GlassCard className="p-5 space-y-3">
            <h3 className="text-[15px] font-semibold text-foreground">New pool</h3>
            <form
              className="grid gap-3 sm:grid-cols-2"
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                if (!newName.trim()) return;
                createMut.mutate();
              }}
            >
              <div className="flex flex-col gap-1.5">
                <label htmlFor="pool-new-name" className="text-[12px] font-medium text-[var(--ui-soft)]">
                  Name
                </label>
                <input
                  id="pool-new-name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  maxLength={120}
                  placeholder="e.g. Fitters, Vizag"
                  className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="pool-new-desc" className="text-[12px] font-medium text-[var(--ui-soft)]">
                  Description (optional)
                </label>
                <input
                  id="pool-new-desc"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  maxLength={1000}
                  className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
              </div>
              <div className="sm:col-span-2 flex items-center gap-3">
                <button
                  type="submit"
                  disabled={!newName.trim() || createMut.isPending}
                  className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {createMut.isPending ? 'Creating…' : 'Create pool'}
                </button>
                <button
                  type="button"
                  onClick={() => setNewOpen(false)}
                  className="text-[13px] text-muted-foreground hover:text-foreground"
                >
                  Cancel
                </button>
              </div>
            </form>
          </GlassCard>
        </Reveal>
      ) : null}

      <Reveal delay={0.08}>
        <GlassCard className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-[15px] font-semibold text-foreground">Pools</h3>
            <label className="flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(e) => setShowArchived(e.target.checked)}
              />
              Show archived
            </label>
          </div>

          {list.isLoading ? (
            <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Loading…
            </p>
          ) : list.isError ? (
            <p className="text-[13px] text-[var(--ui-danger)]">
              {poolErrorMessage(list.error, 'Could not load your talent pools.')}
            </p>
          ) : pools.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">No pools yet</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-[12.5px]">
                <thead>
                  <tr className="border-b border-border text-[11px] uppercase tracking-wide text-[var(--ui-faint)]">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Name
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Members
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Created by
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Updated
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pools.map((p) => (
                    <tr key={p.id} className="border-b border-border align-top">
                      <td className="py-2.5 pr-3">
                        {renamingId === p.id ? (
                          <form
                            className="flex items-center gap-1.5"
                            onSubmit={(e) => {
                              e.preventDefault();
                              if (renameValue.trim()) renameMut.mutate(p.id);
                            }}
                          >
                            <input
                              autoFocus
                              value={renameValue}
                              onChange={(e) => setRenameValue(e.target.value)}
                              aria-label={`Rename ${p.name}`}
                              className="rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12.5px] text-foreground"
                            />
                            <button
                              type="submit"
                              className="text-[11.5px] font-medium text-[var(--ui-info)]"
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => setRenamingId(null)}
                              className="text-[11.5px] text-muted-foreground"
                            >
                              Cancel
                            </button>
                          </form>
                        ) : (
                          <Link
                            to={`/hr/pools/${p.id}`}
                            className="font-medium text-foreground hover:text-[var(--ui-info)] hover:underline"
                          >
                            {p.name}
                          </Link>
                        )}
                        {p.archived_at ? (
                          <StatusTag tone="neutral" className="ml-2">
                            Archived
                          </StatusTag>
                        ) : null}
                        {editingDescId === p.id ? (
                          <form
                            className="mt-1.5 flex items-center gap-1.5"
                            onSubmit={(e) => {
                              e.preventDefault();
                              descMut.mutate(p.id);
                            }}
                          >
                            <input
                              autoFocus
                              value={descValue}
                              onChange={(e) => setDescValue(e.target.value)}
                              aria-label={`Edit description for ${p.name}`}
                              className="min-w-[220px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12px] text-foreground"
                            />
                            <button
                              type="submit"
                              className="text-[11.5px] font-medium text-[var(--ui-info)]"
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingDescId(null)}
                              className="text-[11.5px] text-muted-foreground"
                            >
                              Cancel
                            </button>
                          </form>
                        ) : p.description ? (
                          <p className="mt-0.5 text-[11.5px] text-muted-foreground">{p.description}</p>
                        ) : null}
                      </td>
                      <td className="py-2.5 pr-3 text-foreground">{p.member_count}</td>
                      <td className="py-2.5 pr-3 text-muted-foreground">{p.created_by_name ?? '—'}</td>
                      <td className="py-2.5 pr-3 text-muted-foreground">{fmtDate(p.updated_at)}</td>
                      <td className="py-2.5 pr-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => {
                              setRenamingId(p.id);
                              setRenameValue(p.name);
                            }}
                            className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground"
                          >
                            <Pencil className="h-3 w-3" aria-hidden="true" />
                            Rename
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setEditingDescId(p.id);
                              setDescValue(p.description ?? '');
                            }}
                            className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground"
                          >
                            Edit description
                          </button>
                          <button
                            type="button"
                            onClick={() => archiveMut.mutate({ id: p.id, archived: !p.archived_at })}
                            disabled={archiveMut.isPending}
                            className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground disabled:opacity-40"
                          >
                            {p.archived_at ? (
                              <>
                                <ArchiveRestore className="h-3 w-3" aria-hidden="true" />
                                Restore
                              </>
                            ) : (
                              <>
                                <Archive className="h-3 w-3" aria-hidden="true" />
                                Archive
                              </>
                            )}
                          </button>
                          {confirmDeleteId === p.id ? (
                            <span
                              role="group"
                              aria-label={`Confirm deleting ${p.name}`}
                              className="flex flex-col gap-1"
                            >
                              <span className="text-[11px] text-[var(--ui-danger)]">
                                Delete this pool and its {p.member_count} member
                                {p.member_count === 1 ? '' : 's'}?
                              </span>
                              <span className="flex gap-1.5">
                                <button
                                  type="button"
                                  onClick={() => deleteMut.mutate(p.id)}
                                  disabled={deleteMut.isPending}
                                  className="rounded-[8px] bg-[#e6714f]/20 px-2 py-1 text-[11px] font-semibold text-[#ff8a66] disabled:opacity-50"
                                >
                                  {deleteMut.isPending ? '…' : 'Delete'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setConfirmDeleteId(null)}
                                  className="rounded-[8px] bg-[var(--ui-inset)] px-2 py-1 text-[11px] text-[var(--ui-soft)]"
                                >
                                  Cancel
                                </button>
                              </span>
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteId(p.id)}
                              className="inline-flex items-center gap-1 rounded-[8px] border border-[#e6714f]/30 px-2 py-1 text-[11.5px] text-[#ff8a66]"
                            >
                              <Trash2 className="h-3 w-3" aria-hidden="true" />
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail — one pool's members
// ---------------------------------------------------------------------------

function PoolDetail({ poolId }: { poolId: string }): JSX.Element {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ['hr', 'pools', 'detail', poolId],
    queryFn: () => getPool(poolId),
  });
  const invalidate = () => void qc.invalidateQueries({ queryKey: ['hr', 'pools', 'detail', poolId] });

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [removeReason, setRemoveReason] = useState('');
  const [invitingMemberId, setInvitingMemberId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const removeMut = useMutation({
    mutationFn: ({ memberId, reason }: { memberId: string; reason: string }) =>
      removePoolMember(poolId, memberId, reason || null),
    onSuccess: () => {
      toast.success('Removed from pool');
      setConfirmRemoveId(null);
      setRemoveReason('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not remove this member')),
  });

  const reviewedMut = useMutation({
    mutationFn: (memberId: string) => markEvidenceReviewed(poolId, memberId),
    onSuccess: () => {
      toast.success('Marked as reviewed');
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not update this member')),
  });

  const members = detail.data?.members ?? [];
  // Depends on `detail.data?.members` (stable across re-renders until the
  // query actually refetches), not the `members` fallback above — that `??
  // []` makes a fresh array every render, which would recompute this on
  // every keystroke in the dialog for no reason.
  const existingApplicantIds = useMemo(
    () => new Set((detail.data?.members ?? []).map((m) => m.applicant_id)),
    [detail.data?.members],
  );

  return (
    <div className="mx-auto max-w-[1080px] px-0 py-2 space-y-6">
      <Reveal>
        <Link
          to="/hr/pools"
          className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Talent pools
        </Link>
        <h1 className="mt-2 text-[28px] font-semibold tracking-[-1px] text-foreground">
          {detail.data?.pool.name ?? 'Pool'}
        </h1>
        {detail.data?.pool.description ? (
          <p className="mt-1 text-[14px] text-muted-foreground">{detail.data.pool.description}</p>
        ) : null}
      </Reveal>

      <Reveal delay={0.05}>
        <GlassCard className="p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
              <Users className="h-4 w-4" aria-hidden="true" />
              Members
            </h3>
            <button
              type="button"
              onClick={() => setAddOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-3.5 py-2 text-[13px] font-semibold text-primary-foreground"
            >
              <UserPlus className="h-3.5 w-3.5" aria-hidden="true" />
              {t('hrPools.addCandidates')}
            </button>
          </div>

          {addOpen ? (
            <AddCandidatesDialog
              poolId={poolId}
              poolName={detail.data?.pool.name ?? ''}
              existingApplicantIds={existingApplicantIds}
              onClose={() => setAddOpen(false)}
              onAdded={invalidate}
            />
          ) : null}

          {detail.isLoading ? (
            <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Loading…
            </p>
          ) : detail.isError ? (
            <p className="text-[13px] text-[var(--ui-danger)]">
              {poolErrorMessage(detail.error, 'Could not load this pool.')}
            </p>
          ) : members.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">
              {t('hrPools.emptyState')}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-[12.5px]">
                <thead>
                  <tr className="border-b border-border text-[11px] uppercase tracking-wide text-[var(--ui-faint)]">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Name
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Added
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Source
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Evidence
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Eligibility
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <MemberRow
                      key={m.member_id}
                      poolId={poolId}
                      member={m}
                      expanded={expandedId === m.member_id}
                      onToggle={() => setExpandedId(expandedId === m.member_id ? null : m.member_id)}
                      onReview={() => reviewedMut.mutate(m.member_id)}
                      reviewPending={reviewedMut.isPending && reviewedMut.variables === m.member_id}
                      confirmingRemove={confirmRemoveId === m.member_id}
                      onRemoveClick={() => setConfirmRemoveId(m.member_id)}
                      removeReason={removeReason}
                      onRemoveReasonChange={setRemoveReason}
                      onRemoveConfirm={() =>
                        removeMut.mutate({ memberId: m.member_id, reason: removeReason })
                      }
                      onRemoveCancel={() => {
                        setConfirmRemoveId(null);
                        setRemoveReason('');
                      }}
                      removePending={removeMut.isPending}
                      invitingOpen={invitingMemberId === m.member_id}
                      onInviteClick={() => setInvitingMemberId(m.member_id)}
                      onInviteClose={() => setInvitingMemberId(null)}
                      onInvited={invalidate}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}

interface MemberRowProps {
  poolId: string;
  member: PoolMemberOut;
  expanded: boolean;
  onToggle: () => void;
  onReview: () => void;
  reviewPending: boolean;
  confirmingRemove: boolean;
  onRemoveClick: () => void;
  removeReason: string;
  onRemoveReasonChange: (v: string) => void;
  onRemoveConfirm: () => void;
  onRemoveCancel: () => void;
  removePending: boolean;
  invitingOpen: boolean;
  onInviteClick: () => void;
  onInviteClose: () => void;
  onInvited: () => void;
}

function MemberRow(props: MemberRowProps): JSX.Element {
  const { poolId, member: m } = props;
  const hasSnapshot = Boolean(m.match_reason);

  return (
    <Fragment>
      <tr className="border-b border-border align-top">
        <td className="py-2.5 pr-3">
          {m.eligible ? (
            <Link
              to={`/hr/applicants/${m.applicant_id}`}
              className="font-medium text-foreground hover:text-[var(--ui-info)] hover:underline"
            >
              {m.full_name}
            </Link>
          ) : (
            <span className="font-medium text-foreground">{m.full_name}</span>
          )}
          {m.current_title || m.current_company ? (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {[m.current_title, m.current_company].filter(Boolean).join(' · ')}
            </p>
          ) : null}
          {m.note ? <p className="mt-1 text-[11px] italic text-muted-foreground">“{m.note}”</p> : null}
        </td>
        <td className="py-2.5 pr-3 text-muted-foreground">
          {fmtDate(m.added_at)}
          {m.added_by_name ? ` · ${m.added_by_name}` : ''}
        </td>
        <td className="py-2.5 pr-3">
          <StatusTag tone={m.source === 'rediscovery' ? 'electric' : 'neutral'}>
            {m.source === 'rediscovery' ? 'From rediscovery' : 'Manual'}
          </StatusTag>
        </td>
        <td className="py-2.5 pr-3">
          {m.evidence_freshness ? (
            <StatusTag tone={FRESHNESS_TONE[m.evidence_freshness]} dot>
              {freshnessChipLabel(m.evidence_freshness)}
            </StatusTag>
          ) : (
            <span className="text-[11.5px] text-muted-foreground">—</span>
          )}
          <p className="mt-1 text-[11px] text-muted-foreground">
            {m.evidence_reviewed_at
              ? `Evidence re-checked${m.evidence_reviewed_by_name ? ` by ${m.evidence_reviewed_by_name}` : ''} on ${fmtDate(m.evidence_reviewed_at)}`
              : 'Evidence not re-checked'}
          </p>
          {hasSnapshot ? (
            <button
              type="button"
              onClick={props.onToggle}
              className="mt-1 inline-flex items-center gap-1 text-[11px] text-[var(--ui-info)] hover:underline"
            >
              {props.expanded ? (
                <ChevronDown className="h-3 w-3" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3 w-3" aria-hidden="true" />
              )}
              {props.expanded ? 'Hide match details' : 'Why this match?'}
            </button>
          ) : null}
        </td>
        <td className="py-2.5 pr-3">
          {m.eligible ? (
            <StatusTag tone="forest">
              {m.expires_at ? `Opted in until ${fmtDate(m.expires_at)}` : 'Opted in'}
            </StatusTag>
          ) : (
            <StatusTag tone="ember">{ineligibleReasonLabel(m.ineligible_reason)}</StatusTag>
          )}
        </td>
        <td className="py-2.5 pr-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {m.eligible ? (
              <>
                {m.enrolment_id ? (
                  <Link
                    to={`/hr/enrolments/${m.enrolment_id}/evidence`}
                    className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground"
                  >
                    <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                    Review evidence
                  </Link>
                ) : null}
                {!m.evidence_reviewed_at ? (
                  <button
                    type="button"
                    onClick={props.onReview}
                    disabled={props.reviewPending}
                    className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground disabled:opacity-40"
                  >
                    Mark evidence reviewed
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={props.onInviteClick}
                  className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground"
                >
                  Invite to an opening
                </button>
              </>
            ) : null}
            {props.confirmingRemove ? (
              <span
                role="group"
                aria-label={`Confirm removing ${m.full_name}`}
                className="flex flex-col gap-1"
              >
                <input
                  value={props.removeReason}
                  onChange={(e) => props.onRemoveReasonChange(e.target.value)}
                  placeholder="Reason (optional)"
                  aria-label={`Reason for removing ${m.full_name}`}
                  className="rounded-[8px] border border-border bg-secondary px-2 py-1 text-[11.5px] text-foreground"
                />
                <span className="flex gap-1.5">
                  <button
                    type="button"
                    onClick={props.onRemoveConfirm}
                    disabled={props.removePending}
                    className="rounded-[8px] bg-[#e6714f]/20 px-2 py-1 text-[11px] font-semibold text-[#ff8a66] disabled:opacity-50"
                  >
                    {props.removePending ? '…' : 'Remove'}
                  </button>
                  <button
                    type="button"
                    onClick={props.onRemoveCancel}
                    className="rounded-[8px] bg-[var(--ui-inset)] px-2 py-1 text-[11px] text-[var(--ui-soft)]"
                  >
                    Cancel
                  </button>
                </span>
              </span>
            ) : (
              <button
                type="button"
                onClick={props.onRemoveClick}
                className="inline-flex items-center gap-1 rounded-[8px] border border-[#e6714f]/30 px-2 py-1 text-[11.5px] text-[#ff8a66]"
              >
                <UserMinus className="h-3 w-3" aria-hidden="true" />
                Remove
              </button>
            )}
          </div>
          {props.invitingOpen ? (
            <InviteDialog
              poolId={poolId}
              memberId={m.member_id}
              onClose={props.onInviteClose}
              onInvited={props.onInvited}
            />
          ) : null}
        </td>
      </tr>
      {props.expanded && m.match_reason ? (
        <tr className="border-b border-border bg-[var(--ui-inset-soft)]">
          <td colSpan={6} className="p-3">
            <p className="mb-2 text-[12px] text-muted-foreground">
              Added from a rediscovery search on {fmtDate(m.match_reason.frozen_at)}. This is a
              snapshot from that search and is not recalculated.
            </p>
            <RediscoveryWhyPanel items={m.match_reason.why} />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

// ---------------------------------------------------------------------------
// Invite to an opening — the criterion-14 gate reacts to the server's own
// 422, never pre-computes it.
// ---------------------------------------------------------------------------

function InviteDialog({
  poolId,
  memberId,
  onClose,
  onInvited,
}: {
  poolId: string;
  memberId: string;
  onClose: () => void;
  onInvited: () => void;
}): JSX.Element {
  const openings = useQuery({
    queryKey: ['hr', 'requisitions', 'open-for-invite'],
    queryFn: () => listRequisitions({ status: 'open' }),
  });
  const [requisitionId, setRequisitionId] = useState('');
  const [needsAck, setNeedsAck] = useState(false);

  const inviteMut = useMutation({
    mutationFn: (acknowledgedStale: boolean) =>
      inviteMember(poolId, memberId, { requisitionId, acknowledgedStale }),
    onSuccess: (res) => {
      toast.success(res.already_enrolled ? 'Already invited to this opening' : 'Invited');
      onInvited();
      onClose();
    },
    onError: (e: unknown) => {
      if (isStaleEvidenceUnreviewed(e)) {
        setNeedsAck(true);
        return;
      }
      toast.error(poolErrorMessage(e, 'Could not invite this candidate'));
    },
  });

  return (
    <div
      className="mt-2 rounded-[10px] border border-border bg-card p-3"
      role="group"
      aria-label="Invite to an opening"
    >
      {needsAck ? (
        <div className="space-y-2">
          <p className="flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This evidence has not been reviewed recently. Invite this candidate anyway?
          </p>
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={() => inviteMut.mutate(true)}
              disabled={inviteMut.isPending}
              className="rounded-[8px] bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
            >
              {inviteMut.isPending ? '…' : 'Invite anyway'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-[8px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor={`invite-req-${memberId}`}>
            Opening
          </label>
          <select
            id={`invite-req-${memberId}`}
            value={requisitionId}
            onChange={(e) => setRequisitionId(e.target.value)}
            className="rounded-[8px] border border-border bg-secondary px-2 py-1.5 text-[12px] text-foreground"
          >
            <option value="">Choose an opening…</option>
            {(openings.data ?? []).map((r: Requisition) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => requisitionId && inviteMut.mutate(false)}
            disabled={!requisitionId || inviteMut.isPending}
            className="rounded-[8px] bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
          >
            {inviteMut.isPending ? '…' : 'Invite'}
          </button>
          <button type="button" onClick={onClose} className="text-[12px] text-muted-foreground">
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add candidates (criterion 2: candidates can be added to a pool manually) —
// searches this company's own applicants (the same `listApplicants` the
// Applicants screen uses) and adds the selection with `source: 'manual'` and
// NO `match_reason` — a manual add has no computed match, so nothing here
// fabricates one (see `api/pools.ts::AddMembersInput`'s own note).
// ---------------------------------------------------------------------------

function AddCandidatesDialog({
  poolId,
  poolName,
  existingApplicantIds,
  onClose,
  onAdded,
}: {
  poolId: string;
  poolName: string;
  existingApplicantIds: Set<string>;
  onClose: () => void;
  onAdded: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  const panelRef = useDialogFocus<HTMLDivElement>(onClose);
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebouncedValue(query, 300);
  const trimmedQuery = debouncedQuery.trim();
  const [selected, setSelected] = useState<Record<string, Applicant>>({});
  const [note, setNote] = useState('');
  const [skipped, setSkipped] = useState<
    Array<{ applicantId: string; name: string; reason: AddMemberSkipReason }>
  >([]);

  const applicantsQuery = useQuery({
    queryKey: ['hr', 'applicants', 'pool-picker', trimmedQuery],
    queryFn: () => listApplicants({ q: trimmedQuery || undefined }),
  });

  // Already-in-this-pool applicants never show up as a fresh choice — the
  // server would only tell us `already_a_member` for one anyway.
  const results = (applicantsQuery.data ?? []).filter((a) => !existingApplicantIds.has(a.id));
  const selectedIds = Object.keys(selected);

  function toggle(a: Applicant): void {
    setSelected((prev) => {
      const next = { ...prev };
      if (next[a.id]) delete next[a.id];
      else next[a.id] = a;
      return next;
    });
  }

  const addMut = useMutation({
    mutationFn: () =>
      addPoolMembers(poolId, {
        applicantIds: selectedIds,
        source: 'manual',
        note: note.trim() || null,
      }),
    onSuccess: (res) => {
      if (res.added.length > 0) {
        toast.success(t('hrPools.addedToast', { count: res.added.length }));
        onAdded();
      }
      // Drop everything the server just processed (added or skipped) from
      // the selection — resubmitting an unchanged skip would only skip again.
      setSelected((prev) => {
        const next = { ...prev };
        res.added.forEach((id) => delete next[id]);
        res.skipped.forEach((s) => delete next[s.applicant_id]);
        return next;
      });
      if (res.skipped.length > 0) {
        // Surfaced, not swallowed: named per candidate, in words an HR
        // manager can act on, and left on screen rather than a toast alone.
        toast.error(t('hrPools.skippedToast', { count: res.skipped.length }));
        setSkipped(
          res.skipped.map((s) => ({
            applicantId: s.applicant_id,
            name: selected[s.applicant_id]?.full_name ?? s.applicant_id,
            reason: s.reason,
          })),
        );
      } else {
        setSkipped([]);
        setNote('');
        onClose();
      }
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, t('hrPools.couldNotAdd'))),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <button
        type="button"
        aria-label={t('hrPools.close')}
        onClick={onClose}
        className="absolute inset-0"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-candidates-title"
        tabIndex={-1}
        className="relative z-10 max-h-[85vh] w-full max-w-[480px] space-y-3 overflow-y-auto rounded-[24px] border border-border bg-card p-5 outline-none"
      >
        <h3 id="add-candidates-title" className="text-[15px] font-semibold text-foreground">
          {t('hrPools.dialogTitle', { pool: poolName })}
        </h3>

        <div>
          <label htmlFor="pool-add-search" className="sr-only">
            {t('hrPools.searchLabel')}
          </label>
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              id="pool-add-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('hrPools.searchPlaceholder')}
              className="w-full rounded-[10px] border border-border bg-secondary py-2 pl-8 pr-3 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
            />
          </div>
        </div>

        <div className="max-h-[240px] space-y-0.5 overflow-y-auto rounded-[10px] border border-border p-2">
          {applicantsQuery.isLoading ? (
            <p className="flex items-center gap-2 py-3 text-[12.5px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              {t('hrPools.loading')}
            </p>
          ) : applicantsQuery.isError ? (
            <p className="py-3 text-[12.5px] text-[var(--ui-danger)]">{t('hrPools.loadError')}</p>
          ) : results.length === 0 ? (
            <p className="py-3 text-center text-[12.5px] text-muted-foreground">
              {trimmedQuery ? t('hrPools.noResults') : t('hrPools.noApplicants')}
            </p>
          ) : (
            results.map((a) => (
              <label
                key={a.id}
                className="flex items-center gap-2 rounded-[8px] px-2 py-1.5 text-[12.5px] text-foreground hover:bg-[var(--ui-inset-soft)]"
              >
                <input type="checkbox" checked={Boolean(selected[a.id])} onChange={() => toggle(a)} />
                <span className="flex-1">
                  <span className="font-medium">{a.full_name}</span>
                  {a.current_title || a.current_company ? (
                    <span className="ml-1.5 text-[11.5px] text-muted-foreground">
                      {[a.current_title, a.current_company].filter(Boolean).join(' · ')}
                    </span>
                  ) : null}
                </span>
              </label>
            ))
          )}
        </div>

        <p className="text-[11.5px] text-muted-foreground">
          {t('hrPools.selected', { count: selectedIds.length })}
        </p>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="pool-add-note" className="text-[12px] font-medium text-[var(--ui-soft)]">
            {t('hrPools.noteLabel')}
          </label>
          <input
            id="pool-add-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={500}
            placeholder={t('hrPools.notePlaceholder')}
            className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          />
        </div>

        {skipped.length > 0 ? (
          <div
            role="status"
            className="space-y-1 rounded-[10px] border border-[#e6714f]/30 bg-[#e6714f]/10 p-2.5"
          >
            <p className="text-[11.5px] font-medium text-[#ff8a66]">{t('hrPools.notAddedHeading')}</p>
            <ul className="space-y-0.5 text-[11.5px] text-[#ff8a66]">
              {skipped.map((s) => (
                <li key={s.applicantId}>
                  {s.name} — {t(`hrPools.skipReason.${s.reason}`)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => addMut.mutate()}
            disabled={selectedIds.length === 0 || addMut.isPending}
            className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
          >
            {addMut.isPending
              ? t('hrPools.adding')
              : t('hrPools.addSelected', { count: selectedIds.length })}
          </button>
          <button type="button" onClick={onClose} className="text-[13px] text-muted-foreground">
            {t('hrPools.cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}
