// CitationChips — the one shared renderer for every citation strip and inline
// marker in the product.
//
// Every console that shows an agent's answer must render its sources the same
// way: a react-router Link when there is a record to open, a non-interactive
// chip when there is not (an aggregate or a computed role model has nothing to
// open), and always a way to tell an applicant chip from a document chip
// without hovering. Before PH5-E1 five surfaces each drew this by hand — two
// with a full-reloading `<a href>`, one as a dead `#` link when href was null,
// one as an inert `<span>` that silently dropped a real href, and two that
// rendered no citations at all. This file is now the only place any of them
// do it, and the only place that needs to change if the rule does.
//
// `CitationLike` is deliberately looser than `Citation` (api/agent.ts) so the
// same component also renders `AttentionCitation` (api/attention.ts), which
// predates `ref`/`locator` and types `kind` as a plain `string`. A real
// `Citation`'s extra fields are simply unused where this component has no use
// for them (e.g. `ref` when there is no reply text to link an inline marker
// into).

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart3,
  Briefcase,
  Check,
  ClipboardCheck,
  ClipboardList,
  FileCheck2,
  FileText,
  ShieldCheck,
  Target,
  User,
  Video,
  type LucideIcon,
} from '@/design/components/icons';
import { cn } from '@/lib/utils';

/** The shape every console's citation type already satisfies. */
export interface CitationLike {
  kind: string;
  id: string;
  label: string;
  href: string | null;
  /**
   * Where inside the source the evidence sits — "page 4", "v3 · §2.1". Shown
   * as a tooltip; for a non-interactive chip (href === null) it is the ONLY
   * extra information the chip can offer, since there is nowhere to click
   * through to read more.
   */
  locator?: string | null;
  /**
   * Run-scoped marker ("S1", "S2", …). Only `Citation` (api/agent.ts) carries
   * this — `AttentionCitation` has no inline markers to match, since a
   * watcher finding is not model prose with `[S1]`s written into it.
   */
  ref?: string;
}

/** Per-kind icon + short word, so a kind is legible without a hover. */
const KIND_META: Record<string, { icon: LucideIcon; word: string }> = {
  applicant: { icon: User, word: 'Applicant' },
  scorecard: { icon: ClipboardCheck, word: 'Scorecard' },
  exam: { icon: ClipboardList, word: 'Exam' },
  exam_attempt: { icon: FileCheck2, word: 'Attempt' },
  interview: { icon: Video, word: 'Interview' },
  job: { icon: Briefcase, word: 'Opening' },
  analytics: { icon: BarChart3, word: 'Analytics' },
  audit: { icon: ShieldCheck, word: 'Audit' },
  role_profile: { icon: Target, word: 'Role model' },
  interviewer_scorecard: { icon: ClipboardCheck, word: 'Interviewer scorecard' },
  decision: { icon: Check, word: 'Decision' },
  document: { icon: FileText, word: 'Document' },
};

/** A citation kind this build has never heard of still renders as *something*
 * rather than throwing — a closed-vocabulary mismatch belongs to the parity
 * test, not to a runtime crash in front of an HR manager. */
const DEFAULT_KIND_META = { icon: FileText, word: 'Source' };

function metaFor(kind: string): { icon: LucideIcon; word: string } {
  return KIND_META[kind] ?? DEFAULT_KIND_META;
}

interface CitationChipsProps {
  citations: CitationLike[];
  /**
   * Chips beyond this many collapse into "+N more" (default 4, matching the
   * watcher panel this replaces). Ignored in `inline` mode, which always
   * renders every marker it is handed — in practice exactly one, since each
   * `[S1]` in the reply is resolved to its own single citation.
   */
  cap?: number;
  /**
   * `strip` — the labelled source list under an answer.
   * `inline` — the small marker rendered in place of a `[S1]` in the reply
   * text itself, via `CitedText` below.
   */
  variant?: 'inline' | 'strip';
  className?: string;
}

export default function CitationChips({
  citations,
  cap = 4,
  variant = 'strip',
  className,
}: CitationChipsProps): JSX.Element | null {
  if (citations.length === 0) return null;

  const shown = variant === 'inline' ? citations : citations.slice(0, cap);
  const hidden = citations.length - shown.length;

  if (variant === 'inline') {
    // No wrapping block element: this renders inside a paragraph's running
    // text, and a `<div>` here would break that flow.
    return (
      <>
        {shown.map((c) => {
          const key = `${c.kind}:${c.id}`;
          const text = c.ref ? `[${c.ref}]` : metaFor(c.kind).word;
          const chipClass = cn(
            'mx-0.5 whitespace-nowrap rounded px-1 py-0 align-super text-[10px] font-medium',
            'bg-[var(--ui-inset-soft)]',
            className,
          );
          return c.href ? (
            <Link
              key={key}
              to={c.href}
              className={cn(chipClass, 'transition-colors hover:bg-white/10')}
              title={c.locator ?? metaFor(c.kind).word}
            >
              {text}
            </Link>
          ) : (
            <span key={key} className={chipClass} title={c.locator ?? metaFor(c.kind).word}>
              {text}
            </span>
          );
        })}
      </>
    );
  }

  const chipClass =
    'inline-flex items-center gap-1 rounded-full border border-border bg-[var(--ui-inset-soft)] px-2 py-0.5 text-[11.5px] text-[var(--ui-soft)] transition-colors';

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {shown.map((c) => {
        const key = `${c.kind}:${c.id}`;
        const { icon: Icon, word } = metaFor(c.kind);
        const inner = (
          <>
            <Icon className="h-3 w-3 shrink-0 opacity-70" aria-hidden="true" />
            <span className="opacity-60">{word}</span>
            <span className="max-w-[180px] truncate">{c.label}</span>
          </>
        );
        // href === null means there is no record to open (an aggregate, or a
        // role model computed on the fly rather than stored) — a chip stays a
        // chip, not a live link to nowhere.
        return c.href ? (
          <Link
            key={key}
            to={c.href}
            className={cn(chipClass, 'hover:bg-white/10')}
            title={c.locator ?? undefined}
          >
            {inner}
          </Link>
        ) : (
          <span key={key} className={chipClass} title={c.locator ?? undefined}>
            {inner}
          </span>
        );
      })}
      {hidden > 0 && (
        <span className="px-1 py-0.5 text-[11.5px] text-[var(--ui-faint)]">+{hidden} more</span>
      )}
    </div>
  );
}

const MARKER_PATTERN = /\[(S\d+)\]/g;

/**
 * Renders `text` with every `[S1]`-style marker replaced by an inline chip
 * linking to the matching citation's target — the same target its entry in
 * the source strip links to, because it IS the same citation object.
 *
 * The server strips any marker not backed by a real citation before the
 * reply reaches here (`shared/agents/runtime.py::bind_refs`), so this is
 * belt-and-braces, not the control: a marker with no match on this side is
 * left exactly as written rather than silently dropped or turned into a dead
 * link.
 */
export function CitedText({
  text,
  citations,
}: {
  text: string;
  citations: CitationLike[];
}): JSX.Element {
  const byRef = new Map<string, CitationLike>();
  for (const c of citations) {
    if (c.ref) byRef.set(c.ref, c);
  }

  if (byRef.size === 0) return <>{text}</>;

  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let markerCount = 0;

  while ((match = MARKER_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const citation = byRef.get(match[1]);
    if (citation) {
      parts.push(
        <CitationChips key={`marker-${markerCount++}`} citations={[citation]} variant="inline" />,
      );
    } else {
      // Unmatched marker: left exactly as it is, not converted or dropped.
      parts.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));

  return <>{parts}</>;
}
