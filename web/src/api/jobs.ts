// Jobs API — switches between mock and real backend via VITE_USE_MOCK env var.
// Mirrors data_gateway GET /jobs, GET /jobs/{id}, and POST /jobs endpoints.

import type { Job, JobsListResponse } from '../types/interview';
import { mockJobsResponse, simulateDelay } from './mock';
import { apiGet, apiPost } from './client';
import { pathId } from './pathId';

export interface CreateCustomJobRequest {
  title: string;
  company_name?: string;
  jd_text?: string;
  level?: 'entry' | 'mid' | 'senior';
  interview_type?: string;
  description?: string;
}

export interface CreateCustomJobResponse {
  id: string;
  title: string;
}

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

/**
 * Fetch the list of available jobs.
 * The `_token` parameter is accepted for backwards-compatibility but ignored.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export async function getJobs(_token?: string): Promise<JobsListResponse> {
  if (USE_MOCK) {
    await simulateDelay(500);
    return mockJobsResponse;
  }
  return apiGet<JobsListResponse>('/jobs');
}

/** The server's page-size ceiling for GET /jobs. */
const JOBS_PAGE_MAX = 100;
/** A runaway guard: 50 pages of 100 is far beyond any real catalogue. */
const JOBS_PAGE_LIMIT = 50;

/**
 * Every role in the practice catalogue, across pages (audit AD-F10).
 *
 * GET /jobs returns 20 per page by default, so a picker fed by getJobs() could
 * never offer the roles after the first 20 alphabetically. This walks the pages
 * at the server's maximum page size until it has `total` rows.
 */
export async function getAllJobs(): Promise<Job[]> {
  if (USE_MOCK) {
    await simulateDelay(500);
    return mockJobsResponse.items;
  }
  const all: Job[] = [];
  for (let page = 1; page <= JOBS_PAGE_LIMIT; page += 1) {
    const res = await apiGet<JobsListResponse>(`/jobs?page=${page}&per_page=${JOBS_PAGE_MAX}`);
    all.push(...res.items);
    if (res.items.length < JOBS_PAGE_MAX || all.length >= res.total) break;
  }
  return all;
}

/**
 * Fetch a single job by ID.
 * The `_token` parameter is accepted for backwards-compatibility but ignored.
 */
export async function getJob(
  id: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
): Promise<Job> {
  if (USE_MOCK) {
    await simulateDelay(200);
    const job = mockJobsResponse.items.find((j) => j.id === id);
    if (!job) throw new Error('Job not found');
    return job;
  }
  return apiGet<Job>(`/jobs/${pathId(id)}`);
}

/**
 * Create a custom job posting.
 * The `_token` parameter is accepted for backwards-compatibility but ignored.
 */
export async function createCustomJob(
  body: CreateCustomJobRequest,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
): Promise<CreateCustomJobResponse> {
  if (USE_MOCK) {
    await simulateDelay(500);
    return { id: 'mock-job', title: body.title };
  }
  return apiPost<CreateCustomJobResponse>('/jobs', body);
}
