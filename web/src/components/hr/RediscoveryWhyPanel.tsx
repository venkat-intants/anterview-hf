// RediscoveryWhyPanel — PH5-E3. Renders one `why[]` array, shared by the live
// Rediscovery search results and a pool member's FROZEN `match_reason`
// snapshot (design §6.3-6.4). The same component serves both because the
// item shape already degrades correctly: a frozen item simply has no
// `snippet`/`note`/`citation.href`, and every field below is read with a
// presence check rather than assumed.
//
// THE ONE RULE THIS FILE EXISTS TO ENFORCE: `resume_terms.snippet` carries
// `[[…]]` bracket markers, not HTML. `parseBracketHighlights` turns that into
// plain React nodes — never `dangerouslySetInnerHTML` on candidate CV text.
//
// A citation with no `href` (every frozen one, by design) renders as plain
// text, not a link — see `RediscoveryCitation`'s own note in api/rediscovery.ts.

import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { StatusTag } from '@/design/components/primitives';
import { AlertTriangle } from '@/design/components/icons';
import { parseBracketHighlights, type RediscoveryWhyItem } from '@/api/rediscovery';
import { FRESHNESS_TONE, freshnessChipLabel, signalLabel } from './rediscoveryDisplay';

const SIMILARITY_LINE =
  'Their CV reads as similar to what you searched for. That is a similarity score, not a stated fact — open the CV to check.';

function Snippet({ text }: { text: string }) {
  const segments = parseBracketHighlights(text);
  return (
    <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
      {segments.map((seg, i) =>
        seg.highlighted ? (
          <mark
            key={i}
            className="rounded-[3px] bg-[rgba(var(--accent-rgb),0.22)] px-0.5 text-foreground"
          >
            {seg.text}
          </mark>
        ) : (
          <Fragment key={i}>{seg.text}</Fragment>
        ),
      )}
    </p>
  );
}

function CitationRef({ item }: { item: RediscoveryWhyItem }) {
  const c = item.citation;
  if (!c) return null;
  if (c.href) {
    return (
      <Link
        to={c.href}
        className="text-[11.5px] text-[var(--ui-info)] underline underline-offset-2 hover:no-underline"
      >
        {c.label}
      </Link>
    );
  }
  // A frozen snapshot's citation: named, never linked (design §6.4 — it must
  // not become a second copy of candidate prose).
  return <span className="text-[11.5px] text-muted-foreground">{c.label}</span>;
}

function WhyItemDetail({ item }: { item: RediscoveryWhyItem }) {
  switch (item.signal) {
    case 'resume_similarity':
      return <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{SIMILARITY_LINE}</p>;
    case 'resume_terms':
      return (
        <>
          {item.terms_matched && item.terms_matched.length > 0 ? (
            <p className="mt-1 flex flex-wrap gap-1">
              {item.terms_matched.map((term) => (
                <span
                  key={term}
                  className="rounded-[6px] bg-[var(--ui-inset)] px-1.5 py-0.5 text-[11px] text-foreground"
                >
                  {term}
                </span>
              ))}
            </p>
          ) : null}
          {item.snippet ? <Snippet text={item.snippet} /> : null}
        </>
      );
    case 'interviewer_scorecard':
      return (
        <p className="mt-1 text-[12px] text-muted-foreground">
          {item.competency ?? item.competency_id ?? 'Competency'}
          {item.score !== undefined && item.of !== undefined ? ` · ${item.score}/${item.of}` : ''}
          {item.round_title ? ` · ${item.round_title}` : ''}
        </p>
      );
    case 'round_result':
      return (
        <p className="mt-1 text-[12px] text-muted-foreground">
          {item.round_kind ? `${item.round_kind} · ` : ''}
          {item.passed !== undefined ? (item.passed ? 'Passed' : 'Not passed') : ''}
          {item.percent !== undefined ? ` · ${Math.round(item.percent)}%` : ''}
        </p>
      );
    case 'exam_attempt':
      return (
        <p className="mt-1 text-[12px] text-muted-foreground">
          {item.passed !== undefined ? (item.passed ? 'Passed' : 'Not passed') : ''}
          {item.percent !== undefined ? ` · ${Math.round(item.percent)}%` : ''}
        </p>
      );
    case 'ai_interview':
      return (
        <p className="mt-1 flex items-start gap-1.5 text-[12px] text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          {item.content_hidden_reason ?? 'This AI interview is no longer available.'}
        </p>
      );
    default:
      return null;
  }
}

export function RediscoveryWhyPanel({ items }: { items: RediscoveryWhyItem[] }): JSX.Element {
  return (
    <ul className="flex flex-col gap-2.5" data-testid="rediscovery-why-panel">
      {items.map((item, i) => (
        <li key={`${item.signal}-${i}`} className="rounded-[10px] border border-border p-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[12.5px] font-medium text-foreground">
              {signalLabel(item.signal)}
              {item.locator ? (
                <span className="ml-1.5 font-normal text-muted-foreground">· {item.locator}</span>
              ) : null}
            </span>
            <span className="flex items-center gap-1.5">
              {item.freshness || item.freshness_reason ? (
                <StatusTag tone={item.freshness ? FRESHNESS_TONE[item.freshness] : 'neutral'} dot>
                  {freshnessChipLabel(item.freshness, item.freshness_reason)}
                </StatusTag>
              ) : null}
              <span className="text-[11px] text-muted-foreground">+{item.contribution}</span>
            </span>
          </div>
          <WhyItemDetail item={item} />
          {!item.explainable && item.signal !== 'resume_similarity' ? (
            <p className="mt-1 text-[11px] text-muted-foreground">Shown as context, not a reason.</p>
          ) : null}
          {item.citation ? <div className="mt-1.5">{<CitationRef item={item} />}</div> : null}
        </li>
      ))}
    </ul>
  );
}

export default RediscoveryWhyPanel;
