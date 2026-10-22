// getAllJobs walks every page of GET /jobs (audit AD-F10).
//
// The JD admin page fed its picker from getJobs(), which returns the server's
// default first page of 20, ordered by title: every practice role after the
// 20th alphabetically could never be selected.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Job, JobsListResponse } from '../types/interview';

const apiGet = vi.fn();
vi.mock('../api/client', () => ({
  apiGet: (...a: unknown[]) => apiGet(...a) as unknown,
  apiPost: vi.fn(),
}));

import { getAllJobs } from '../api/jobs';

function job(n: number): Job {
  return {
    id: `job-${n}`,
    title: `Role ${String(n).padStart(3, '0')}`,
    description: '',
    level: 'entry',
    language: 'en',
    is_active: true,
  };
}

function pageOf(page: number, size: number, total: number): JobsListResponse {
  const start = (page - 1) * 100;
  return {
    items: Array.from({ length: size }, (_, i) => job(start + i + 1)),
    total,
    page,
    per_page: 100,
  };
}

beforeEach(() => {
  apiGet.mockReset();
});

describe('getAllJobs', () => {
  it('reads every page at the maximum page size until it has them all', async () => {
    apiGet
      .mockResolvedValueOnce(pageOf(1, 100, 230))
      .mockResolvedValueOnce(pageOf(2, 100, 230))
      .mockResolvedValueOnce(pageOf(3, 30, 230));

    const jobs = await getAllJobs();

    expect(jobs).toHaveLength(230);
    expect(apiGet.mock.calls.map((c: unknown[]) => String(c[0]))).toEqual([
      '/jobs?page=1&per_page=100',
      '/jobs?page=2&per_page=100',
      '/jobs?page=3&per_page=100',
    ]);
  });

  it('stops after one request for a small catalogue', async () => {
    apiGet.mockResolvedValueOnce(pageOf(1, 12, 12));
    expect(await getAllJobs()).toHaveLength(12);
    expect(apiGet).toHaveBeenCalledTimes(1);
  });

  it('stops when a full page is also the last one', async () => {
    apiGet.mockResolvedValueOnce(pageOf(1, 100, 100));
    expect(await getAllJobs()).toHaveLength(100);
    expect(apiGet).toHaveBeenCalledTimes(1);
  });
});
