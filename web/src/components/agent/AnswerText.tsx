// AnswerText — an assistant reply, laid out so a long answer is readable.
//
// The model writes light Markdown (headings, bullet and numbered lists, pipe
// tables, fenced code, **bold**, `code`). This renders exactly that subset as
// React nodes — never as HTML, so nothing the model emits can inject markup —
// and hands every plain-text run to CitedText, which is what turns `[S1]`
// markers into links. Anything it does not recognise falls through to a
// paragraph, so an unusual reply degrades to the old plain-text rendering
// rather than disappearing.
//
// Presentation only: it changes how the text is laid out, never what it says,
// and it has no part in which citations or proposals exist.

import { Fragment, type ReactNode } from 'react';
import { CitedText, type CitationLike } from './CitationChips';

type Block =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'code'; text: string }
  | { kind: 'table'; head: string[]; rows: string[][] };

const LIST_ITEM = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/;
const ORDERED_ITEM = /^\s*\d+[.)]\s+/;
const HEADING = /^#{1,6}\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

function parse(text: string): Block[] {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === '') {
      i += 1;
      continue;
    }

    if (line.trim().startsWith('```')) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // the closing fence, if there was one
      blocks.push({ kind: 'code', text: body.join('\n') });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: 'heading', text: heading[1] });
      i += 1;
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      const head = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ kind: 'table', head, rows });
      continue;
    }

    if (LIST_ITEM.test(line)) {
      const ordered = ORDERED_ITEM.test(line);
      const items: string[] = [];
      while (i < lines.length) {
        const m = LIST_ITEM.exec(lines[i]);
        if (!m || ORDERED_ITEM.test(lines[i]) !== ordered) break;
        items.push(m[1]);
        i += 1;
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].trim().startsWith('```') &&
      !HEADING.test(lines[i]) &&
      !LIST_ITEM.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: 'paragraph', text: para.join('\n') });
  }

  return blocks;
}

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;

function Inline({ text, citations }: { text: string; citations: CitationLike[] }): JSX.Element {
  const parts = text.split(INLINE);
  return (
    <>
      {parts.map((part, i): ReactNode => {
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          return (
            <strong key={i} className="font-semibold">
              <CitedText text={part.slice(2, -2)} citations={citations} />
            </strong>
          );
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          return (
            <code
              key={i}
              className="rounded bg-[var(--ui-inset)] px-1 py-0.5 font-mono text-[12.5px] break-words"
            >
              {part.slice(1, -1)}
            </code>
          );
        }
        return <CitedText key={i} text={part} citations={citations} />;
      })}
    </>
  );
}

export default function AnswerText({
  text,
  citations,
}: {
  text: string;
  citations: CitationLike[];
}): JSX.Element {
  const blocks = parse(text);

  return (
    <div className="space-y-3 break-words text-[14.5px] leading-[1.65] text-foreground">
      {blocks.map((b, i) => (
        <Fragment key={i}>
          {b.kind === 'heading' ? (
            <h3 className="pt-1 text-[15px] font-bold tracking-[-0.1px] text-foreground">
              <Inline text={b.text} citations={citations} />
            </h3>
          ) : b.kind === 'paragraph' ? (
            <p className="whitespace-pre-wrap">
              <Inline text={b.text} citations={citations} />
            </p>
          ) : b.kind === 'list' ? (
            b.ordered ? (
              <ol className="list-decimal space-y-1 pl-5 marker:text-muted-foreground">
                {b.items.map((it, j) => (
                  <li key={j}>
                    <Inline text={it} citations={citations} />
                  </li>
                ))}
              </ol>
            ) : (
              <ul className="list-disc space-y-1 pl-5 marker:text-muted-foreground">
                {b.items.map((it, j) => (
                  <li key={j}>
                    <Inline text={it} citations={citations} />
                  </li>
                ))}
              </ul>
            )
          ) : b.kind === 'code' ? (
            <pre
              tabIndex={0}
              className="overflow-x-auto rounded-[10px] bg-[var(--ui-inset)] p-3 font-mono text-[12.5px] leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <code>{b.text}</code>
            </pre>
          ) : (
            <div className="overflow-x-auto rounded-[10px] border border-border">
              <table className="w-full border-collapse text-left text-[13px]">
                <thead className="bg-[var(--ui-inset-soft)]">
                  <tr>
                    {b.head.map((h, j) => (
                      <th key={j} scope="col" className="px-3 py-2 font-semibold text-foreground">
                        <Inline text={h} citations={citations} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {b.rows.map((r, j) => (
                    <tr key={j} className="border-t border-border">
                      {r.map((c, k) => (
                        <td key={k} className="px-3 py-2 align-top">
                          <Inline text={c} citations={citations} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Fragment>
      ))}
    </div>
  );
}
