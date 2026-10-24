// TalentPools (/hr/pools, /hr/pools/:poolId) — PH5-E3. `hr_manager` ONLY
// (HRRoute; a company super_admin gets 403 server-side — a pool names
// candidates, and `candidate_pii` is `{hr_manager}` alone, CLAUDE.md).
//
// One component renders both routes, on the CorpusDocuments precedent: the
// list (name, members, created by, updated, and the pool-level actions) when
// there is no `:poolId`, the member table when there is.
//
// THE THREE RULES THIS SCREEN MUST NOT BREAK:
//  1. Deleting a pool takes its members with it — the confirm names how many
//     (design §10, and the exact defect a missing count would be).
//  2. An INELIGIBLE member (`eligible: false`) shows its reason and offers
//     ONLY Remove — no evidence panel, no match explanation, no Add to an
//     opening. The server also withholds the snapshot and the band for such a
//     row (audit PL-F1); the guards below are the second line, not the only one.
//  3. `match_reason` is a FROZEN snapshot, labelled as one ("Added from a
//     rediscovery search on …") and never recomputed. No in-page evidence
//     editing of any kind — "Review evidence" only ever navigates to the
//     existing PH5-E5 screen (`/hr/enrolments/{id}/evidence`).
//
// The criterion-14 acknowledgement gate for a stale-evidence invite lives on
// the SERVER (422 `stale_evidence_unreviewed`) — this screen reacts to that
// specific failure rather than pre-computing "does this member need it?"; see
// `api/pools.ts::inviteMember`'s own comment.
//
// "Add to an opening", not "Invite" (audit PL-F4): the action creates an
// enrolment at stage `new` and sends the candidate nothing, so the copy must
// not claim they were contacted. The API route keeps its `invite` name.
//
// English-only by design (CLAUDE.md — staff consoles are not translated). The
// members section used to go through t('hrPools.*') with HI/TE values while the
// rest of the page was literal English, so a HI/TE staff user saw half a
// translation (audit PL-L5).

import {
  Fragment,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from 'react';
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
  MAX_ADD_PER_CALL,
  poolErrorMessage,
  poolFailureCode,
  removePoolMember,
  skipReasonLabel,
  updatePool,
  type AddMemberSkipReason,
  type PoolMemberOut,
} from '@/api/pools';
import { listApplicants, type Applicant } from '@/api/applicants';
import { listRequisitions, type Requisition } from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryState } from '@/components/QueryState';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { Breadcrumbs, EmptyState, PageHeader, Page } from '@/design/components/page';
import { Button, buttonVariants } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  ChevronDown,
  ChevronRight,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  UserMinus,
  UserPlus,
  Users,
} from '@/design/components/icons';
import RediscoveryWhyPanel from '@/components/hr/RediscoveryWhyPanel';
import {
  FRESHNESS_TONE,
  freshnessChipLabel,
  ineligibleReasonLabel,
} from '@/components/hr/rediscoveryDisplay';
import { formatDate } from '@/lib/formatters';

/** Server bounds (schemas/pools.py), enforced here so a long value is stopped
 *  in the field rather than coming back as a validation error (PL-F12). */
const NAME_MAX = 120;
const DESCRIPTION_MAX = 1000;
const NOTE_MAX = 500;
const REMOVE_REASON_MAX = 200;
/** The opening picker asks for the server's largest page (hr_requisitions.py
 *  `_MAX_PAGE`); the default of 50 silently dropped the rest (PL-L10/F22). */
const OPENINGS_PAGE = 200;

/** The compact row actions: outline buttons a little smaller than `sm`. */
const ROW_ACTION_CLASS = 'h-8 gap-1 px-3 text-[12px] [&_svg]:size-3';
/** A link styled as one of those row actions. */
const ROW_ACTION_LINK_CLASS = cn(buttonVariants({ variant: 'outline' }), ROW_ACTION_CLASS);
/** The compact table density used on both pool tables. */
const HEAD_CLASS = 'h-auto px-0 py-2 pr-3 font-medium';
const CELL_CLASS = 'p-0 py-2.5 pr-3';

/** Skeleton rows for a loading picker or list, with the words for a screen reader. */
function LoadingLines({ label, rows = 2 }: { label: string; rows?: number }): JSX.Element {
  return (
    <div role="status" aria-busy="true" className="flex flex-col gap-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

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

/** Focus `ref` whenever `open` goes from true to false: the control that
 *  opened an inline panel gets focus back when the panel closes, instead of
 *  focus dropping to the page (PL-L10/F29). */
function useReturnFocus(open: boolean, ref: RefObject<HTMLElement>): void {
  const wasOpen = useRef(open);
  useEffect(() => {
    if (wasOpen.current && !open) ref.current?.focus();
    wasOpen.current = open;
  }, [open, ref]);
}

export default function TalentPools(): JSX.Element {
  const { poolId } = useParams<{ poolId?: string }>();
  return poolId ? <PoolDetail poolId={poolId} /> : <PoolsList />;
}

// ---------------------------------------------------------------------------
// Inline edit form (rename, description)
// ---------------------------------------------------------------------------

/**
 * One inline text edit: Save is disabled while the save is in flight, Escape
 * cancels, and a required field left blank says so instead of doing nothing
 * (PL-L2).
 */
function InlineEditForm({
  label,
  initial,
  maxLength,
  required,
  requiredMessage,
  pending,
  onSave,
  onCancel,
  className,
  inputClassName,
}: {
  label: string;
  initial: string;
  maxLength: number;
  required: boolean;
  requiredMessage?: string;
  pending: boolean;
  onSave: (value: string) => void;
  onCancel: () => void;
  className?: string;
  inputClassName?: string;
}): JSX.Element {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  function submit(e: FormEvent): void {
    e.preventDefault();
    if (pending) return;
    if (required && !value.trim()) {
      setError(requiredMessage ?? 'This field is required.');
      inputRef.current?.focus();
      return;
    }
    onSave(value.trim());
  }

  // On the form, not the input: Escape cancels wherever focus is inside it
  // (the Save button keeps focus after a refused blank save).
  function onKeyDown(e: KeyboardEvent<HTMLFormElement>): void {
    if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  }

  return (
    <form className={className ?? 'flex flex-col gap-1'} onSubmit={submit} onKeyDown={onKeyDown}>
      <span className="flex items-center gap-1.5">
        <Input
          ref={inputRef}
          size="sm"
          autoFocus
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError(null);
          }}
          maxLength={maxLength}
          aria-label={label}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={cn('w-auto min-w-0', inputClassName)}
        />
        <Button type="submit" size="sm" variant="outline" disabled={pending} aria-busy={pending}>
          {pending ? 'Saving…' : 'Save'}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </span>
      {error ? (
        <span id={errorId} className="text-[12px] text-[var(--ui-danger)]">
          {error}
        </span>
      ) : null}
    </form>
  );
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
  const invalidate = () => void invalidateAfterHrMutation(qc);

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
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not create this pool.')),
  });

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const renameMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updatePool(id, { name }),
    onSuccess: () => {
      toast.success('Pool renamed');
      setRenamingId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not rename this pool.')),
  });

  const [editingDescId, setEditingDescId] = useState<string | null>(null);
  const descMut = useMutation({
    mutationFn: ({ id, description }: { id: string; description: string }) =>
      updatePool(id, { description: description || null }),
    onSuccess: () => {
      toast.success('Description updated');
      setEditingDescId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not update the description.')),
  });

  // Archive is reversible, so it takes no confirm; the toast offers the way
  // back instead (consistency B5).
  const archiveMut = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) =>
      updatePool(id, { archived }),
    onSuccess: (_res, vars) => {
      if (vars.archived) {
        toast.success('Pool archived', {
          action: {
            label: 'Restore',
            onClick: () => archiveMut.mutate({ id: vars.id, archived: false }),
          },
        });
      } else {
        toast.success('Pool restored');
      }
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not update this pool.')),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deletePool(id),
    onSuccess: (res) => {
      toast.success(
        res.members_removed > 0
          ? `Pool deleted, along with ${plural(res.members_removed, 'member')}`
          : 'Pool deleted',
      );
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not delete this pool.')),
  });

  // The server refuses a pool past the company limit, archived pools
  // included; say so up front instead of letting New pool lead to a 409
  // (audit F20).
  const maxPools = list.data?.limits.max_per_company;
  const poolCount = list.data?.pool_count;
  const atLimit = maxPools !== undefined && poolCount !== undefined && poolCount >= maxPools;
  const archivedCount = list.data?.archived_count ?? 0;

  return (
    <Page width="default">
      <PageHeader
        className="mb-0"
        title="Talent pools"
        description="Candidates you have chosen to keep in mind for future openings."
        actions={
          // One primary action per view (consistency A5): while the form is
          // open its own Create pool button is the primary, so this one goes.
          newOpen ? undefined : (
            <Button
              type="button"
              onClick={() => setNewOpen(true)}
              disabled={atLimit}
              aria-describedby={atLimit ? 'pool-limit-note' : undefined}
            >
              <Plus aria-hidden="true" />
              New pool
            </Button>
          )
        }
      />

      {atLimit ? (
        <p id="pool-limit-note" role="status" className="text-[12.5px] text-muted-foreground">
          Your company has reached its limit of {maxPools} pools, and archived pools count. Delete
          one you no longer need to make a new one.
        </p>
      ) : null}

      {newOpen ? (
        <Reveal delay={0.05}>
          <GlassCard className="p-5 space-y-3">
            <h3 className="text-[15px] font-semibold text-foreground">New pool</h3>
            <form
              className="grid gap-3 sm:grid-cols-2"
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                if (!newName.trim() || createMut.isPending) return;
                createMut.mutate();
              }}
            >
              <FormField id="pool-new-name" label="Name">
                {(p) => (
                  <Input
                    {...p}
                    autoFocus
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    maxLength={NAME_MAX}
                    placeholder="e.g. Fitters, Vizag"
                  />
                )}
              </FormField>
              <FormField id="pool-new-desc" label="Description (optional)">
                {(p) => (
                  <Input
                    {...p}
                    value={newDesc}
                    onChange={(e) => setNewDesc(e.target.value)}
                    maxLength={DESCRIPTION_MAX}
                  />
                )}
              </FormField>
              <div className="sm:col-span-2 flex items-center gap-2">
                <Button type="submit" disabled={!newName.trim() || createMut.isPending}>
                  {createMut.isPending ? 'Creating…' : 'Create pool'}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setNewOpen(false)}>
                  Cancel
                </Button>
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

          <QueryState
            query={list}
            errorTitle="Could not load your talent pools."
            isEmpty={(d) => d.pools.length === 0}
            empty={
              showArchived ? (
                <EmptyState
                  title="No pools yet"
                  description="Create one with New pool, or add candidates from Rediscovery."
                />
              ) : (
                // Archived pools are hidden here and still count toward the
                // company limit, so "no pools" would not always be true
                // (PL-L10/F20).
                <EmptyState
                  title="No active pools"
                  description={
                    archivedCount > 0
                      ? `Create one with New pool, or tick Show archived to see ${plural(archivedCount, 'archived pool')}.`
                      : 'Create one with New pool, or add candidates from Rediscovery.'
                  }
                />
              )
            }
          >
            {(data) => (
              <Table className="border-collapse text-left text-[12.5px]">
                <TableHeader>
                  <TableRow className="border-b border-border text-[12px] uppercase tracking-wide text-muted-foreground hover:bg-transparent">
                    {['Name', 'Members', 'Created by', 'Updated', 'Actions'].map((h) => (
                      <TableHead key={h} scope="col" className={HEAD_CLASS}>
                        {h}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.pools.map((p) => (
                    <TableRow
                      key={p.id}
                      className="border-b border-border align-top hover:bg-transparent"
                    >
                      <TableCell className={CELL_CLASS}>
                        {renamingId === p.id ? (
                          <InlineEditForm
                            label={`Rename ${p.name}`}
                            initial={p.name}
                            maxLength={NAME_MAX}
                            required
                            requiredMessage="Give the pool a name."
                            pending={renameMut.isPending}
                            onSave={(name) => renameMut.mutate({ id: p.id, name })}
                            onCancel={() => setRenamingId(null)}
                          />
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
                          <InlineEditForm
                            className="mt-1.5 flex flex-col gap-1"
                            label={`Edit description for ${p.name}`}
                            initial={p.description ?? ''}
                            maxLength={DESCRIPTION_MAX}
                            required={false}
                            pending={descMut.isPending}
                            onSave={(description) => descMut.mutate({ id: p.id, description })}
                            onCancel={() => setEditingDescId(null)}
                            inputClassName="min-w-[220px]"
                          />
                        ) : p.description ? (
                          <p className="mt-0.5 text-[12px] text-muted-foreground">
                            {p.description}
                          </p>
                        ) : null}
                      </TableCell>
                      <TableCell className="p-0 py-2.5 pr-3 text-foreground">
                        {p.member_count}
                      </TableCell>
                      <TableCell className="p-0 py-2.5 pr-3 text-muted-foreground">
                        {p.created_by_name ?? '-'}
                      </TableCell>
                      <TableCell className="p-0 py-2.5 pr-3 text-muted-foreground">
                        {formatDate(p.updated_at)}
                      </TableCell>
                      <TableCell className={CELL_CLASS}>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => setRenamingId(p.id)}
                            className={ROW_ACTION_CLASS}
                          >
                            <Pencil className="h-3 w-3" aria-hidden="true" />
                            Rename
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => setEditingDescId(p.id)}
                            className={ROW_ACTION_CLASS}
                          >
                            Edit description
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() =>
                              archiveMut.mutate({ id: p.id, archived: !p.archived_at })
                            }
                            disabled={archiveMut.isPending}
                            className={ROW_ACTION_CLASS}
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
                          </Button>
                          {/* Set apart from the reversible Archive beside it
                                (consistency A8 / PL-L7). The confirm names the
                                member count: rule 1 above. */}
                          <span className="ml-3">
                            <ConfirmDeleteButton
                              label="Delete"
                              confirmText={`Delete pool and ${plural(p.member_count, 'member')}`}
                              confirmGroupLabel={`Confirm deleting ${p.name}`}
                              onConfirm={() => deleteMut.mutate(p.id)}
                              pending={deleteMut.isPending && deleteMut.variables === p.id}
                            />
                          </span>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </QueryState>
        </GlassCard>
      </Reveal>
    </Page>
  );
}

// ---------------------------------------------------------------------------
// Detail — one pool's members
// ---------------------------------------------------------------------------

function PoolDetail({ poolId }: { poolId: string }): JSX.Element {
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ['hr', 'pools', 'detail', poolId],
    queryFn: () => getPool(poolId),
  });
  // Every member change also changes the pools list (member count, updated
  // date), so refresh by the HR prefix, not only this pool (PL-L1).
  const invalidate = () => void invalidateAfterHrMutation(qc);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [removeReason, setRemoveReason] = useState('');
  const [confirmReviewId, setConfirmReviewId] = useState<string | null>(null);
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
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not remove this member.')),
  });

  const reviewedMut = useMutation({
    mutationFn: (memberId: string) => markEvidenceReviewed(poolId, memberId),
    onSuccess: () => {
      toast.success('Marked as reviewed');
      setConfirmReviewId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not mark this review.')),
  });

  const pool = detail.data?.pool;
  const archived = Boolean(pool?.archived_at);
  // Depends on `detail.data?.members` (stable across re-renders until the
  // query actually refetches) — a `?? []` fallback would make a fresh array
  // every render and recompute this on every keystroke in the dialog.
  const existingApplicantIds = useMemo(
    () => new Set((detail.data?.members ?? []).map((m) => m.applicant_id)),
    [detail.data?.members],
  );

  return (
    <Page width="default">
      <PageHeader
        className="mb-0"
        breadcrumbs={
          <Breadcrumbs
            items={[{ label: 'Talent pools', to: '/hr/pools' }, { label: pool?.name ?? 'Pool' }]}
          />
        }
        title={pool?.name ?? 'Pool'}
        description={pool?.description ?? undefined}
      />

      {archived ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12.5px] text-muted-foreground"
        >
          <Archive className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          This pool is archived. Restore it from the pools list to add candidates or add them to an
          opening.
        </div>
      ) : null}

      <Reveal delay={0.05}>
        <GlassCard className="p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
              <Users className="h-4 w-4" aria-hidden="true" />
              Members
              {archived ? <StatusTag tone="neutral">Archived</StatusTag> : null}
            </h3>
            <Button type="button" onClick={() => setAddOpen(true)} disabled={!pool || archived}>
              <UserPlus aria-hidden="true" />
              Add candidates
            </Button>
          </div>

          {addOpen && pool ? (
            <AddCandidatesDialog
              poolId={poolId}
              poolName={pool.name}
              existingApplicantIds={existingApplicantIds}
              onClose={() => setAddOpen(false)}
              onAdded={invalidate}
            />
          ) : null}

          <QueryState
            query={detail}
            errorTitle="Could not load this pool."
            isEmpty={(d) => d.members.length === 0}
            empty={
              <EmptyState
                title="No members yet"
                description="Add candidates with the button above, or find matches with Rediscovery."
              />
            }
          >
            {(data) => (
              <Table className="border-collapse text-left text-[12.5px]">
                <TableHeader>
                  <TableRow className="border-b border-border text-[12px] uppercase tracking-wide text-muted-foreground hover:bg-transparent">
                    {['Name', 'Added', 'Source', 'Evidence', 'Eligibility', 'Actions'].map((h) => (
                      <TableHead key={h} scope="col" className={HEAD_CLASS}>
                        {h}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.members.map((m) => (
                    <MemberRow
                      key={m.member_id}
                      poolId={poolId}
                      member={m}
                      archived={archived}
                      expanded={expandedId === m.member_id}
                      onToggle={() =>
                        setExpandedId(expandedId === m.member_id ? null : m.member_id)
                      }
                      confirmingReview={confirmReviewId === m.member_id}
                      onReviewClick={() => setConfirmReviewId(m.member_id)}
                      onReviewCancel={() => setConfirmReviewId(null)}
                      onReviewConfirm={() => reviewedMut.mutate(m.member_id)}
                      reviewPending={reviewedMut.isPending && reviewedMut.variables === m.member_id}
                      confirmingRemove={confirmRemoveId === m.member_id}
                      onRemoveClick={() => {
                        setRemoveReason('');
                        setConfirmRemoveId(m.member_id);
                      }}
                      removeReason={removeReason}
                      onRemoveReasonChange={setRemoveReason}
                      onRemoveConfirm={() =>
                        removeMut.mutate({ memberId: m.member_id, reason: removeReason.trim() })
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
                </TableBody>
              </Table>
            )}
          </QueryState>
        </GlassCard>
      </Reveal>
    </Page>
  );
}

interface MemberRowProps {
  poolId: string;
  member: PoolMemberOut;
  archived: boolean;
  expanded: boolean;
  onToggle: () => void;
  confirmingReview: boolean;
  onReviewClick: () => void;
  onReviewCancel: () => void;
  onReviewConfirm: () => void;
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

/** The review line under the band: who re-checked and when, whether that
 *  review has lapsed (PL-F3), or that nobody has. */
function reviewLine(m: PoolMemberOut): string {
  if (!m.evidence_reviewed_at) return 'Evidence not re-checked';
  const when = formatDate(m.evidence_reviewed_at);
  if (!m.review_current) return `Review from ${when} has expired`;
  const by = m.evidence_reviewed_by_name ? ` by ${m.evidence_reviewed_by_name}` : '';
  return `Evidence re-checked${by} on ${when}`;
}

function MemberRow(props: MemberRowProps): JSX.Element {
  const { poolId, member: m } = props;
  // Rule 2: an ineligible row shows no evidence and no match explanation,
  // whatever the payload carries.
  const snapshot = m.eligible ? m.match_reason : undefined;
  const whyId = `pool-member-why-${m.member_id}`;

  const removeTriggerRef = useRef<HTMLButtonElement>(null);
  const reviewTriggerRef = useRef<HTMLButtonElement>(null);
  const inviteTriggerRef = useRef<HTMLButtonElement>(null);
  const reviewKeepRef = useRef<HTMLButtonElement>(null);
  useReturnFocus(props.confirmingRemove, removeTriggerRef);
  useReturnFocus(props.confirmingReview, reviewTriggerRef);
  useReturnFocus(props.invitingOpen, inviteTriggerRef);
  useEffect(() => {
    if (props.confirmingReview) reviewKeepRef.current?.focus();
  }, [props.confirmingReview]);

  return (
    <Fragment>
      <TableRow className="border-b border-border align-top hover:bg-transparent">
        <TableCell className={CELL_CLASS}>
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
            <p className="mt-0.5 text-[12px] text-muted-foreground">
              {[m.current_title, m.current_company].filter(Boolean).join(' · ')}
            </p>
          ) : null}
          {m.note ? (
            <p className="mt-1 text-[12px] italic text-muted-foreground">“{m.note}”</p>
          ) : null}
        </TableCell>
        <TableCell className="p-0 py-2.5 pr-3 text-muted-foreground">
          {formatDate(m.added_at)}
          {m.added_by_name ? ` · ${m.added_by_name}` : ''}
        </TableCell>
        <TableCell className={CELL_CLASS}>
          <StatusTag tone={m.source === 'rediscovery' ? 'electric' : 'neutral'}>
            {m.source === 'rediscovery' ? 'From rediscovery' : 'Manual'}
          </StatusTag>
        </TableCell>
        <TableCell className={CELL_CLASS}>
          {m.eligible ? (
            <>
              {m.evidence_freshness ? (
                <StatusTag tone={FRESHNESS_TONE[m.evidence_freshness]} dot>
                  {freshnessChipLabel(m.evidence_freshness)}
                </StatusTag>
              ) : (
                <span className="text-[12px] text-muted-foreground">-</span>
              )}
              <p className="mt-1 text-[12px] text-muted-foreground">{reviewLine(m)}</p>
              {snapshot ? (
                <button
                  type="button"
                  onClick={props.onToggle}
                  aria-expanded={props.expanded}
                  aria-controls={whyId}
                  className="mt-1 inline-flex items-center gap-1 text-[12px] text-[var(--ui-info)] hover:underline"
                >
                  {props.expanded ? (
                    <ChevronDown className="h-3 w-3" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="h-3 w-3" aria-hidden="true" />
                  )}
                  {props.expanded ? 'Hide match details' : 'Why this match?'}
                </button>
              ) : null}
            </>
          ) : (
            <span className="text-[12px] text-muted-foreground">-</span>
          )}
        </TableCell>
        <TableCell className={CELL_CLASS}>
          {m.eligible ? (
            <StatusTag tone="forest">
              {m.expires_at ? `Opted in until ${formatDate(m.expires_at)}` : 'Opted in'}
            </StatusTag>
          ) : (
            <StatusTag tone="ember">{ineligibleReasonLabel(m.ineligible_reason)}</StatusTag>
          )}
        </TableCell>
        <TableCell className={CELL_CLASS}>
          <div className="flex flex-wrap items-center gap-1.5">
            {m.eligible ? (
              <>
                {m.enrolment_id ? (
                  <Link
                    to={`/hr/enrolments/${m.enrolment_id}/evidence`}
                    className={ROW_ACTION_LINK_CLASS}
                  >
                    <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                    Review evidence
                  </Link>
                ) : null}
                {!m.review_current ? (
                  props.confirmingReview ? (
                    // An attestation in a person's name, so it takes a second,
                    // explicit step rather than one stray click (PL-L6).
                    <span
                      role="group"
                      aria-label={`Confirm reviewing evidence for ${m.full_name}`}
                      className="flex flex-col gap-1"
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') {
                          e.stopPropagation();
                          props.onReviewCancel();
                        }
                      }}
                    >
                      <span className="max-w-[260px] text-[12px] text-foreground">
                        Confirm you have looked at this candidate&apos;s evidence and it still
                        holds. This is recorded in your name.
                      </span>
                      <span className="flex gap-1.5">
                        <Button
                          type="button"
                          size="sm"
                          onClick={props.onReviewConfirm}
                          disabled={props.reviewPending}
                          aria-busy={props.reviewPending}
                        >
                          {props.reviewPending ? 'Saving…' : 'Confirm review'}
                        </Button>
                        <Button
                          ref={reviewKeepRef}
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={props.onReviewCancel}
                        >
                          Cancel
                        </Button>
                      </span>
                    </span>
                  ) : (
                    <Button
                      ref={reviewTriggerRef}
                      type="button"
                      variant="outline"
                      onClick={props.onReviewClick}
                      className={ROW_ACTION_CLASS}
                    >
                      Mark evidence reviewed
                    </Button>
                  )
                ) : null}
                {!props.archived ? (
                  <Button
                    ref={inviteTriggerRef}
                    type="button"
                    variant="outline"
                    onClick={props.onInviteClick}
                    aria-expanded={props.invitingOpen}
                    className={ROW_ACTION_CLASS}
                  >
                    Add to an opening
                  </Button>
                ) : null}
              </>
            ) : null}
            {props.confirmingRemove ? (
              <span
                role="group"
                aria-label={`Confirm removing ${m.full_name}`}
                className="flex flex-col gap-1"
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    e.stopPropagation();
                    props.onRemoveCancel();
                  }
                }}
              >
                <Input
                  size="sm"
                  autoFocus
                  value={props.removeReason}
                  onChange={(e) => props.onRemoveReasonChange(e.target.value)}
                  maxLength={REMOVE_REASON_MAX}
                  placeholder="Reason (optional)"
                  aria-label={`Reason for removing ${m.full_name}`}
                />
                <span className="flex gap-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    onClick={props.onRemoveConfirm}
                    disabled={props.removePending}
                    aria-busy={props.removePending}
                  >
                    {props.removePending ? 'Removing…' : 'Remove'}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={props.onRemoveCancel}>
                    Cancel
                  </Button>
                </span>
              </span>
            ) : (
              <Button
                ref={removeTriggerRef}
                type="button"
                variant="outline"
                onClick={props.onRemoveClick}
                className={cn(
                  ROW_ACTION_CLASS,
                  'border-[var(--ui-danger)]/40 text-[var(--ui-danger)] hover:text-[var(--ui-danger)] focus-visible:ring-[var(--ui-danger)]',
                )}
              >
                <UserMinus className="h-3 w-3" aria-hidden="true" />
                Remove
              </Button>
            )}
          </div>
          {props.invitingOpen && m.eligible && !props.archived ? (
            <AddToOpeningPanel
              poolId={poolId}
              member={m}
              onClose={props.onInviteClose}
              onInvited={props.onInvited}
            />
          ) : null}
        </TableCell>
      </TableRow>
      {props.expanded && snapshot ? (
        <TableRow
          id={whyId}
          className="border-b border-border bg-[var(--ui-inset-soft)] hover:bg-[var(--ui-inset-soft)]"
        >
          <TableCell colSpan={6} className="p-3">
            <p className="mb-2 text-[12px] text-muted-foreground">
              Added from a rediscovery search on {formatDate(snapshot.frozen_at || m.added_at)}.
              This is a snapshot from that search and is not recalculated.
            </p>
            <RediscoveryWhyPanel items={snapshot.why} />
          </TableCell>
        </TableRow>
      ) : null}
    </Fragment>
  );
}

// ---------------------------------------------------------------------------
// Add to an opening — the criterion-14 gate reacts to the server's own 422,
// never pre-computes it.
// ---------------------------------------------------------------------------

function AddToOpeningPanel({
  poolId,
  member,
  onClose,
  onInvited,
}: {
  poolId: string;
  member: PoolMemberOut;
  onClose: () => void;
  onInvited: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const openings = useQuery({
    queryKey: ['hr', 'requisitions', 'open-for-invite'],
    queryFn: () => listRequisitions({ status: 'open', limit: OPENINGS_PAGE }),
  });
  const [requisitionId, setRequisitionId] = useState('');
  const [needsAck, setNeedsAck] = useState(false);
  const ackRef = useRef<HTMLButtonElement>(null);
  const openingList: Requisition[] = openings.data ?? [];
  const chosen = openingList.find((r) => r.id === requisitionId);

  useEffect(() => {
    if (needsAck) ackRef.current?.focus();
  }, [needsAck]);

  const inviteMut = useMutation({
    mutationFn: (acknowledgedStale: boolean) =>
      inviteMember(poolId, member.member_id, { requisitionId, acknowledgedStale }),
    onSuccess: (res) => {
      const title = chosen?.title ?? 'the opening';
      toast.success(res.already_enrolled ? `Already in ${title}` : `Added to ${title}`);
      onInvited();
      onClose();
    },
    onError: (e: unknown) => {
      if (isStaleEvidenceUnreviewed(e)) {
        setNeedsAck(true);
        return;
      }
      if (poolFailureCode(e) === 'requisition_not_open') {
        // The list was stale: refresh it so the closed opening drops out.
        setRequisitionId('');
        setNeedsAck(false);
        void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
      }
      toast.error(poolErrorMessage(e, 'Could not add this candidate to the opening.'));
    },
  });

  return (
    <div
      className="mt-2 rounded-[10px] border border-border bg-card p-3"
      role="group"
      aria-label={`Add ${member.full_name} to an opening`}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      {needsAck ? (
        <div className="space-y-2">
          <p className="flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This candidate has no recently reviewed evidence. Review it first, or add them to the
            opening anyway. Adding them anyway is recorded.
          </p>
          <div className="flex gap-1.5">
            <Button
              ref={ackRef}
              type="button"
              size="sm"
              variant="outline"
              onClick={() => inviteMut.mutate(true)}
              disabled={inviteMut.isPending}
              aria-busy={inviteMut.isPending}
            >
              {inviteMut.isPending ? 'Adding…' : 'Add anyway'}
            </Button>
            {/* The way to clear the gate properly: open the evidence (F21).
                Not a one-click "mark reviewed" here, which would attest to
                evidence nobody opened (PL-L6). */}
            {member.enrolment_id ? (
              <Link
                to={`/hr/enrolments/${member.enrolment_id}/evidence`}
                className={ROW_ACTION_LINK_CLASS}
              >
                <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                Review evidence
              </Link>
            ) : null}
            <Button type="button" size="sm" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </div>
        </div>
      ) : openings.isLoading ? (
        <LoadingLines label="Loading openings…" rows={1} />
      ) : openings.isError ? (
        <div className="flex flex-wrap items-center gap-2" role="alert">
          <p className="text-[12px] text-[var(--ui-danger)]">
            {errorMessage(openings.error, 'Could not load your openings.')}
          </p>
          <Button type="button" size="sm" variant="outline" onClick={() => void openings.refetch()}>
            Try again
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      ) : openingList.length === 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[12px] text-muted-foreground">
            No openings are open right now. Open one first, then add this candidate to it.
          </p>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor={`invite-req-${member.member_id}`}>
            Opening
          </label>
          <SelectControl
            size="sm"
            autoFocus
            id={`invite-req-${member.member_id}`}
            value={requisitionId}
            onChange={(e) => setRequisitionId(e.target.value)}
            className="w-auto"
          >
            <option value="">Choose an opening…</option>
            {openingList.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </SelectControl>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => requisitionId && inviteMut.mutate(false)}
            disabled={!requisitionId || inviteMut.isPending}
            aria-busy={inviteMut.isPending}
          >
            {inviteMut.isPending ? 'Adding…' : 'Add'}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {openingList.length >= OPENINGS_PAGE ? (
            <p className="w-full text-[12px] text-muted-foreground">
              Showing the first {OPENINGS_PAGE} open openings.
            </p>
          ) : null}
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
  // The server takes at most MAX_ADD_PER_CALL ids per call; stop the picker
  // there rather than let the add come back as a validation error (PL-F12).
  const atCap = selectedIds.length >= MAX_ADD_PER_CALL;

  function toggle(a: Applicant): void {
    setSelected((prev) => {
      const next = { ...prev };
      if (next[a.id]) delete next[a.id];
      else if (Object.keys(next).length < MAX_ADD_PER_CALL) next[a.id] = a;
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
        toast.success(`Added ${plural(res.added.length, 'candidate')} to the pool`);
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
        toast.error(`${plural(res.skipped.length, 'candidate')} could not be added.`);
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
    onError: (e: unknown) =>
      toast.error(poolErrorMessage(e, 'Could not add candidates to this pool.')),
  });

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-[480px] space-y-3">
        <DialogHeader>
          <DialogTitle className="text-[17px] font-bold">Add candidates to {poolName}</DialogTitle>
        </DialogHeader>

        {/* A manual add needs no opt-in, but adding someone to an opening
            later does (PL-F6): say so before they are added, not after. */}
        <p className="text-[12px] text-muted-foreground">
          Anyone can be kept on a pool. Only candidates who opted in to future openings can later be
          added to an opening from it.
        </p>

        <div>
          <label htmlFor="pool-add-search" className="sr-only">
            Search your applicants
          </label>
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              id="pool-add-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={200}
              placeholder="Search by name, title or company…"
              className="pl-9"
            />
          </div>
        </div>

        <div className="max-h-[240px] space-y-0.5 overflow-y-auto rounded-[10px] border border-border p-2">
          {applicantsQuery.isLoading ? (
            <LoadingLines label="Loading applicants…" rows={3} />
          ) : applicantsQuery.isError ? (
            <div className="flex flex-wrap items-center gap-2 py-3" role="alert">
              <p className="text-[12.5px] text-[var(--ui-danger)]">
                {errorMessage(applicantsQuery.error, 'Could not load applicants.')}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void applicantsQuery.refetch()}
              >
                Try again
              </Button>
            </div>
          ) : results.length === 0 ? (
            <p className="py-3 text-center text-[12.5px] text-muted-foreground">
              {trimmedQuery
                ? 'No applicants match this search.'
                : 'Your company has no applicants yet.'}
            </p>
          ) : (
            results.map((a) => {
              const checked = Boolean(selected[a.id]);
              return (
                <label
                  key={a.id}
                  className="flex items-center gap-2 rounded-[8px] px-2 py-1.5 text-[12.5px] text-foreground hover:bg-[var(--ui-inset-soft)]"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!checked && atCap}
                    onChange={() => toggle(a)}
                  />
                  <span className="flex-1">
                    <span className="font-medium">{a.full_name}</span>
                    {a.current_title || a.current_company ? (
                      <span className="ml-1.5 text-[12px] text-muted-foreground">
                        {[a.current_title, a.current_company].filter(Boolean).join(' · ')}
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })
          )}
        </div>

        <p className="text-[12px] text-muted-foreground" aria-live="polite">
          {selectedIds.length} selected
          {atCap ? `. You can add up to ${MAX_ADD_PER_CALL} at a time.` : ''}
        </p>

        <FormField id="pool-add-note" label="Note (optional)">
          {(p) => (
            <Input
              {...p}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={NOTE_MAX}
              placeholder="Why you are adding them"
            />
          )}
        </FormField>

        {skipped.length > 0 ? (
          <div
            role="status"
            className="space-y-1 rounded-[10px] border border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)] p-2.5"
          >
            <p className="text-[12px] font-medium text-[var(--ui-danger)]">Not added</p>
            <ul className="space-y-0.5 text-[12px] text-[var(--ui-danger)]">
              {skipped.map((s) => (
                <li key={s.applicantId}>
                  {s.name}: {skipReasonLabel(s.reason)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <DialogFooter className="flex-row items-center justify-end gap-2 sm:space-x-0">
          <Button type="button" variant="ghost" onClick={onClose}>
            {skipped.length > 0 ? 'Close' : 'Cancel'}
          </Button>
          <Button
            type="button"
            onClick={() => addMut.mutate()}
            disabled={selectedIds.length === 0 || addMut.isPending}
          >
            {addMut.isPending ? 'Adding…' : `Add ${plural(selectedIds.length, 'candidate')}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
