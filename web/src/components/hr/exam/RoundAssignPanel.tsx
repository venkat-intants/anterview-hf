// RoundAssignPanel: assign or schedule one round for applicants, and list and
// revoke its links. Moved out of pages/hr/ExamEditor.tsx.
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useMemo, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Send, Ban, Loader2, Clock, AlertTriangle, Search } from '@/design/components/icons';
import { listAssignments, assignExam, revokeAssignment, type AssignResult } from '@/api/exams';
import { getPipeline, type PipelineRow } from '@/api/pipeline';
import { applicationKey } from '@/lib/applicationKey';
import { errorMessage } from '@/lib/errorMessage';
import { formatDateTime, plural } from '@/lib/formatters';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { statusLabel } from '@/lib/statusLabel';
import { toast } from '@/lib/toast';
import { StatusTag } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

// ── Round assign panel ────────────────────────────────────────────────────────

const ASSIGN_PAGE = 200;

interface RoundAssignPanelProps {
  examId: string;
  roundId: string;
  isPublished: boolean;
  /** Legacy links with no round belong to the first round. */
  isFirstRound: boolean;
}

export function RoundAssignPanel({
  examId,
  roundId,
  isPublished,
  isFirstRound,
}: RoundAssignPanelProps) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scheduledAt, setScheduledAt] = useState('');
  const [minted, setMinted] = useState<AssignResult[]>([]);
  const [search, setSearch] = useState('');

  // One entry per APPLICATION (B5), so the exam is recorded against the opening
  // it is for: a person who applied to two openings is two entries, each
  // labelled with its opening. Someone filed under no opening is one entry.
  // Paged (audit E13): one fixed page of 200 silently hid everyone after it.
  const pipelineQuery = useInfiniteQuery({
    queryKey: ['hr', 'pipeline', 'assignable', 'pages'],
    queryFn: ({ pageParam }) => getPipeline({ limit: ASSIGN_PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.items.length;
      return last.items.length > 0 && next < last.count ? next : undefined;
    },
    enabled: isPublished,
  });
  const loadedRows = useMemo(
    () => (pipelineQuery.data?.pages ?? []).flatMap((p) => p.items),
    [pipelineQuery.data],
  );
  const total = pipelineQuery.data?.pages[0]?.count ?? 0;
  const applicants: PipelineRow[] = loadedRows.filter(
    (r) => r.status !== 'hired' && r.status !== 'rejected',
  );
  const needle = search.trim().toLowerCase();
  const shown = needle
    ? applicants.filter(
        (r) =>
          r.full_name.toLowerCase().includes(needle) ||
          (r.opening_title ?? '').toLowerCase().includes(needle),
      )
    : applicants;
  const chosen = applicants.filter((r) => selected.has(applicationKey(r)));

  const assignmentsQuery = useQuery({
    queryKey: ['hr', 'exam', examId, 'assignments'],
    queryFn: () => listAssignments(examId),
  });

  const roundAssignments = (assignmentsQuery.data ?? []).filter(
    (a) => a.round_id === roundId || (a.round_id == null && isFirstRound),
  );
  // People who already hold a live link to THIS round. Assigning them again
  // revokes that link (the server keeps one live link per person and round),
  // so the email they already have stops working (audit E12).
  const liveLink = new Set(
    roundAssignments.filter((a) => a.status === 'invited').map((a) => a.applicant_id),
  );
  const rotating = chosen.filter((r) => liveLink.has(r.applicant_id));

  const assignMut = useMutation({
    mutationFn: () =>
      assignExam(
        examId,
        // People filed under no opening go by person; everyone else by the
        // application chosen.
        chosen.filter((r) => !r.enrolment_id).map((r) => r.applicant_id),
        undefined,
        roundId,
        scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
        chosen.flatMap((r) => (r.enrolment_id ? [r.enrolment_id] : [])),
      ),
    onSuccess: (res) => {
      setMinted(res);
      setSelected(new Set());
      setScheduledAt('');
      toast.success(`${plural(res.length, 'link')} created and emailed`);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not assign the exam.')),
  });

  const revokeMut = useMutation({
    mutationFn: (aid: string) => revokeAssignment(examId, aid),
    onSuccess: () => {
      toast.success('Link revoked');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not revoke the exam link.')),
  });

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Link copied');
    } catch {
      toast.error('Could not copy the link. Select it and copy it by hand.');
    }
  }

  function toggleApplicant(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  if (!isPublished) {
    return (
      <p className="mt-3 text-[12.5px] text-muted-foreground">
        Publish this round first, then assign it to applicants.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-3">
      {/* Applicant search */}
      <div className="relative">
        <Search
          size={15}
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search applicants"
          aria-label="Search applicants"
          className="pl-9"
        />
      </div>

      {/* Applicant list */}
      {pipelineQuery.isError && !pipelineQuery.data ? (
        <QueryErrorPanel
          title="Could not load the applicants."
          error={pipelineQuery.error}
          onRetry={() => void pipelineQuery.refetch()}
          retrying={pipelineQuery.isFetching}
        />
      ) : (
        <div
          className="max-h-48 space-y-0.5 overflow-y-auto rounded-[14px] border border-border bg-[var(--ui-inset-soft)] p-2"
          role="group"
          aria-label="Select applicants to assign"
        >
          {pipelineQuery.isLoading ? (
            <Skeleton className="h-8 rounded-[9px]" />
          ) : applicants.length === 0 ? (
            <p className="px-2 py-2 text-[12px] text-muted-foreground">
              No applicants yet. Add them under Applicants.
            </p>
          ) : shown.length === 0 ? (
            <p className="px-2 py-2 text-[12px] text-muted-foreground">
              No loaded applicant matches that search.
            </p>
          ) : (
            shown.map((a) => (
              <label
                key={applicationKey(a)}
                className="flex min-h-10 cursor-pointer items-center gap-2 rounded-[9px] px-2 py-1.5 text-[12.5px] text-foreground hover:bg-[var(--ui-inset)] transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selected.has(applicationKey(a))}
                  onChange={(e) => toggleApplicant(applicationKey(a), e.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1 truncate">
                  {a.full_name}
                  {a.opening_title ? (
                    <span className="text-[var(--ui-faint)]"> · {a.opening_title}</span>
                  ) : null}
                </span>
                {liveLink.has(a.applicant_id) ? (
                  <StatusTag tone="neutral" className="text-[12px]">
                    Link sent
                  </StatusTag>
                ) : null}
              </label>
            ))
          )}
        </div>
      )}
      {pipelineQuery.data ? (
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          <span role="status">
            {loadedRows.length < total
              ? `Showing ${loadedRows.length} of ${total} applications. Search covers the loaded ones.`
              : `${plural(total, 'application')} loaded.`}
          </span>
          {pipelineQuery.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pipelineQuery.isFetchingNextPage}
              aria-busy={pipelineQuery.isFetchingNextPage}
              onClick={() => void pipelineQuery.fetchNextPage()}
            >
              {pipelineQuery.isFetchingNextPage ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : null}
              Load more
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* Schedule (optional) */}
      <FormField label="Schedule (optional, your local time)">
        {(p) => (
          <Input
            {...p}
            type="datetime-local"
            value={scheduledAt}
            onChange={(e) => setScheduledAt(e.target.value)}
            aria-label="Scheduled time for this round assignment"
          />
        )}
      </FormField>

      {rotating.length > 0 ? (
        <p
          role="status"
          className="flex items-start gap-1.5 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn-wash)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)]"
        >
          <AlertTriangle
            size={14}
            className="mt-0.5 shrink-0 text-[var(--ui-warn)]"
            aria-hidden="true"
          />
          {rotating.length === 1
            ? `${rotating[0].full_name} already has a live link to this round. A new link replaces it, and the link in the email they already have stops working.`
            : `${rotating.length} of the people chosen already have a live link to this round. New links replace them, and the links in the emails they already have stop working.`}
        </p>
      ) : null}

      <Button
        type="button"
        disabled={assignMut.isPending || selected.size === 0}
        aria-busy={assignMut.isPending}
        onClick={() => assignMut.mutate()}
      >
        {assignMut.isPending ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <Send aria-hidden="true" />
        )}
        Send links ({selected.size})
      </Button>

      {/* Once-shown magic links */}
      {minted.length > 0 && (
        <div
          role="region"
          aria-label="Generated magic links"
          className="space-y-1.5 rounded-[14px] border border-[var(--ui-ok)] bg-[var(--ui-ok-wash)] p-3"
        >
          <p className="text-[12px] font-semibold text-[var(--ui-ok)]">
            Emailed to each candidate. The links are shown here once, in case you also want to share
            one yourself.
          </p>
          {minted.map((m) => (
            <div key={m.assignment_id} className="flex items-center gap-2 text-[12px]">
              <span className="w-28 shrink-0 truncate font-medium text-foreground">
                {m.applicant_name}
              </span>
              <Input
                readOnly
                size="sm"
                value={m.magic_link}
                className="min-w-0 flex-1 text-muted-foreground"
                aria-label={`Magic link for ${m.applicant_name}`}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Copy link for ${m.applicant_name}`}
                className="shrink-0 text-[var(--ui-ok)]"
                onClick={() => void copyLink(m.magic_link)}
              >
                <Copy aria-hidden="true" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Existing assignments for this round */}
      {assignmentsQuery.isError && !assignmentsQuery.data ? (
        <QueryErrorPanel
          title="Could not load the links already sent for this round."
          error={assignmentsQuery.error}
          onRetry={() => void assignmentsQuery.refetch()}
          retrying={assignmentsQuery.isFetching}
        />
      ) : null}
      {roundAssignments.length > 0 && (
        <div className="space-y-1">
          <p className="text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
            Assigned
          </p>
          {roundAssignments.map((a) => (
            <div
              key={a.assignment_id}
              className="flex flex-wrap items-center gap-2 rounded-[11px] border border-border bg-[var(--ui-inset-soft)] px-3 py-1.5 text-[12.5px] text-foreground"
            >
              <span className="min-w-0 flex-1 truncate">{a.applicant_name}</span>
              {a.scheduled_at && (
                <span className="flex items-center gap-1 text-[12px] text-[var(--ui-faint)]">
                  <Clock size={10} aria-hidden="true" />
                  {formatDateTime(a.scheduled_at)}
                </span>
              )}
              <StatusTag tone="neutral" className="text-[12px]">
                {statusLabel(a.status, 'invite')}
              </StatusTag>
              {a.status === 'invited' && (
                <ConfirmDeleteButton
                  title={`Revoke link for ${a.applicant_name}`}
                  confirmText="Revoke link"
                  icon={Ban}
                  pending={revokeMut.isPending && revokeMut.variables === a.assignment_id}
                  onConfirm={() => revokeMut.mutate(a.assignment_id)}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
