// useDialogFocus — shared modal-dialog accessibility (WCAG 2.1 AA).
//
// Extracted from the pattern CandidateDrawer.tsx already used for Escape +
// initial focus (~699-710), widened to a full trap: Tab/Shift+Tab stay
// inside the dialog, and focus returns to whatever triggered it once the
// dialog closes. Written once so `MetricInfoDialog`, `MembersDrillDown` and
// any future HRAnalytics overlay get it identically rather than by copying
// three slightly-different keydown handlers.
//
// Usage: a component that is only ever MOUNTED while open (every dialog in
// this codebase — the parent renders `{open ? <Dialog .../> : null}`) calls
// `const panelRef = useDialogFocus<HTMLDivElement>(onClose);` and puts
// `ref={panelRef}` on its `role="dialog"` element. Mount = open, unmount =
// close, so the effect's cleanup is exactly "restore focus on close".

import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function focusableIn(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/**
 * Escape closes; focus moves into the panel on mount and is trapped there
 * (Tab from the last focusable wraps to the first, Shift+Tab from the first
 * wraps to the last); focus returns to whatever was focused before the
 * dialog opened once it unmounts.
 */
export function useDialogFocus<T extends HTMLElement = HTMLDivElement>(
  onClose: () => void,
): RefObject<T> {
  const panelRef = useRef<T>(null);

  useEffect(() => {
    const panel = panelRef.current;
    const triggeredBy = document.activeElement as HTMLElement | null;

    // Focus the panel's first focusable child, or the panel itself as a
    // fallback (it needs tabIndex={-1} for that to work — every dialog here
    // sets one) so a dialog with nothing else focusable is still reachable.
    const first = panel ? focusableIn(panel)[0] : undefined;
    (first ?? panel)?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !panel) return;
      const items = focusableIn(panel);
      if (items.length === 0) return;
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      // Still in the document (not e.g. a row removed by the same action
      // that closed the dialog) — a trigger that no longer exists has
      // nowhere sensible to return focus to.
      if (triggeredBy && document.contains(triggeredBy)) {
        triggeredBy.focus();
      }
    };
    // Deliberately once per mount: this dialog IS the open state (see the
    // module comment) — there is no "same dialog, different onClose" case
    // to re-run the effect for, and re-running on every onClose identity
    // change would re-focus the panel on each re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return panelRef;
}
