// The HR invalidation convention (audit X7): a mutation refreshes by prefix, so
// every HR list on screen catches up, not just the one key its author named.

import { describe, expect, it } from 'vitest';
import { QueryClient, type QueryKey } from '@tanstack/react-query';
import {
  HR_ROOT,
  invalidateAfterHrMutation,
  invalidatePrefixes,
  queryKeys,
} from '../lib/queryKeys';

function seeded(keys: QueryKey[]): QueryClient {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const key of keys) qc.setQueryData(key, { seeded: true });
  return qc;
}

const stale = (qc: QueryClient, key: QueryKey): boolean | undefined =>
  qc.getQueryState(key)?.isInvalidated;

describe('invalidateAfterHrMutation', () => {
  const HR_VIEWS: QueryKey[] = [
    ['hr', 'decision-queue', 'req-1'],
    ['hr', 'offers', '', 0],
    ['hr', 'interviews'],
    ['hr', 'pools'],
    queryKeys.requisitionReviewEnrolments('req-1'),
    ['hr-attention', 'summary'],
  ];
  const ELSEWHERE: QueryKey[] = [
    ['admin', 'offer-approvals'],
    ['candidate', 'applications'],
    queryKeys.jdHistory('req-1'),
  ];

  it('marks every HR view and the attention panel stale', async () => {
    const qc = seeded([...HR_VIEWS, ...ELSEWHERE]);

    await invalidateAfterHrMutation(qc);

    for (const key of HR_VIEWS) expect(stale(qc, key), JSON.stringify(key)).toBe(true);
  });

  it('leaves state outside the HR console alone unless asked', async () => {
    const qc = seeded([...HR_VIEWS, ...ELSEWHERE]);

    await invalidateAfterHrMutation(qc);

    for (const key of ELSEWHERE) expect(stale(qc, key), JSON.stringify(key)).toBe(false);
  });

  it('takes extra prefixes for state that lives outside ["hr"]', async () => {
    const qc = seeded(ELSEWHERE);

    await invalidateAfterHrMutation(qc, queryKeys.jdHistory('req-1'), ['admin', 'offer-approvals']);

    expect(stale(qc, queryKeys.jdHistory('req-1'))).toBe(true);
    expect(stale(qc, ['admin', 'offer-approvals'])).toBe(true);
    expect(stale(qc, ['candidate', 'applications'])).toBe(false);
  });

  it('is the HR root prefix, which the requisition keys share', () => {
    expect(queryKeys.requisition('x').slice(0, 1)).toEqual([...HR_ROOT]);
  });
});

describe('invalidatePrefixes', () => {
  it('invalidates exactly the keys that start with each prefix', async () => {
    const qc = seeded([['hr', 'exam', 'e-1', 'attempts'], ['hr', 'exams'], ['hr-attention']]);

    await invalidatePrefixes(qc, [['hr', 'exam']]);

    expect(stale(qc, ['hr', 'exam', 'e-1', 'attempts'])).toBe(true);
    expect(stale(qc, ['hr', 'exams'])).toBe(false);
    expect(stale(qc, ['hr-attention'])).toBe(false);
  });
});
