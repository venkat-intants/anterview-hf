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

import { useState, type FormEvent } from 'react';
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
  type PoolOut,
} from '@/api/pools';
import { listRequisitions } from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
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
const KEYWORD_ONLY_TEXT = 'Semantic search is unavailable — matching keywords only';
const UNEXPLAINED_HEADING = 'Matched on similarity only — nothing here says why';

export default function Rediscovery(): JSX.Element {
  const universeQuery = useQuery({
    queryKey: ['hr', 'rediscovery', 'universe'],
    queryFn: getRediscoveryUniverse,
  });
  const openingsQuery = useQuery({
    queryKey: ['hr', 'requisitions', 'open-for-rediscovery'],
    queryFn: () => listRequisitions({ status: 'open' }),
  });

  const [query, setQuery] = useState('');
  const [requisitionId, setRequisitionId] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dialogRows, setDialogRows] = useState<RediscoveryResult[] | null>(null);

  const searchMut = useMutation({
    mutationFn: () => searchRediscovery({ query: query.trim(), requisitionId: requisitionId || null }),
    onSuccess: () => setSelected(new Set()),
    onError: (e: unknown) => toast.error(rediscoveryErrorMessage(e, 'Could not run that search')),
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

  return (
    <div className="mx-auto max-w-[1080px] px-0 py-2 space-y-6">
      <Reveal>
        <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">Rediscovery</h1>
        <p className="mt-1 text-[14px] text-muted-foreground">
          Search candidates who chose to be kept in mind for future openings.
        </p>
      </Reveal>

      {universeQuery.isLoading ? (
        <p className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Checking how many candidates can be searched…
        </p>
      ) : universeQuery.isError ? (
        <p className="text-[12.5px] text-[var(--ui-danger)]">
          {rediscoveryErrorMessage(universeQuery.error, 'Could not check how many candidates are searchable.')}
        </p>
      ) : universeQuery.data ? (
        <p role="status" className="text-[12.5px] text-muted-foreground">
          Searching {eligible.toLocaleString()} of {total.toLocaleString()} applicants — the rest
          have not opted in.
        </p>
      ) : null}

      {showEmptyUniverse ? (
        <Reveal delay={0.05}>
          <GlassCard className="p-8 text-center">
            <Users className="mx-auto h-8 w-8 text-[var(--ui-faint)]" aria-hidden="true" />
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
                    className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
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
                    className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
                  >
                    <option value="">None</option>
                    {(openingsQuery.data ?? []).map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.title}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="submit"
                  disabled={!query.trim() || searchMut.isPending}
                  className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
                >
                  <Search className="h-3.5 w-3.5" aria-hidden="true" />
                  {searchMut.isPending ? 'Searching…' : 'Search'}
                </button>
              </form>
            </GlassCard>
          </Reveal>

          {searchMut.data && !searchMut.data.semantic ? (
            <div
              role="status"
              className="flex items-center gap-2 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[rgba(255,183,100,0.12)] px-3 py-2 text-[12.5px] text-[var(--ui-warn)]"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {KEYWORD_ONLY_TEXT}
            </div>
          ) : null}

          {selectedRows.length > 0 ? (
            <div className="flex flex-wrap items-center gap-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2">
              <span className="text-[12.5px] text-foreground">{selectedRows.length} selected</span>
              <button
                type="button"
                onClick={() => setDialogRows(selectedRows)}
                className="rounded-[8px] bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground"
              >
                Add {selectedRows.length} to pool
              </button>
              <button
                type="button"
                onClick={() => setSelected(new Set())}
                className="text-[12px] text-muted-foreground"
              >
                Clear selection
              </button>
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

      {dialogRows ? <AddToPoolDialog rows={dialogRows} onClose={() => setDialogRows(null)} /> : null}
    </div>
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
            <p className="mt-0.5 text-[11.5px] text-muted-foreground">
              {[row.current_title, row.current_company].filter(Boolean).join(' · ')}
              {row.years_experience !== undefined ? ` · ${row.years_experience} yrs` : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusTag tone={FRESHNESS_TONE[row.evidence_freshness]} dot>
            {freshnessChipLabel(row.evidence_freshness)}
          </StatusTag>
          <span className="text-[13px] font-semibold text-foreground">{row.score}</span>
        </div>
      </div>

      {row.requires_review ? (
        <p className="mt-2 flex items-center gap-1.5 text-[11.5px] text-[var(--ui-warn)]">
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
          className="inline-flex items-center gap-1 text-[11.5px] text-[var(--ui-info)] hover:underline"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3 w-3" aria-hidden="true" />
          )}
          {expanded ? 'Hide why' : 'Why this match?'}
        </button>
        <button
          type="button"
          onClick={onAddToPool}
          className="rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-foreground"
        >
          Add to pool
        </button>
      </div>

      {expanded ? (
        <div className="mt-3 border-t border-border pt-3">
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

function AddToPoolDialog({
  rows,
  onClose,
}: {
  rows: RediscoveryResult[];
  onClose: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const poolsQuery = useQuery({ queryKey: ['hr', 'pools', 'list-for-add'], queryFn: () => listPools(false) });
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [poolId, setPoolId] = useState('');
  const [newName, setNewName] = useState('');

  const mut = useMutation({
    mutationFn: async () => {
      let targetId = poolId;
      if (mode === 'new') {
        const created = await createPool({ name: newName.trim(), description: null });
        targetId = created.id;
      }
      return Promise.allSettled(
        rows.map((row) =>
          addPoolMembers(targetId, {
            applicantIds: [row.applicant_id],
            source: 'rediscovery',
            matchReason: matchReasonFromResult(row),
          }),
        ),
      );
    },
    onSuccess: (outcomes) => {
      let added = 0;
      let notAdded = 0;
      for (const outcome of outcomes) {
        if (outcome.status === 'fulfilled') {
          added += outcome.value.added.length;
          notAdded += outcome.value.skipped.length;
        } else {
          notAdded += 1;
        }
      }
      if (added > 0) toast.success(`Added ${added} to the pool`);
      if (notAdded > 0) toast.error(`${notAdded} could not be added`);
      void qc.invalidateQueries({ queryKey: ['hr', 'pools'] });
      onClose();
    },
    onError: (e: unknown) => toast.error(poolErrorMessage(e, 'Could not add to a pool')),
  });

  const canSubmit = mode === 'existing' ? Boolean(poolId) : newName.trim().length > 0;

  return (
    <div
      role="dialog"
      aria-label="Add to a talent pool"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <GlassCard className="w-full max-w-[420px] space-y-3 p-5">
        <h3 className="text-[15px] font-semibold text-foreground">
          Add {rows.length} candidate{rows.length === 1 ? '' : 's'} to a pool
        </h3>
        <div className="flex items-center gap-4 text-[12.5px] text-foreground">
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={mode === 'existing'} onChange={() => setMode('existing')} />
            Existing pool
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={mode === 'new'} onChange={() => setMode('new')} />
            New pool
          </label>
        </div>
        {mode === 'existing' ? (
          <select
            value={poolId}
            onChange={(e) => setPoolId(e.target.value)}
            aria-label="Pool"
            className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
          >
            <option value="">Choose a pool…</option>
            {(poolsQuery.data?.pools ?? []).map((p: PoolOut) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        ) : (
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Pool name"
            aria-label="New pool name"
            maxLength={120}
            className="w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
          />
        )}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => mut.mutate()}
            disabled={!canSubmit || mut.isPending}
            className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
          >
            {mut.isPending ? 'Adding…' : 'Add'}
          </button>
          <button type="button" onClick={onClose} className="text-[13px] text-muted-foreground">
            Cancel
          </button>
        </div>
      </GlassCard>
    </div>
  );
}
