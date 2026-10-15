// BankAddPanel - stage 2 of the bank flow: put questions in the bank.
//
// ONE chooser, four ways in, and only ways the API really supports:
//   Write one yourself              createBankQuestion
//   Generate MCQs with AI           generateBankQuestions, then a bulk save
//   Generate coding questions       generateBankCodingQuestions, then a bulk save
//   Import from a file              importBankQuestions
//
// Picking one opens that method's panel in place and nothing else; there is
// always a way back to the chooser. Whatever a method adds arrives as a DRAFT -
// AI never approves or publishes, and an import is not a way past review.
//
// The tally ("3 MCQ · 2 coding in this bank") is read from the bank's real
// question list, so it moves when a method saves, not when a toast says it did.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Code2,
  Pencil,
  Sparkles,
  Upload,
  type LucideIcon,
} from '@/design/components/icons';
import { Card } from '@/design/components/card';
import { Well } from '@/design/components/rows';
import { Button } from '@/components/ui/button';
import BankFillPanel from '@/components/bank/BankFillPanel';
import { BankQuestionEditor } from '@/components/bank/BankQuestionEditor';
import type { UnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { CodingQuestionGenerator } from '@/components/hr/bank/CodingQuestionGenerator';
import { toast } from '@/lib/toast';
import { kindTally, type BankFacts } from '@/lib/bankFlow';
import { createBankQuestion, type BankQuestion, type BankQuestionInput } from '@/api/questionBanks';

export type AddMethod = 'write' | 'mcq-ai' | 'coding-ai' | 'import';

interface MethodSpec {
  key: AddMethod;
  title: string;
  blurb: string;
  icon: LucideIcon;
}

const ADD_METHODS: readonly MethodSpec[] = [
  {
    key: 'write',
    title: 'Write one yourself',
    blurb: 'Type a question, either kind. Best for the few that matter most.',
    icon: Pencil,
  },
  {
    key: 'mcq-ai',
    title: 'Generate MCQs with AI',
    blurb: 'Describe a topic and read a preview. Nothing is saved until you add it.',
    icon: Sparkles,
  },
  {
    key: 'coding-ai',
    title: 'Generate coding questions with AI',
    blurb: 'Statement, tests and a reference solution to read, then keep what is good.',
    icon: Code2,
  },
  {
    key: 'import',
    title: 'Import from a file',
    blurb: 'Upload an Excel or CSV sheet of MCQs, one question per row.',
    icon: Upload,
  },
];

const METHOD_TITLE = { write: 'Write a question' } as const;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** Focus the first heading in a freshly opened method, so the change is announced. */
function useFocusFirstHeading(active: unknown): React.RefObject<HTMLDivElement> {
  const ref = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const h = ref.current?.querySelector<HTMLElement>('h1, h2, h3');
    if (!h) return;
    h.setAttribute('tabindex', '-1');
    h.focus();
  }, [active]);
  return ref;
}

interface Props {
  bankId: string;
  facts: BankFacts;
  /** Whether the question list has loaded; the tally says "…" until it has. */
  factsReady: boolean;
  guard: UnsavedGuard;
  /** Go to the Check your drafts stage. */
  onCheckDrafts: () => void;
}

export function BankAddPanel({
  bankId,
  facts,
  factsReady,
  guard,
  onCheckDrafts,
}: Props): JSX.Element {
  const qc = useQueryClient();
  const [method, setMethod] = useState<AddMethod | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const [writeKey, setWriteKey] = useState(0);
  const bodyRef = useFocusFirstHeading(method);

  const invalidate = (): void => {
    void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-review-queue'] });
  };

  const createMut = useMutation({
    mutationFn: (input: BankQuestionInput) => createBankQuestion(bankId, input),
    onSuccess: () => {
      toast.success('Question created');
      guard.setDirty(false);
      setAdded('1 question saved as a draft.');
      setWriteKey((k) => k + 1);
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create this question')),
  });

  function open(next: AddMethod): void {
    setAdded(null);
    setMethod(next);
  }

  function back(): void {
    if (!guard.confirmLeave()) return;
    setMethod(null);
  }

  const tally = kindTally(facts.kinds);

  const onGenerated = (saved: BankQuestion[]): void => {
    setAdded(`${saved.length} coding draft${saved.length === 1 ? '' : 's'} added.`);
    invalidate();
  };
  const onFillAdded = (count: number): void => {
    setAdded(`${count} draft${count === 1 ? '' : 's'} added.`);
  };

  return (
    <div className="flex flex-col gap-4" ref={bodyRef} data-testid="bank-add-panel">
      <p className="text-[13.5px] text-muted-foreground" role="status" data-testid="bank-tally">
        In this bank now:{' '}
        <span className="font-semibold text-foreground">
          {!factsReady ? '…' : (tally ?? 'no questions yet')}
        </span>
        {factsReady && facts.draft > 0 ? ` · ${facts.draft} not yet submitted` : ''}
      </p>

      {method === null ? (
        <Card density="default">
          <h3 className="mb-3 text-[15px] font-semibold text-foreground">
            How do you want to add questions?
          </h3>
          <ul className="grid gap-3 sm:grid-cols-2" aria-label="Ways to add questions">
            {ADD_METHODS.map((m) => {
              const Icon = m.icon;
              return (
                <li key={m.key}>
                  <button
                    type="button"
                    onClick={() => open(m.key)}
                    data-testid={`method-${m.key}`}
                    className="group flex min-h-[88px] w-full items-start gap-3 rounded-[14px] border border-border bg-card p-4 text-left transition-colors hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] motion-reduce:transition-none"
                  >
                    <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[var(--ui-highlight-wash)] text-foreground">
                      <Icon size={18} aria-hidden="true" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[14px] font-semibold text-foreground">
                        {m.title}
                      </span>
                      <span className="mt-0.5 block text-[13px] leading-snug text-muted-foreground">
                        {m.blurb}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-[12.5px] text-muted-foreground">
            Every method saves drafts. Someone other than the author approves them later; nothing
            here skips that.
          </p>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" size="sm" onClick={back}>
              <ArrowLeft aria-hidden="true" />
              All methods
            </Button>
            {/* mcq-ai, import and coding-ai each bring a heading of their own. */}
            {method === 'write' ? (
              <h3 className="text-[15px] font-semibold text-foreground">{METHOD_TITLE.write}</h3>
            ) : null}
          </div>

          {added ? (
            <Well
              role="status"
              data-testid="bank-added"
              className="flex flex-wrap items-center gap-3"
            >
              <span className="text-[13.5px] text-foreground">
                {added} Read them before you send them.
              </span>
              <Button type="button" variant="outline" size="sm" onClick={onCheckDrafts}>
                Check your drafts
              </Button>
            </Well>
          ) : null}

          {method === 'write' ? (
            <Card density="default">
              <BankQuestionEditor
                key={writeKey}
                saving={createMut.isPending}
                onSave={(input) => createMut.mutate(input)}
                onDirtyChange={guard.setDirty}
              />
            </Card>
          ) : null}
          {method === 'mcq-ai' ? (
            <Card density="default">
              <BankFillPanel bankId={bankId} only="ai" onAdded={onFillAdded} />
            </Card>
          ) : null}
          {method === 'import' ? (
            <Card density="default">
              <BankFillPanel bankId={bankId} only="import" onAdded={onFillAdded} />
            </Card>
          ) : null}
          {/* The generator is a Card of its own and must not sit inside another. */}
          {method === 'coding-ai' ? (
            <CodingQuestionGenerator bankId={bankId} onGenerated={onGenerated} />
          ) : null}
        </>
      )}
    </div>
  );
}
