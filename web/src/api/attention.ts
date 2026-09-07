// attention.ts — what needs a human in this company's hiring, right now.
//
// The counterpart to the notification bell rather than a duplicate of it. The
// bell is a push channel and is deduplicated, so a condition tells you once and
// then goes quiet; this is computed fresh on every read, so a problem nobody
// has fixed keeps showing up until somebody fixes it.

import { apiGet } from './client';

/** The record a finding is about, so the panel can link straight to it. */
export interface AttentionCitation {
  kind: string;
  id: string;
  label: string;
  href: string | null;
}

export type AttentionSeverity = 'critical' | 'warning' | 'info';

export interface AttentionItem {
  /** The rule that fired. Useful for grouping; not shown to the user as-is. */
  watcher: string;
  severity: AttentionSeverity;
  title: string;
  body: string;
  /** Where to go to act on it. Every finding should have one. */
  link: string | null;
  /** Stable across refreshes, which makes it the right list key. */
  dedupe_key: string;
  citations: AttentionCitation[];
}

export interface AttentionBoard {
  generated_at: string;
  total: number;
  /** Already sorted worst-first by the server. Do not re-sort. */
  items: AttentionItem[];
}

export function getAttention(): Promise<AttentionBoard> {
  return apiGet<AttentionBoard>('/hr/attention');
}
