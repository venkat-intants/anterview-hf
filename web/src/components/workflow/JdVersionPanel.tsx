// JdVersionPanel — the JD's history, and the way to revise it privately.
//
// PH3-B6 asks for version-aware authoring "in JD Studio". JD Studio is
// PostingEditor: the panel above this one, where the advert is written. This
// sits underneath it rather than replacing it, which is what the story's own
// Task 7 asks for — "add version information to the existing JD Studio rather
// than creating a completely separate interface".
//
// TWO WAYS TO EDIT, AND THE DIFFERENCE MATTERS
// "Save posting" above publishes immediately. It always has, a dozen screens
// rely on it, and PH3-B3's compatibility criterion requires that it keep
// working — what changed is that the previous wording is now kept instead of
// being destroyed.
// "Save draft" here does not touch what candidates see. The public surfaces
// read the requisition; a draft does not write to it. That is the whole
// mechanism, and it is why a draft cannot leak.
//
// REVERTING IS A PUBLISH, NOT AN EDIT
// Going back to v2 publishes v2 again as the live advert and leaves v3 in the
// history. Rewriting the past would make "historical versions cannot be
// accidentally overwritten" false.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  discardJdDraft,
  getJdHistory,
  publishJdVersion,
  saveJdDraft,
  type JdVersion,
  type Requisition,
} from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation, queryKeys } from '@/lib/queryKeys';
import { GlassCard } from '@/design/components/primitives';
import { AlertCircle, XCircle } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { formatDate } from '@/lib/formatters';

const INPUT =
  'w-full rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[13.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
const LABEL = 'mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]';

const STATUS_LABEL: Record<JdVersion['status'], string> = {
  draft: 'Draft',
  published: 'Published',
  archived: 'Previous',
};

/** Deliberately not colour alone: "published" must be readable without it. */
const STATUS_TONE: Record<JdVersion['status'], string> = {
  draft: 'border-[var(--ui-warn)] text-[var(--ui-warn)]',
  published: 'border-[var(--ui-ok)] text-[var(--ui-ok)]',
  archived: 'border-border text-muted-foreground',
};

/**
 * The window in which this wording was the one candidates actually saw.
 *
 * This is the question PH3-B6 Task 5 asks — "which JD version was
 * used/published at this point in time?" — and answering it from published_at
 * and superseded_at is why those two columns exist rather than the order being
 * inferred from version numbers.
 */
function liveWindow(v: JdVersion): string | null {
  if (!v.published_at) return null;
  const from = formatDate(v.published_at);
  return v.superseded_at ? `Live ${from} - ${formatDate(v.superseded_at)}` : `Live since ${from}`;
}

export default function JdVersionPanel({ requisition }: { requisition: Requisition }) {
  const client = useQueryClient();
  const [draftText, setDraftText] = useState<string | null>(null);
  const [note, setNote] = useState('');

  const history = useQuery({
    queryKey: queryKeys.jdHistory(requisition.id),
    queryFn: () => getJdHistory(requisition.id),
  });

  const versions = history.data?.versions ?? [];
  const draft = versions.find((v) => v.status === 'draft') ?? null;
  const published = versions.find((v) => v.status === 'published') ?? null;
  const newestVersion = versions.reduce<JdVersion | null>(
    (n, v) => (n === null || v.version > n.version ? v : n),
    null,
  );
  const historyKey = useScopedPersistKey('jd-version-history');

  // The textarea starts from the draft if there is one, otherwise from the
  // live advert — editing one field must not silently blank the document.
  const text = draftText ?? draft?.jd_text ?? requisition.jd_text ?? '';
  // HR-H3: typed but not saved. Publishing used to send the draft as last
  // saved and then throw the typed text away; now Publish saves it first.
  const unsaved = draftText !== null && draftText !== (draft?.jd_text ?? requisition.jd_text ?? '');

  function refresh(): void {
    void invalidateAfterHrMutation(client, queryKeys.jdHistory(requisition.id));
  }

  const save = useMutation({
    mutationFn: () => saveJdDraft(requisition.id, { jd_text: text, change_note: note || null }),
    onSuccess: () => {
      setDraftText(null);
      setNote('');
      refresh();
      toast.success('Draft saved. Candidates still see the published version.');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the draft.')),
  });

  // Publishing the draft publishes what is on screen: unsaved edits are saved
  // as the draft first, and the version that save returns is the one
  // published. If the save fails nothing is published and the text stays.
  const publishDraft = useMutation({
    mutationFn: async (draftId: string) => {
      const id = unsaved
        ? (await saveJdDraft(requisition.id, { jd_text: text, change_note: note || null })).id
        : draftId;
      return publishJdVersion(requisition.id, id);
    },
    onSuccess: (v) => {
      setDraftText(null);
      setNote('');
      refresh();
      toast.success(`Version ${v.version} is now the published job description.`);
    },
    onError: (e: unknown) => {
      refresh();
      toast.error(errorMessage(e, 'Could not publish the draft.'));
    },
  });

  // Restoring an earlier version leaves the draft and anything typed alone.
  const restore = useMutation({
    mutationFn: (versionId: string) => publishJdVersion(requisition.id, versionId),
    onSuccess: (v) => {
      refresh();
      toast.success(`Version ${v.version} is now the published job description.`);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not restore that version.')),
  });

  const discard = useMutation({
    mutationFn: () => discardJdDraft(requisition.id),
    onSuccess: () => {
      setDraftText(null);
      refresh();
      toast.success('Draft discarded. The published version is unchanged.');
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not discard the draft.')),
  });

  return (
    <section aria-labelledby="jd-versions-heading">
      <GlassCard className="p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="jd-versions-heading" className="text-[15px] font-semibold text-foreground">
            Job description
          </h2>
          {published ? (
            <span className="text-[12px] text-muted-foreground">
              Published: v{published.version}
              {published.created_by_name ? ` · by ${published.created_by_name}` : ''}
            </span>
          ) : (
            <span className="text-[12px] text-muted-foreground">No published version yet</span>
          )}
        </div>

        <p className="mt-1 text-[12.5px] text-muted-foreground">
          Editing here creates a draft. Candidates keep seeing the published version until you
          publish the draft.
        </p>

        <div className="mt-4">
          <label htmlFor="jd-draft-text" className={LABEL}>
            {draft ? `Draft (v${draft.version})` : 'New draft'}
          </label>
          <textarea
            id="jd-draft-text"
            rows={10}
            value={text}
            onChange={(e) => setDraftText(e.target.value)}
            placeholder="What this role does, who it reports to, what success looks like…"
            className={INPUT}
          />
        </div>

        <div className="mt-3">
          <label htmlFor="jd-change-note" className={LABEL}>
            What changed <span className="text-[var(--ui-faint)]">(optional)</span>
          </label>
          <input
            id="jd-change-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Added the on-call expectation"
            maxLength={500}
            className={INPUT}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2.5">
          {/* One primary action: Publish when there is a draft, Save otherwise. */}
          <Button
            type="button"
            variant={draft ? 'outline' : 'default'}
            onClick={() => save.mutate()}
            disabled={save.isPending || publishDraft.isPending}
          >
            {save.isPending ? 'Saving…' : 'Save draft'}
          </Button>
          {draft ? (
            <>
              <Button
                type="button"
                onClick={() => publishDraft.mutate(draft.id)}
                disabled={publishDraft.isPending || save.isPending || discard.isPending}
                aria-busy={publishDraft.isPending}
              >
                {publishDraft.isPending
                  ? 'Publishing…'
                  : unsaved
                    ? `Save and publish v${draft.version}`
                    : `Publish v${draft.version}`}
              </Button>
              {/* HR-M2: a draft cannot be brought back once discarded. */}
              <ConfirmDeleteButton
                label="Discard draft"
                confirmText="Discard draft"
                icon={XCircle}
                onConfirm={() => discard.mutate()}
                pending={discard.isPending}
              />
            </>
          ) : null}
        </div>
        {draft && unsaved ? (
          <p className="mt-2 text-[12px] text-muted-foreground">
            You have unsaved edits. Publishing saves them as v{draft.version} first.
          </p>
        ) : null}

        {draft ? (
          <p className="mt-2.5 flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]">
            <AlertCircle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
            This draft is not visible to candidates. Publish it to make it the live job description.
          </p>
        ) : null}

        {/* ── History ───────────────────────────────────────────────── */}
        <div className="mt-6">
          <Collapsible
            title="Version history"
            count={history.isLoading ? undefined : versions.length}
            noun="version"
            latest={
              newestVersion
                ? `v${newestVersion.version} ${STATUS_LABEL[newestVersion.status].toLowerCase()}, ${formatDate(newestVersion.created_at)}`
                : undefined
            }
            persistKey={historyKey}
            data-testid="jd-version-history"
          >
            {history.isLoading ? (
              <p className="text-[12.5px] text-muted-foreground">Loading…</p>
            ) : versions.length === 0 ? (
              <p className="text-[12.5px] text-muted-foreground">
                No versions recorded yet. The next edit will create version 1.
              </p>
            ) : (
              <ProgressiveList
                items={versions}
                initial={5}
                noun="versions"
                getKey={(v) => v.id}
                className="flex flex-col gap-1.5"
                renderItem={(v) => (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-medium text-foreground">v{v.version}</span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[12px] ${STATUS_TONE[v.status]}`}
                        >
                          {STATUS_LABEL[v.status]}
                        </span>
                      </div>
                      <p className="mt-0.5 text-[12px] text-muted-foreground">
                        {formatDate(v.created_at)}
                        {v.created_by_name ? ` · ${v.created_by_name}` : ''}
                        {liveWindow(v) ? ` · ${liveWindow(v)}` : ''}
                      </p>
                      {v.change_note ? (
                        <p className="mt-0.5 truncate text-[12px] text-[var(--ui-soft)]">
                          {v.change_note}
                        </p>
                      ) : null}
                    </div>
                    {v.status === 'archived' ? (
                      <button
                        type="button"
                        onClick={() => restore.mutate(v.id)}
                        disabled={restore.isPending}
                        className="inline-flex min-h-10 shrink-0 items-center rounded-[10px] border border-border px-3 text-[12px] text-[var(--ui-soft)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40"
                      >
                        Restore
                      </button>
                    ) : null}
                  </li>
                )}
              />
            )}
            <p className="mt-2 text-[12px] text-[var(--ui-soft)]">
              Restoring publishes that wording again as a new live version. Nothing in the history
              is overwritten.
            </p>
          </Collapsible>
        </div>
      </GlassCard>
    </section>
  );
}
