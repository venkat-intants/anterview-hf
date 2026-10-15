// EditorHeader — the one frame every HR authoring screen shares (exam editor,
// question bank, hiring workflow, offer). It stays in view while a long
// document is being edited, and it always reads in the same order:
//
//   breadcrumbs
//   title · status pill                         secondary actions · PRIMARY
//   meta · save state
//
// Exactly one primary action lives on the right; everything else is a
// secondary (outline) or quiet control. The save-state wording is shared so a
// reviewer never has to learn four dialects of "saved".
//
// Purely presentational — callers own every mutation, query and permission.

import type { ReactNode } from 'react';
import { Breadcrumbs, type Crumb } from '@/design/components/page';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Check, Loader2, AlertTriangle } from '@/design/components/icons';
import { cn } from '@/lib/utils';

export type SaveState = 'saved' | 'saving' | 'unsaved' | 'error';

/** The only four phrasings of save state used on an editor screen. */
const SAVE_STATE_TEXT: Record<SaveState, string> = {
  saved: 'All changes saved',
  saving: 'Saving…',
  unsaved: 'Unsaved changes',
  error: 'Could not save',
};

export type DocStatus = 'draft' | 'live' | 'locked' | 'approved';

const DOC_STATUS_TONE: Record<DocStatus, TagTone> = {
  draft: 'amber',
  live: 'forest',
  locked: 'neutral',
  approved: 'electric',
};

/** A document's lifecycle pill. The word is always shown — colour is never the signal. */
export function DocStatusPill({
  status,
  children,
}: {
  status: DocStatus;
  children?: ReactNode;
}): JSX.Element {
  const label = children ?? status[0].toUpperCase() + status.slice(1);
  return (
    <StatusTag tone={DOC_STATUS_TONE[status]} dot>
      {label}
    </StatusTag>
  );
}

export function SaveIndicator({ state }: { state: SaveState }): JSX.Element {
  return (
    <span
      role="status"
      aria-live="polite"
      data-testid="save-state"
      className={cn(
        'inline-flex items-center gap-1.5 text-[13px]',
        state === 'error' ? 'text-[var(--ui-danger)]' : 'text-muted-foreground',
      )}
    >
      {state === 'saving' ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
      ) : state === 'saved' ? (
        <Check className="h-3.5 w-3.5" aria-hidden="true" />
      ) : state === 'error' ? (
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
      ) : null}
      {SAVE_STATE_TEXT[state]}
    </span>
  );
}

interface EditorHeaderProps {
  breadcrumbs: Crumb[];
  title: ReactNode;
  titleTestId?: string;
  /** A <DocStatusPill> (or any pill) beside the title. */
  status?: ReactNode;
  /** Facts under the title — counts, version, owner. */
  meta?: ReactNode;
  /** Omit when the screen has no unsaved-state concept to report. */
  saveState?: SaveState;
  /** Outline / quiet controls, left of the primary. */
  secondary?: ReactNode;
  /** The screen's single primary action. */
  primary?: ReactNode;
}

export function EditorHeader({
  breadcrumbs,
  title,
  titleTestId,
  status,
  meta,
  saveState,
  secondary,
  primary,
}: EditorHeaderProps): JSX.Element {
  return (
    <header className="sticky top-16 z-10 -mx-4 mb-6 border-b border-border bg-background px-4 py-3 sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10">
      <Breadcrumbs items={breadcrumbs} />
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1
              data-testid={titleTestId}
              className="min-w-0 break-words text-[24px] font-bold leading-tight tracking-[-0.5px] text-foreground sm:text-[26px]"
            >
              {title}
            </h1>
            {status}
          </div>
          {meta || saveState ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
              {meta}
              {saveState ? <SaveIndicator state={saveState} /> : null}
            </div>
          ) : null}
        </div>
        {secondary || primary ? (
          <div className="flex flex-wrap items-center gap-2">
            {secondary}
            {primary}
          </div>
        ) : null}
      </div>
    </header>
  );
}
