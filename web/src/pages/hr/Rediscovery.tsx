// Rediscovery (/hr/rediscovery) — PH5-E3. `hr_manager` ONLY (HRRoute; see
// TalentPools.tsx's header for why a company super_admin never reaches this
// screen — a result names a candidate, and `candidate_pii` is `{hr_manager}`
// alone).
//
// THREE LINES THIS SCREEN MUST NEVER SOFTEN, because they carry the honesty
// D5-1's opt-in design depends on (design §10.3):
//  1. The empty-universe state, verbatim below.
//  2. The always-on "Searching N of M applicants" counter — shown whenever
//     the universe is known, not only after a search.
//  3. "Semantic search is unavailable — matching keywords only" when a
//     search's own `semantic` flag comes back false.
//
// A row whose only contribution is CV similarity (`explained: false`) renders
// in its OWN section, below the explained ones — never interleaved, and never
// silently dropped. Its `why` panel still opens; it just has nothing to name.
//
// `resume_terms.snippet` renders through `RediscoveryWhyPanel`, which parses
// `[[…]]` bracket markers itself. Nothing in this file touches innerHTML.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getRediscoveryUniverse,
  rediscoveryErrorMessage,
  searchRediscovery,
  type RediscoveryResult,
} from '@/api/rediscovery';
import {
  addPoolMembers,
  createPool,
  listPools,
  matchReasonFromResult,
  poolErrorMessage,
  skipReasonLabel,
  type PoolOut,
} from '@/api/pools';
import { listRequisitions } from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { formatScore } from '@/lib/formatters';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { QueryErrorPanel } from '@/components/QueryState';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { PageHeader, Page } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Search,
  Users,
} from '@/design/components/icons';
import RediscoveryWhyPanel from '@/components/hr/RediscoveryWhyPanel';
import { FRESHNESS_TONE, freshnessChipLabel } from '@/components/hr/rediscoveryDisplay';

const EMPTY_UNIVERSE_TEXT =
  'No candidates have opted in yet. Rediscovery searches only candidates who chose to be kept for future openings. New applicants can opt in on your application form from today; candidates who applied earlier appear here only if they opt in themselves.';
const KEYWORD_ONLY_TEXT = 'Semantic search is unavailable - matching keywords only';
const UNEXPLAINED_HEADING = 'Matched on similarity only - nothing here says why';
/** The server's largest requisitions page (hr_requisitions.py `_MAX_PAGE`);
 *  the default of 50 silently dropped the rest (audit F22). */
const OPENINGS_PAGE = 200;
const POOL_NAME_MAX = 120;

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

export default function Rediscovery(): JSX.Element {
  const universeQuery = useQuery({
    queryKey: ['hr', 'rediscovery', 'universe'],
    queryFn: getRediscoveryUniverse,
  });
  const openingsQuery = useQuery({
    queryKey: ['hr', 'requisitions', 'open-for-rediscovery'],
    queryFn: () => listRequisitions({ status: 'open', limit: OPENINGS_PAGE }),
  });

  const [query, setQuery] = useState('');
  const [requisitionId, setRequisitionId] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dialogRows, setDialogRows] = useState<RediscoveryResult[] | null>(null);

  const searchMut = useMutation({
    mutationFn: () =>
      searchRediscovery({ query: query.trim(), requisitionId: requisitionId || null }),
    onSuccess: () => setSelected(new Set()),
    onError: (e: unknown) => toast.error(rediscoveryErrorMessage(e, 'Could not run that search.')),
  });

  function handleSubmit(e: FormEvent): void {
    e.preventDefault();
    if (!query.trim()) return;
    searchMut.mutate();
  }

  function toggleSelected(id: string): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /** Drop the people a pool add has dealt with (added, or skipped for a
   *  reason that would only repeat) from the selection, so the bulk bar does
   *  not go on offering to add them again (audit PL-F11). */
  function unselect(ids: string[]): void {
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.delete(id));
      return next;
    });
  }

  const universe = searchMut.data?.universe ?? universeQuery.data?.universe;
  const eligible = universe?.eligible ?? 0;
  const total = universe?.total ?? 0;
  // The universe is knowable the moment the page loads, before any search —
  // that is what makes the counter "always-on" rather than a search result.
  const showEmptyUniverse = universeQuery.isSuccess && eligible === 0;

  const results = searchMut.data?.results ?? [];
  const explained = results.filter((r) => r.explained);
  const unexplained = results.filter((r) => !r.explained);
  const selectedRows = results.filter((r) => selected.has(r.applicant_id));
  const openings = openingsQuery.data ?? [];

  return (
    <Page width="default">
      <PageHeader
        className="mb-0"
        title="Rediscovery"
        description="Search candidates who chose to be kept in mind for future openings."
      />

      {universeQuery.isLoading ? (
        <p className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Checking how many candidates can be searched…
        </p>
      ) : universeQuery.isError ? (
        <QueryErrorPanel
          title="Could not check how many candidates are searchable."
          error={universeQuery.error}
          onRetry={() => void universeQuery.refetch()}
          retrying={universeQuery.isFetching}
        />
      ) : universeQuery.data ? (
        // "The rest" also covers withdrawn, expired and erasure-requested
        // consent, so the counter says so (audit F24).
        <p role="status" className="text-[12.5px] text-muted-foreground">
          Searching {eligible.toLocaleString('en-IN')} of {total.toLocaleString('en-IN')}{' '}
          applicants - the rest have not opted in, or their consent has lapsed.
        </p>
      ) : null}

      {showEmptyUniverse ? (
        <Reveal delay={0.05}>
          <GlassCard className="p-8 text-center">
            <Users className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden="true" />
            <p className="mx-auto mt-4 max-w-[60ch] text-[13.5px] leading-relaxed text-muted-foreground">
              {EMPTY_UNIVERSE_TEXT}
            </p>
          </GlassCard>
        </Reveal>
      ) : (
        <>
          <Reveal delay={0.05}>
            <GlassCard className="p-5">
              <form className="flex flex-wrap items-end gap-3" onSubmit={handleSubmit}>
                <div className="flex min-w-[220px] flex-1 flex-col gap-1.5">
                  <label
                    htmlFor="rediscovery-query"
                    className="text-[12px] font-medium text-[var(--ui-soft)]"
                  >
                    Search
                  </label>
                  <input
                    id="rediscovery-query"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="e.g. hydraulic press maintenance"
                    maxLength={500}
                    className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  />
                </div>
                <div className="flex min-w-[200px] flex-col gap-1.5">
                  <label
                    htmlFor="rediscovery-opening"
                    className="text-[12px] font-medium text-[var(--ui-soft)]"
                  >
                    For this opening (optional)
                  </label>
                  <select
                    id="rediscovery-opening"
                    value={requisitionId}
                    onChange={(e) => setRequisitionId(e.target.value)}
                    disabled={openingsQuery.isLoading}
                    aria-describedby={
                      openingsQuery.isError || openingsQuery.isSuccess
                        ? 'rediscovery-opening-help'
                        : undefined
                    }
                    className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
                  >
                    <option value="">
                      {openingsQuery.isLoading ? 'Loading openings…' : 'No opening'}
                    </option>
                    {openings.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.title}
                      </option>
                    ))}
                  </select>
                </div>
                <Button type="submit" disabled={!query.trim() || searchMut.isPending}>
                  <Search aria-hidden="true" />
                  {searchMut.isPending ? 'Searching…' : 'Search'}
                </Button>
              </form>
              {openingsQuery.isError ? (
                <p
                  id="rediscovery-opening-help"
                  role="alert"
                  className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-[var(--ui-danger)]"
                >
                  {errorMessage(openingsQuery.error, 'Could not load your openings.')}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void openingsQuery.refetch()}
                  >
                    Try again
                  </Button>
                </p>
              ) : openingsQuery.isSuccess && openings.length === 0 ? (
                <p id="rediscovery-opening-help" className="mt-2 text-[12px] text-muted-foreground">
                  No openings are open right now, so a search cannot target one.
                </p>
              ) : openingsQuery.isSuccess && openings.length >= OPENINGS_PAGE ? (
                <p id="rediscovery-opening-help" className="mt-2 text-[12px] text-muted-foreground">
                  Showing the first {OPENINGS_PAGE} open openings.
                </p>
              ) : null}
            </GlassCard>
          </Reveal>

          {searchMut.data && !searchMut.data.semantic ? (
            <div
              role="status"
              className="flex items-center gap-2 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn-wash)] px-3 py-2 text-[12.5px] text-[var(--ui-warn)]"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {KEYWORD_ONLY_TEXT}
            </div>
          ) : null}

          {selectedRows.length > 0 ? (
            <div className="flex flex-wrap items-center gap-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2">
              <span className="text-[12.5px] text-foreground">{selectedRows.length} selected</span>
              <Button type="button" size="sm" onClick={() => setDialogRows(selectedRows)}>
                Add {selectedRows.length} to pool
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Set())}
              >
                Clear selection
              </Button>
            </div>
          ) : null}

          {searchMut.data ? (
            <Reveal delay={0.08}>
              <GlassCard className="p-5 space-y-4">
                <p className="text-[12.5px] text-muted-foreground">
                  {searchMut.data.returned} of {searchMut.data.matched} matches shown.
                </p>
                {explained.length === 0 && unexplained.length === 0 ? (
                  <p className="py-6 text-center text-[13px] text-muted-foreground">
                    No matches for that search.
                  </p>
                ) : (
                  <>
                    <ul className="flex flex-col gap-3" data-testid="explained-results">
                      {explained.map((r) => (
                        <ResultRow
                          key={r.applicant_id}
                          row={r}
                          expanded={expandedId === r.applicant_id}
                          onToggle={() =>
                            setExpandedId(expandedId === r.applicant_id ? null : r.applicant_id)
                          }
                          selected={selected.has(r.applicant_id)}
                          onSelect={() => toggleSelected(r.applicant_id)}
                          onAddToPool={() => setDialogRows([r])}
                        />
                      ))}
                    </ul>
                    {unexplained.length > 0 ? (
                      <div>
                        <h3 className="mb-2 text-[13px] font-semibold text-foreground">
                          {UNEXPLAINED_HEADING}
                        </h3>
                        <ul className="flex flex-col gap-3" data-testid="unexplained-results">
                          {unexplained.map((r) => (
                            <ResultRow
                              key={r.applicant_id}
                              row={r}
                              expanded={expandedId === r.applicant_id}
                              onToggle={() =>
                                setExpandedId(expandedId === r.applicant_id ? null : r.applicant_id)
                              }
                              selected={selected.has(r.applicant_id)}
                              onSelect={() => toggleSelected(r.applicant_id)}
                              onAddToPool={() => setDialogRows([r])}
                            />
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </>
                )}
              </GlassCard>
            </Reveal>
          ) : null}
        </>
      )}

      {dialogRows ? (
        <AddToPoolDialog
          rows={dialogRows}
          onDone={unselect}
          onClose={() => setDialogRows(null)}
        />
      ) : null}
    </Page>
  );
}

// ---------------------------------------------------------------------------
// One result
// ---------------------------------------------------------------------------

function ResultRow({
  row,
  expanded,
  onToggle,
  selected,
  onSelect,
  onAddToPool,
}: {
  row: RediscoveryResult;
  expanded: boolean;
  onToggle: () => void;
  selected: boolean;
  onSelect: () => void;
  onAddToPool: () => void;
}): JSX.Element {
  const whyId = `rediscovery-why-${row.applicant_id}`;
  return (
    <li className="rounded-[12px] border border-border p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <input
            type="checkbox"
            checked={selected}
            onChange={onSelect}
            aria-label={`Select ${row.full_name}`}
            className="mt-1"
          />
          <div>
            <Link
              to={`/hr/applicants/${row.applicant_id}`}
              className="font-medium text-foreground hover:text-[var(--ui-info)] hover:underline"
            >
              {row.full_name}
            </Link>
            <p className="mt-0.5 text-[12px] text-muted-foreground">
              {[row.current_title, row.current_company].filter(Boolean).join(' · ')}
              {row.years_experience !== undefined ? ` · ${row.years_experience} yrs` : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusTag tone={FRESHNESS_TONE[row.evidence_freshness]} dot>
            {freshnessChipLabel(row.evidence_freshness)}
          </StatusTag>
          {/* A bare "72" said nothing about its scale (audit F24). */}
          <span className="text-[13px] font-semibold text-foreground">
            Match {formatScore('resume', row.score)}
          </span>
        </div>
      </div>

      {row.requires_review ? (
        <p className="mt-2 flex items-center gap-1.5 text-[12px] text-[var(--ui-warn)]">
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          Needs review before acting on it
        </p>
      ) : null}

      {!row.explained && row.unexplained_note ? (
        <p className="mt-2 text-[12px] text-muted-foreground">{row.unexplained_note}</p>
      ) : null}

      <div className="mt-2.5 flex items-center gap-3">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-controls={whyId}
          className="inline-flex items-center gap-1 text-[12px] text-[var(--ui-info)] hover:underline"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3 w-3" aria-hidden="true" />
          )}
          {expanded ? 'Hide why' : 'Why this match?'}
        </button>
        <Button type="button" size="sm" variant="outline" onClick={onAddToPool}>
          Add to pool
        </Button>
      </div>

      {expanded ? (
        <div id={whyId} className="mt-3 border-t border-border pt-3">
          <RediscoveryWhyPanel items={row.why} />
        </div>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Add to pool — one call per applicant, each with its own frozen snapshot
// (see api/pools.ts::matchReasonFromResult and AddMembersInput's own note on
// why a bulk add cannot share one match_reason across different people).
// ---------------------------------------------------------------------------

interface NotAdded {
  applicantId: string;
  name: string;
  reason: string;
  /** True when the request itself failed, so trying again may work. A skip
   *  (already a member, consent not active) would only skip again. */
  retryable: boolean;
}

function AddToPoolDialog({
  rows,
  onDone,
  onClose,
}: {
  rows: RediscoveryResult[];
  onDone: (applicantIds: string[]) => void;
  onClose: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const poolsQuery = useQuery({
    queryKey: ['hr', 'pools', 'list-for-add'],
    queryFn: () => listPools(false),
  });
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [poolId, setPoolId] = useState('');
  const [newName, setNewName] = useState('');
  // The rows still to add. After a partial failure only the retryable ones
  // stay, so "Try again" never re-sends someone already dealt with.
  const [pendingRows, setPendingRows] = useState<RediscoveryResult[]>(rows);
  const [notAdded, setNotAdded] = useState<NotAdded[]>([]);
  // A pool created by an earlier attempt is reused on retry, rather than a
  // second create that would fail as "name already taken".
  const [createdPoolId, setCreatedPoolId] = useState<string | null>(null);

  const pools = poolsQuery.data?.pools ?? [];
  const noPools = poolsQuery.isSuccess && pools.length === 0;
  // With no pool to choose, the only way forward is a new one (audit F23).
  useEffect(() => {
    if (noPools) setMode('new');
  }, [noPools]);

  const mut = useMutation({
    mutationFn: async () => {
      let targetId = mode === 'existing' ? poolId : createdPoolId;
      if (!targetId) {
        const created = await createPool({ name: newName.trim(), description: null });
        targetId = created.id;
        setCreatedPoolId(created.id);
      }
      const poolIdUsed = targetId;
      const outcomes = await Promise.allSettled(
        pendingRows.map((row) =>
          addPoolMembers(poolIdUsed, {
            applicantIds: [row.applicant_id],
            source: 'rediscovery',
            matchReason: matchReasonFromResult(row),
          }),
        ),
      );
      return outcomes;
    },
    onSuccess: (outcomes) => {
      let added = 0;
      const failed: NotAdded[] = [];
      const done: string[] = [];
      outcomes.forEach((outcome, i) => {
        const row = pendingRows[i];
        if (outcome.status === 'fulfilled') {
          added += outcome.value.added.length;
          done.push(row.applicant_id);
          outcome.value.skipped.forEach((s) =>
            failed.push({
              applicantId: row.applicant_id,
              name: row.full_name,
              reason: skipReasonLabel(s.reason),
              retryable: false,
            }),
          );
        } else {
          failed.push({
            applicantId: row.applicant_id,
            name: row.full_name,
            reason: poolErrorMessage(outcome.reason, 'Could not be added.'),
            retryable: true,
          });
        }
      });
      if (added > 0) {
        toast.success(`Added ${plural(added, 'candidate')} to the pool`);
        void invalidateAfterHrMutation(qc);
      }
      onDone(done);
      if (failed.length === 0) {
        onClose();
        return;
      }
      // Named per person and kept on screen, as the pools page does, rather
      // than one "N could not be added" toast (audit PL-F11).
      setNotAdded(failed);
      const retry = new Set(failed.filter((f) => f.retryable).map((f) => f.applicantId));
      setPendingRows(pendingRows.filter((r) => retry.has(r.applicant_id)));
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not add to a pool.')),
  });

  const hasTarget =
    mode === 'existing' ? Boolean(poolId) : Boolean(createdPoolId) || newName.trim().length > 0;
  const canSubmit = hasTarget && pendingRows.length > 0;
  const finished = notAdded.length > 0 && pendingRows.length === 0;

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-[420px] space-y-3">
        <DialogHeader>
          <DialogTitle className="text-[17px] font-bold">
            Add {plural(rows.length, 'candidate')} to a pool
          </DialogTitle>
        </DialogHeader>
        {notAdded.length === 0 ? (
          <>
            <fieldset className="flex items-center gap-4 text-[12.5px] text-foreground">
              <legend className="sr-only">Add to</legend>
              <label className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="pool-mode"
                  value="existing"
                  checked={mode === 'existing'}
                  disabled={noPools}
                  onChange={() => setMode('existing')}
                />
                Existing pool
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="pool-mode"
                  value="new"
                  checked={mode === 'new'}
                  onChange={() => setMode('new')}
                />
                New pool
              </label>
            </fieldset>
            {mode === 'existing' ? (
              poolsQuery.isLoading ? (
                <p className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  Loading your pools…
                </p>
              ) : poolsQuery.isError ? (
                <div className="flex flex-wrap items-center gap-2" role="alert">
                  <p className="text-[12.5px] text-[var(--ui-danger)]">
                    {errorMessage(poolsQuery.error, 'Could not load your pools.')}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void poolsQuery.refetch()}
                  >
                    Try again
                  </Button>
                </div>
              ) : (
                <select
                  value={poolId}
                  onChange={(e) => setPoolId(e.target.value)}
                  aria-label="Pool"
                  className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
                >
                  <option value="">Choose a pool…</option>
                  {pools.map((p: PoolOut) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              )
            ) : (
              <>
                {noPools ? (
                  <p className="text-[12px] text-muted-foreground">
                    You have no pools yet. Name a new one.
                  </p>
                ) : null}
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Pool name"
                  aria-label="New pool name"
                  maxLength={POOL_NAME_MAX}
                  className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
                />
              </>
            )}
          </>
        ) : (
          <div
            role="status"
            className="space-y-1 rounded-[10px] border border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)] p-2.5"
          >
            <p className="text-[12px] font-medium text-[var(--ui-danger)]">Not added</p>
            <ul className="space-y-0.5 text-[12px] text-[var(--ui-danger)]">
              {notAdded.map((n) => (
                <li key={n.applicantId}>
                  {n.name}: {n.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
        <DialogFooter className="flex-row items-center justify-end gap-2 sm:space-x-0">
          <Button type="button" variant="ghost" onClick={onClose}>
            {notAdded.length > 0 ? 'Close' : 'Cancel'}
          </Button>
          {finished ? null : (
            <Button
              type="button"
              onClick={() => mut.mutate()}
              disabled={!canSubmit || mut.isPending}
            >
              {mut.isPending ? 'Adding…' : notAdded.length > 0 ? 'Try again' : 'Add'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
