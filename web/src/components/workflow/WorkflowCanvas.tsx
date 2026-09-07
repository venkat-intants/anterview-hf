// WorkflowCanvas — D1. The chain of rounds a candidate walks, as an object.
//
// Vertical rather than the free-floating 2D graph the n8n comparison suggests,
// and that is the main design decision in this file. Execution IS linear: the
// runner walks `on_pass_next_round_id` from the first round and validate_chain
// refuses to publish anything unreachable or looping. A canvas that let HR drag
// nodes anywhere would be drawing a topology the engine cannot run, and every
// arrangement it allowed that the server rejects is a lie the builder told.
//
// So the canvas keeps what makes a node graph legible — discrete objects, an
// explicit path between them, direct manipulation — and drops the coordinate
// plane, which here carries no meaning.
//
// Reordering is offered twice on purpose. Dragging is what people reach for;
// the up/down buttons are what works with a keyboard, a screen reader, or a
// trackpad someone is fighting with. They call the same handler.

import { useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  GripVertical,
  Plus,
  Trash2,
} from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { cn } from '@/lib/utils';
import type { Round, RoundKind } from '@/api/workflows';
import { ROUND_KIND_META, ROUND_KIND_ORDER } from './roundKinds';

interface Props {
  rounds: Round[];
  selectedId: string | null;
  editable: boolean;
  /** Rounds this canvas is mid-mutation on — the node shows as busy. */
  busy?: boolean;
  onSelect: (roundId: string) => void;
  onAdd: (kind: RoundKind) => void;
  onRemove: (roundId: string) => void;
  /** Full ordered list of ids, not a delta — the server takes the whole order. */
  onReorder: (roundIds: string[]) => void;
}

function moveWithin<T>(items: T[], from: number, to: number): T[] {
  if (from === to || to < 0 || to >= items.length) return items;
  const next = items.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/** What this round still needs before the workflow can go live. */
function nodeGap(round: Round): string | null {
  const meta = ROUND_KIND_META[round.kind];
  if (meta.needsExam && round.needs_questions) return 'No questions attached';
  if (meta.needsThreshold && round.pass_threshold === null) return 'No advance threshold';
  return null;
}

function RoundNode({
  round,
  index,
  total,
  selected,
  editable,
  onSelect,
  onRemove,
  onMove,
  dragProps,
}: {
  round: Round;
  index: number;
  total: number;
  selected: boolean;
  editable: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onMove: (to: number) => void;
  dragProps: React.HTMLAttributes<HTMLDivElement> & { draggable?: boolean };
}) {
  const meta = ROUND_KIND_META[round.kind];
  const Icon = meta.icon;
  const gap = nodeGap(round);

  return (
    <div
      {...dragProps}
      className={cn(
        'group relative flex w-full items-center gap-3 rounded-[16px] border bg-[#0f0f10] p-3.5 text-left transition-colors',
        selected
          ? 'border-[var(--accent)] shadow-[0_0_0_1px_var(--accent)]'
          : 'border-white/[0.08] hover:border-white/20',
      )}
    >
      {editable ? (
        <span
          className="cursor-grab text-[#5a5f66] group-hover:text-[#888b91]"
          aria-hidden="true"
        >
          <GripVertical className="h-4 w-4" />
        </span>
      ) : (
        <span className="w-4" aria-hidden="true" />
      )}

      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="flex min-w-0 flex-1 items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-white/[0.06]">
          <Icon className="h-[18px] w-[18px] text-[#d5d7da]" aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium text-white">{round.title}</span>
            <StatusTag tone={meta.tone}>{meta.label}</StatusTag>
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11.5px] text-[#70757c]">
            <span>Step {index + 1}</span>
            {round.pass_threshold !== null ? (
              <span>advance at {round.pass_threshold}%</span>
            ) : null}
            {round.criteria.length > 0 ? (
              <span>
                {round.criteria.length} competenc
                {round.criteria.length === 1 ? 'y' : 'ies'}
              </span>
            ) : null}
            <span>{round.deadline_days}d to complete</span>
          </span>
          {gap ? (
            <span className="mt-1 flex items-center gap-1 text-[11.5px] text-[#ffb764]">
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              {gap}
            </span>
          ) : null}
        </span>
      </button>

      {editable ? (
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <button
            type="button"
            onClick={() => onMove(index - 1)}
            disabled={index === 0}
            aria-label={`Move ${round.title} earlier`}
            className="rounded-[8px] p-1.5 text-[#888b91] hover:bg-white/[0.06] hover:text-white disabled:opacity-25"
          >
            <ChevronDown className="h-4 w-4 rotate-180" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => onMove(index + 1)}
            disabled={index === total - 1}
            aria-label={`Move ${round.title} later`}
            className="rounded-[8px] p-1.5 text-[#888b91] hover:bg-white/[0.06] hover:text-white disabled:opacity-25"
          >
            <ChevronDown className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ${round.title}`}
            className="rounded-[8px] p-1.5 text-[#888b91] hover:bg-[#e6714f]/15 hover:text-[#e6714f]"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        </span>
      ) : null}
    </div>
  );
}

export default function WorkflowCanvas({
  rounds,
  selectedId,
  editable,
  busy = false,
  onSelect,
  onAdd,
  onRemove,
  onReorder,
}: Props): JSX.Element {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);

  const move = (from: number, to: number) => {
    const next = moveWithin(rounds, from, to);
    if (next !== rounds) onReorder(next.map((r) => r.id));
  };

  return (
    <div className={cn('flex flex-col', busy && 'pointer-events-none opacity-60')}>
      {/* Entry marker — where candidates come in. Not a round, and not
          selectable: making the start point look like a node people can
          configure is the fastest way to get "why can't I edit this?". */}
      <div className="flex items-center gap-2.5 px-1 text-[12px] text-[#70757c]">
        <span className="flex h-6 w-6 items-center justify-center rounded-full border border-white/15">
          <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
        Shortlisted candidates enter here
      </div>

      <ol className="mt-2 flex list-none flex-col">
        {rounds.map((round, i) => (
          <li key={round.id} className="flex flex-col">
            {i > 0 ? (
              <span
                className="ml-[26px] h-5 w-px bg-white/15"
                aria-hidden="true"
                data-testid="round-connector"
              />
            ) : null}
            <RoundNode
              round={round}
              index={i}
              total={rounds.length}
              selected={round.id === selectedId}
              editable={editable}
              onSelect={() => onSelect(round.id)}
              onRemove={() => onRemove(round.id)}
              onMove={(to) => move(i, to)}
              dragProps={
                editable
                  ? {
                      draggable: true,
                      onDragStart: () => setDragFrom(i),
                      onDragOver: (e) => e.preventDefault(),
                      onDrop: () => {
                        if (dragFrom !== null) move(dragFrom, i);
                        setDragFrom(null);
                      },
                      onDragEnd: () => setDragFrom(null),
                    }
                  : {}
              }
            />
          </li>
        ))}
      </ol>

      {rounds.length > 0 ? (
        <>
          <span className="ml-[26px] h-5 w-px bg-white/15" aria-hidden="true" />
          <div className="flex items-center gap-2.5 px-1 text-[12px] text-[#70757c]">
            <span className="flex h-6 w-6 items-center justify-center rounded-full border border-white/15">
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            {/* Says what actually happens at the end, which is the single most
                misread part of the product: finishing the last round does not
                hire anyone. */}
            Finishing the last round puts a candidate in your decision queue
          </div>
        </>
      ) : null}

      {editable ? (
        <div className="mt-5">
          {adding ? (
            <div className="rounded-[16px] border border-dashed border-white/15 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[12.5px] font-medium text-[#b8babf]">Add a round</span>
                <button
                  type="button"
                  onClick={() => setAdding(false)}
                  className="text-[12px] text-[#888b91] hover:text-white"
                >
                  Cancel
                </button>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {ROUND_KIND_ORDER.map((kind) => {
                  const meta = ROUND_KIND_META[kind];
                  const Icon = meta.icon;
                  return (
                    <button
                      key={kind}
                      type="button"
                      onClick={() => {
                        onAdd(kind);
                        setAdding(false);
                      }}
                      className="flex items-start gap-2.5 rounded-[12px] border border-white/[0.08] p-3 text-left hover:border-[var(--accent)]/50 hover:bg-white/[0.03]"
                    >
                      <Icon
                        className="mt-0.5 h-4 w-4 shrink-0 text-[#d5d7da]"
                        aria-hidden="true"
                      />
                      <span>
                        <span className="block text-[13px] font-medium text-white">
                          {meta.label}
                        </span>
                        <span className="mt-0.5 block text-[11.5px] leading-snug text-[#888b91]">
                          {meta.blurb}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="flex w-full items-center justify-center gap-1.5 rounded-[16px] border border-dashed border-white/15 py-3 text-[13px] text-[#888b91] hover:border-[var(--accent)]/50 hover:text-white"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add a round
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}
