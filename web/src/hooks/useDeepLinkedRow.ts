// useDeepLinkedRow — land a deep link on the ROW it names, not just the list
// that contains it.
//
// PH5-E1/E2. A citation chip ("Document — Leave policy") is a promise that the
// link identifies one record. Several of those records have no page of their
// own: the thing that shows them IS a list screen, so the route opens the list
// and this hook scrolls the named row into view and focuses it. Focus, not
// only scroll — a keyboard or screen-reader user arriving from a citation must
// land on the record too, or the deep link is sighted-users-only.
//
// The target element needs `tabIndex={-1}` for `focus()` to take (the same
// requirement CandidateDrawer's section anchors have).
//
// Runs ONCE per id, deliberately. Both screens using this poll their list while
// something is in flight (a document indexing, an interview in progress), and
// re-focusing on every refetch would yank the caret out of whatever the user
// had started typing seconds after they arrived.

import { useEffect, useRef } from 'react';

/**
 * @param elementId DOM id of the row to land on — null when this is not a
 *                  deep link, which is the common case.
 * @param ready     False while the list is still loading; the row does not
 *                  exist yet and the effect must not conclude it never will.
 */
export function useDeepLinkedRow(elementId: string | null, ready: boolean): void {
  const landedOn = useRef<string | null>(null);

  useEffect(() => {
    if (!elementId || !ready || landedOn.current === elementId) return;
    const el = document.getElementById(elementId);
    if (!el) return;
    landedOn.current = elementId;
    // Feature-detected: jsdom and some embedded webviews have no
    // scrollIntoView at all (CandidateDrawer's own note).
    el.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    el.focus?.();
  }, [elementId, ready]);
}
