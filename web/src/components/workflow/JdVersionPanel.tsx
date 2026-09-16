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
import { GlassCard, Pill } from '@/design/components/primitives';
import { AlertCircle } from '@/design/components/icons';

const INPUT =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13.5px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none';
const LABEL = 'mb-1.5 block text-[12.5px] font-medium text-[#b8babf]';

const STATUS_LABEL: Record<JdVersion['status'], string> = {
  draft: 'Draft',
  published: 'Published',
  archived: 'Previous',
};

/** Deliberately not colour alone: "published" must be readable without it. */
const STATUS_TONE: Record<JdVersion['status'], string> = {
  draft: 'border-[#d6a23d]/40 text-[#d6a23d]',
  published: 'border-[#4f9e6a]/40 text-[#6fbf8d]',
  archived: 'border-white/[0.1] text-[#888b91]',
};

function when(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

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
  const from = when(v.published_at);
  return v.superseded_at ? `Live ${from} – ${when(v.superseded_at)}` : `Live since ${from}`;
}

export default function JdVersionPanel({ requisition }: { requisition: Requisition }) {
  const client = useQueryClient();
  const [draftText, setDraftText] = useState<string | null>(null);
  const [note, setNote] = useState('');

  const history = useQuery({
    queryKey: ['jd-history', requisition.id],
    queryFn: () => getJdHistory(requisition.id),
  });

  const versions = history.data?.versions ?? [];
  const draft = versions.find((v) => v.status === 'draft') ?? null;
  const published = versions.find((v) => v.status === 'published') ?? null;

  // The textarea starts from the draft if there is one, otherwise from the
  // live advert — editing one field must not silently blank the document.
  const text = draftText ?? draft?.jd_text ?? requisition.jd_text ?? '';

  function refresh(): void {
    void client.invalidateQueries({ queryKey: ['jd-history', requisition.id] });
    void client.invalidateQueries({ queryKey: ['requisition', requisition.id] });
  }

  const save = useMutation({
    mutationFn: () =>
      saveJdDraft(requisition.id, { jd_text: text, change_note: note || null }),
    onSuccess: () => {
      setDraftText(null);
      setNote('');
      refresh();
      toast.success('Draft saved. Candidates still see the published version.');
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not save the draft.'),
  });

  const publish = useMutation({
    mutationFn: (versionId: string) => publishJdVersion(requisition.id, versionId),
    onSuccess: (v) => {
      setDraftText(null);
      refresh();
      toast.success(`Version ${v.version} is now the published job description.`);
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not publish that version.'),
  });

  const discard = useMutation({
    mutationFn: () => discardJdDraft(requisition.id),
    onSuccess: () => {
      setDraftText(null);
      refresh();
      toast.success('Draft discarded. The published version is unchanged.');
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not discard the draft.'),
  });

  return (
    <section aria-labelledby="jd-versions-heading" className="mt-5">
      <GlassCard className="p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="jd-versions-heading" className="text-[15px] font-semibold text-white">
            Job description
          </h2>
          {published ? (
            <span className="text-[12px] text-[#888b91]">
              Published: v{published.version}
              {published.created_by_name ? ` · by ${published.created_by_name}` : ''}
            </span>
          ) : (
            <span className="text-[12px] text-[#888b91]">No published version yet</span>
          )}
        </div>

        <p className="mt-1 text-[12.5px] text-[#888b91]">
          Editing here creates a draft. Candidates keep seeing the published version until
          you publish the draft.
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
            What changed <span className="text-[#5a5f66]">(optional)</span>
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
          <Pill onClick={() => save.mutate()} disabled={save.isPending} className="px-4 py-2">
            {save.isPending ? 'Saving…' : 'Save draft'}
          </Pill>
          {draft ? (
            <>
              <Pill
                onClick={() => publish.mutate(draft.id)}
                disabled={publish.isPending}
                className="px-4 py-2"
              >
                {publish.isPending ? 'Publishing…' : `Publish v${draft.version}`}
              </Pill>
              <button
                type="button"
                onClick={() => discard.mutate()}
                disabled={discard.isPending}
                className="rounded-[10px] border border-white/[0.1] px-3 py-2 text-[12.5px] text-[#b8babf] hover:text-white disabled:opacity-40"
              >
                Discard draft
              </button>
            </>
          ) : null}
        </div>

        {draft ? (
          <p className="mt-2.5 flex items-start gap-1.5 text-[11.5px] text-[#d6a23d]">
            <AlertCircle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
            This draft is not visible to candidates. Publish it to make it the live job
            description.
          </p>
        ) : null}

        {/* ── History ───────────────────────────────────────────────── */}
        <div className="mt-6">
          <h3 className="text-[13px] font-semibold text-[#d5d7da]">Version history</h3>
          {history.isLoading ? (
            <p className="mt-2 text-[12.5px] text-[#888b91]">Loading…</p>
          ) : versions.length === 0 ? (
            <p className="mt-2 text-[12.5px] text-[#888b91]">
              No versions recorded yet. The next edit will create version 1.
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-1.5">
              {versions.map((v) => (
                <li
                  key={v.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-white/[0.07] bg-white/[0.02] px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium text-[#d5d7da]">
                        v{v.version}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10.5px] ${STATUS_TONE[v.status]}`}
                      >
                        {STATUS_LABEL[v.status]}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11.5px] text-[#888b91]">
                      {when(v.created_at)}
                      {v.created_by_name ? ` · ${v.created_by_name}` : ''}
                      {liveWindow(v) ? ` · ${liveWindow(v)}` : ''}
                    </p>
                    {v.change_note ? (
                      <p className="mt-0.5 truncate text-[11.5px] text-[#6f7379]">
                        {v.change_note}
                      </p>
                    ) : null}
                  </div>
                  {v.status === 'archived' ? (
                    <button
                      type="button"
                      onClick={() => publish.mutate(v.id)}
                      disabled={publish.isPending}
                      className="shrink-0 rounded-[10px] border border-white/[0.1] px-3 py-1.5 text-[12px] text-[#b8babf] hover:text-white disabled:opacity-40"
                    >
                      Restore
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11.5px] text-[#6f7379]">
            Restoring publishes that wording again as a new live version. Nothing in the
            history is overwritten.
          </p>
        </div>
      </GlassCard>
    </section>
  );
}
