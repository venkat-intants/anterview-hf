// tabKeys — the keyboard half of the WAI-ARIA tabs pattern, for the small
// hand-built tab bars in the exam editor, the coding composer and the attempt
// detail page (audit E26). Those bars had role="tab" with no tabpanel wiring
// and no arrow-key movement, so a keyboard user had to Tab through every tab
// and a screen reader could not tell which panel a tab controlled.
//
// Use: spread `tabListProps` on the role="tablist" element and `tabProps(key)`
// on each role="tab" button; give the panel `panelProps(key)`. Arrow keys move
// AND select (automatic activation, which suits two or three cheap panels),
// Home/End jump to the ends, and only the selected tab is in the Tab order.

import type { KeyboardEvent } from 'react';

export interface TabKeysOptions<K extends string> {
  /** A stable prefix for the generated ids, unique on the page. */
  idBase: string;
  keys: readonly K[];
  active: K;
  onChange: (key: K) => void;
}

export function tabIds(idBase: string, key: string): { tab: string; panel: string } {
  return { tab: `${idBase}-tab-${key}`, panel: `${idBase}-panel-${key}` };
}

export function useTabKeys<K extends string>({ idBase, keys, active, onChange }: TabKeysOptions<K>) {
  const move = (e: KeyboardEvent<HTMLElement>) => {
    const i = keys.indexOf(active);
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % keys.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + keys.length) % keys.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = keys.length - 1;
    if (next < 0) return;
    e.preventDefault();
    const key = keys[next];
    onChange(key);
    document.getElementById(tabIds(idBase, key).tab)?.focus();
  };

  return {
    tabListProps: { onKeyDown: move },
    tabProps: (key: K) => ({
      id: tabIds(idBase, key).tab,
      role: 'tab' as const,
      'aria-selected': key === active,
      'aria-controls': tabIds(idBase, key).panel,
      tabIndex: key === active ? 0 : -1,
    }),
    panelProps: (key: K) => ({
      id: tabIds(idBase, key).panel,
      role: 'tabpanel' as const,
      'aria-labelledby': tabIds(idBase, key).tab,
    }),
  };
}
