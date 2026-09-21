// SubmissionPanel — PH4-D4. The "Submission" tab on InterviewerScorecard for
// a job_simulation/portfolio round: what the candidate sent back.
//
// EXTERNAL LINKS ARE THE CAREFUL PART. A candidate's link is untrusted
// content: the server validates its shape (https, an approved domain) but
// NEVER fetches it (AR-7 — no SSRF surface), so this interstitial is the
// only thing standing between a reviewer and whatever that URL actually
// resolves to. It shows the hostname the BROWSER will resolve — parsed with
// `new URL(...)`, never the candidate's own label for the link — and the
// anchor itself carries `rel="noopener noreferrer nofollow"`. A backslash,
// percent-encoding or control character in the host is exactly the kind of
// thing a candidate-chosen label could paper over, which is why this reads
// `url.hostname` rather than trusting the stored string's appearance.
//
// SCHEME, NOT JUST HOSTNAME (security review, PH4 wave 5): checking only
// that `new URL(href).hostname` is non-empty let `javascript://github.com/x`
// and `vbscript://github.com/x` through with a hostname of `github.com` —
// this interstitial would have labelled either "resolves to github.com" and
// then rendered it as the anchor's own `href`. `http:` passed too. The
// server blocks all of these today, but this interstitial is meant to be
// the LAST safeguard, so it parses the URL once with `downloadUrl()`
// (`lib/safeUrl.ts`) — https-only, plus http when this app is itself served
// insecurely, exactly the rule that helper already enforces — and renders
// `url.href` from that same parse, never the raw candidate string.
//
// WHAT THIS CANNOT SHOW (backend gaps, not UI omissions — see
// `app.job_tasks.submission_for_reviewer`):
//   - An item's PROMPT. The endpoint returns each response's `item_key` and
//     answer, never the round's `items`/`brief`, so a reviewer sees which
//     item a text or link answers only by its key, not by the question HR
//     wrote. This falls back to a prettified key.
//   - Reference MATERIAL downloads. `materials` here carries title/filename
//     metadata only (`_material_out`) — there is no
//     `/interviewer/.../materials/{id}/download` route, only the HR and
//     candidate ones. Titles are listed; there is nothing to click.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  downloadScorecardArtifact,
  getScorecardSubmission,
  type TaskResponseOut,
} from '@/api/jobTasks';
import { downloadUrl } from '@/lib/safeUrl';
import { formatDate } from '@/lib/formatters';
import { toast } from '@/lib/toast';
import { AlertTriangle, Download, ExternalLink, Info, Loader2 } from '@/design/components/icons';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** An item's key, read as a label when there is nothing better — see the
 *  module note: the prompt itself never reaches this endpoint. */
function prettyKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * The interstitial. Shows the hostname exactly as the BROWSER will resolve
 * it (`new URL(href).hostname`), never the candidate's own title for the
 * link — a mismatch between the two is precisely the thing worth a reviewer
 * seeing before they click through.
 */
function ExternalLinkGate({ href }: { href: string }) {
  const [confirming, setConfirming] = useState(false);
  // Parsed ONCE, https-only (see the module note): `safeHref` is what gets
  // rendered as the anchor's `href`, never the raw candidate string, and
  // `hostname` is read from that same parsed URL.
  const safeHref = downloadUrl(href);
  const hostname = safeHref ? new URL(safeHref).hostname : null;

  if (!safeHref || !hostname) {
    return <p className="text-[12px] text-[var(--ui-danger)]">This link could not be opened.</p>;
  }

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="inline-flex items-center gap-1.5 text-[12.5px] text-[var(--ui-info)] hover:underline"
      >
        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        Open link
      </button>
    );
  }

  return (
    <div className="mt-1.5 flex flex-col gap-2 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn)]/[0.06] p-3">
      <p className="flex items-start gap-1.5 text-[12px] leading-relaxed text-[var(--ui-soft)]">
        <AlertTriangle
          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--ui-warn)]"
          aria-hidden="true"
        />
        This is a link the candidate provided — the server never fetches it, only your browser will,
        and only if you continue. It resolves to{' '}
        <strong className="text-foreground">{hostname}</strong>.
      </p>
      <div className="flex items-center gap-2">
        <a
          href={safeHref}
          target="_blank"
          rel="noopener noreferrer nofollow"
          onClick={() => setConfirming(false)}
          className="inline-flex items-center gap-1.5 rounded-[8px] bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          Open {hostname}
        </a>
        <button
          type="button"
          onClick={() => setConfirming(false)}
          className="text-[12px] text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function FileDownload({
  scorecardId,
  response,
}: {
  scorecardId: string;
  response: TaskResponseOut;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function open(): Promise<void> {
    setPending(true);
    setError(null);
    try {
      const res = await downloadScorecardArtifact(scorecardId, response.id);
      const url = downloadUrl(res.url);
      if (!url) {
        setError('That download link could not be opened.');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (e) {
      setError(errText(e, 'Could not open this file'));
      toast.error(errText(e, 'Could not open this file'));
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => void open()}
        disabled={pending}
        className="inline-flex items-center gap-1.5 text-[12.5px] text-[var(--ui-info)] hover:underline disabled:opacity-50"
      >
        {pending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {response.original_name ?? 'Download file'}
      </button>
      {error ? <p className="mt-1 text-[11.5px] text-ember">{error}</p> : null}
    </div>
  );
}

function ResponseRow({
  scorecardId,
  response,
}: {
  scorecardId: string;
  response: TaskResponseOut;
}) {
  return (
    <li className="rounded-[10px] border border-border p-3">
      <p className="text-[12.5px] font-medium text-foreground">
        {response.item_key ? prettyKey(response.item_key) : response.title || 'Portfolio artifact'}
      </p>
      {response.description ? (
        <p className="mt-1 text-[12px] text-[var(--ui-soft)]">{response.description}</p>
      ) : null}
      <div className="mt-1.5">
        {response.response_type === 'text' ? (
          <p className="whitespace-pre-wrap text-[13px] text-[var(--ui-soft)]">
            {response.text_value || (
              <span className="text-[var(--ui-faint)]">No answer given.</span>
            )}
          </p>
        ) : response.response_type === 'link' ? (
          response.link_url ? (
            <ExternalLinkGate href={response.link_url} />
          ) : (
            <span className="text-[12px] text-[var(--ui-faint)]">No link given.</span>
          )
        ) : (
          <FileDownload scorecardId={scorecardId} response={response} />
        )}
      </div>
    </li>
  );
}

export default function SubmissionPanel({ scorecardId }: { scorecardId: string }): JSX.Element {
  const submission = useQuery({
    queryKey: ['interviewer', 'submission', scorecardId],
    queryFn: () => getScorecardSubmission(scorecardId),
    retry: false,
  });

  if (submission.isLoading) {
    return (
      <div className="flex items-center gap-2 py-10 text-[13px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading the submission…
      </div>
    );
  }
  if (submission.isError || !submission.data) {
    return (
      <p className="py-6 text-[13px] text-[var(--ui-danger)]">
        {errText(submission.error, 'Could not load this submission')}
      </p>
    );
  }

  const data = submission.data;

  // The candidate is told submitting is the moment their work goes to the
  // hiring team, and "Remove" exists on their own page precisely so a
  // mistaken upload (the code's own example: an ID scan) can be withdrawn
  // BEFORE that moment. Rendering responses here regardless of status showed
  // a reviewer work the candidate had not yet sent, with only the header
  // line saying otherwise (security review, PH4 wave 5). The backend gates
  // this too (`submission_for_reviewer` now only returns a `submitted` row),
  // so this is belt and braces, not either/or.
  if (data.status !== 'submitted') {
    return (
      <p className="py-6 text-[13px] text-muted-foreground">
        Not yet submitted ({data.status.replace('_', ' ')}) — nothing to show the reviewer until
        the candidate submits.
      </p>
    );
  }

  const items = data.responses.filter((r) => r.item_key);
  const artifacts = data.responses.filter((r) => !r.item_key);

  return (
    <div className="flex flex-col gap-5">
      <p className="text-[12.5px] text-muted-foreground">
        {data.submitted_at ? `Submitted ${formatDate(data.submitted_at)}` : 'Submitted'}
      </p>

      {data.materials.length > 0 ? (
        <section>
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-faint)]">
            Reference materials
          </h3>
          <ul className="mt-1.5 flex flex-col gap-1">
            {data.materials.map((m) => (
              <li key={m.id} className="text-[12.5px] text-[var(--ui-soft)]">
                {m.title}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {items.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-faint)]">
            Items
          </h3>
          <ul className="flex flex-col gap-2">
            {items.map((r) => (
              <ResponseRow key={r.id} scorecardId={scorecardId} response={r} />
            ))}
          </ul>
        </section>
      ) : null}

      {data.kind === 'portfolio' ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-faint)]">
            Portfolio
          </h3>
          {artifacts.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">No artifacts submitted.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {artifacts.map((r) => (
                <ResponseRow key={r.id} scorecardId={scorecardId} response={r} />
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-muted-foreground">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        This is evidence, not a verdict — score it against the checklist in the Scorecard tab.
      </p>
    </div>
  );
}
