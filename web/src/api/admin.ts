// admin.ts — API client for admin_ops service (port 8004).
// Mirrors the exact Pydantic response models from analytics.py.
// Switches between mock and real backend via VITE_USE_MOCK env var.

import { simulateDelay } from './mock';
import { clientFetch, fetchBlobWithAuth } from './client';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const ADMIN_BASE: string =
  // ?? (not ||) so an explicit empty string means "same origin" — the HF Space
  // build serves the frontend and all APIs from one origin behind Caddy.
  import.meta.env.VITE_ADMIN_API_URL ?? 'http://localhost:8004';

// 401s from adminGet trigger a token refresh via clientFetch against
// VITE_API_BASE_URL/data_gateway, which issues the JWT that admin_ops also validates.
function adminGet<T>(path: string): Promise<T> {
  return clientFetch<T>(`${ADMIN_BASE}${path}`);
}

// Streamed/binary responses (the CSV export) must skip clientFetch's JSON
// parsing, but NOT its auth handling — fetchBlobWithAuth is the same wrapper
// minus the parsing, so an access token that expires mid-session still
// refreshes and retries instead of failing the download.
function adminGetRaw(path: string): Promise<Response> {
  return fetchBlobWithAuth(`${ADMIN_BASE}${path}`);
}

// ---------------------------------------------------------------------------
// Response type definitions — mirror analytics.py Pydantic models exactly
// ---------------------------------------------------------------------------

export interface OverviewResponse {
  total_candidates: number;
  total_interviews: number;
  completed_interviews: number;
  /** Fraction 0.0–1.0 */
  completion_rate: number;
  /** Rounded to 2 dp; null if no scored interviews exist */
  avg_composite_score: number | null;
  /** Rounded to 1 dp; null if no completed interviews exist */
  avg_duration_seconds: number | null;
  interviews_today: number;
  interviews_last_7d: number;
  interviews_last_30d: number;
}

export interface InterviewListItem {
  session_id: string;
  candidate_email: string;
  candidate_name: string | null;
  job_title: string | null;
  status: string;
  /** Language code, e.g. 'en', 'hi', 'te' */
  language: string;
  /** Rounded to 2 dp; null when unscored */
  composite_score: number | null;
  /** ISO-8601 UTC timestamp */
  created_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
}

export interface InterviewListResponse {
  items: InterviewListItem[];
  total: number;
  page: number;
  per_page: number;
}

// improvements are stored as {area, suggestion} objects (scorer ImprovementItem),
// NOT plain strings — must match the JSONB shape feedback_billing writes.
export interface ImprovementItem {
  area: string;
  suggestion: string;
}

export interface ScorecardDetail {
  scorecard_id: string;
  composite_score: number | null;
  communication: number | null;
  technical: number | null;
  problem_solving: number | null;
  confidence: number | null;
  /** Per-axis "why this score" text, keyed by axis. {} for legacy scorecards. */
  rationale?: Record<string, string>;
  strengths: string[] | null;
  improvements: ImprovementItem[] | null;
  summary: string | null;
}

export interface InterviewDetailResponse {
  session_id: string;
  candidate_email: string;
  candidate_name: string | null;
  candidate_preferred_language: string | null;
  job_title: string | null;
  status: string;
  language: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  scorecard: ScorecardDetail | null;
  /** Phase B proctoring — 0-100 integrity score; null if proctoring was off. */
  integrity_score?: number | null;
  /** Per-type event counts + flagged seconds; null if proctoring was off. */
  proctoring_summary?: ProctoringSummary | null;
  /** Time-ordered proctoring events (most recent first). */
  integrity_events?: IntegrityEventItem[];
}

export interface ProctoringSummary {
  by_type?: Record<string, number>;
  flagged_seconds?: Record<string, number>;
  total_events?: number;
  total_flagged_seconds?: number;
}

export interface IntegrityEventItem {
  event_type: string;
  started_at: string;
  ended_at?: string | null;
  duration_seconds?: number | null;
}

export interface ByRoleItem {
  job_id: string;
  job_title: string;
  interview_count: number;
  avg_composite: number | null;
  avg_communication: number | null;
  avg_technical: number | null;
  avg_problem_solving: number | null;
  avg_confidence: number | null;
}

export interface ByLanguageItem {
  language: string;
  interview_count: number;
  avg_composite: number | null;
}

export interface ScoreBucket {
  /** e.g. '0-2', '2-4', '4-6', '6-8', '8-10' */
  label: string;
  count: number;
}

export interface ScoreDistributionResponse {
  buckets: ScoreBucket[];
  avg_communication: number | null;
  avg_technical: number | null;
  avg_problem_solving: number | null;
  avg_confidence: number | null;
}

export interface TrendItem {
  /** ISO-8601 date string, e.g. '2026-05-01' */
  date: string;
  interview_count: number;
  avg_composite: number | null;
}

export interface TrendsResponse {
  items: TrendItem[];
  date_from: string;
  date_to: string;
}

// ---------------------------------------------------------------------------
// Filter params for list + export endpoints
// ---------------------------------------------------------------------------

export interface InterviewFilters {
  page?: number;
  per_page?: number;
  date_from?: string;
  date_to?: string;
  status?: string;
  language?: string;
  min_score?: number;
  max_score?: number;
  q?: string;
  sort_by?: 'created_at' | 'composite_score';
  sort_desc?: boolean;
}

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_OVERVIEW: OverviewResponse = {
  total_candidates: 142,
  total_interviews: 217,
  completed_interviews: 183,
  completion_rate: 0.8433,
  avg_composite_score: 6.74,
  avg_duration_seconds: 682.1,
  interviews_today: 4,
  interviews_last_7d: 31,
  interviews_last_30d: 98,
};

const MOCK_INTERVIEWS: InterviewListItem[] = [
  {
    session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    candidate_email: 'priya.sharma@example.com',
    candidate_name: 'Priya Sharma',
    job_title: 'Junior Java Developer',
    status: 'completed',
    language: 'en',
    composite_score: 7.85,
    created_at: '2026-06-01T10:00:00Z',
    completed_at: '2026-06-01T10:12:34Z',
    duration_seconds: 754,
  },
  {
    session_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    candidate_email: 'rahul.verma@example.com',
    candidate_name: 'Rahul Verma',
    job_title: 'Sales Associate',
    status: 'completed',
    language: 'hi',
    composite_score: 6.4,
    created_at: '2026-05-31T14:05:00Z',
    completed_at: '2026-05-31T14:16:20Z',
    duration_seconds: 680,
  },
  {
    session_id: 'cccccccc-cccc-cccc-cccc-cccccccccccc',
    candidate_email: 'ananya.reddy@example.com',
    candidate_name: 'Ananya Reddy',
    job_title: 'Data Entry Operator',
    status: 'abandoned',
    language: 'te',
    composite_score: null,
    created_at: '2026-05-30T09:30:00Z',
    completed_at: null,
    duration_seconds: null,
  },
  {
    session_id: 'dddddddd-dddd-dddd-dddd-dddddddddddd',
    candidate_email: 'kiran.kumar@example.com',
    candidate_name: 'Kiran Kumar',
    job_title: 'Junior Java Developer',
    status: 'completed',
    language: 'te',
    composite_score: 8.12,
    created_at: '2026-05-29T11:20:00Z',
    completed_at: '2026-05-29T11:33:45Z',
    duration_seconds: 825,
  },
  {
    session_id: 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
    candidate_email: 'meena.iyer@example.com',
    candidate_name: 'Meena Iyer',
    job_title: 'Sales Associate',
    status: 'completed',
    language: 'en',
    composite_score: 5.75,
    created_at: '2026-05-28T08:00:00Z',
    completed_at: '2026-05-28T08:11:10Z',
    duration_seconds: 670,
  },
];

const MOCK_INTERVIEW_DETAIL: InterviewDetailResponse = {
  session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  candidate_email: 'priya.sharma@example.com',
  candidate_name: 'Priya Sharma',
  candidate_preferred_language: 'en',
  job_title: 'Junior Java Developer',
  status: 'completed',
  language: 'en',
  started_at: '2026-06-01T10:00:00Z',
  completed_at: '2026-06-01T10:12:34Z',
  duration_seconds: 754,
  scorecard: {
    scorecard_id: '00000000-0000-0000-0000-000000000099',
    composite_score: 7.85,
    communication: 8.0,
    technical: 7.5,
    problem_solving: 8.0,
    confidence: 7.75,
    strengths: [
      'Articulate communicator — uses concrete examples effectively',
      'Strong grasp of Java OOP fundamentals',
      'Structured problem-solving approach',
    ],
    improvements: [
      {
        area: 'Java Concurrency',
        suggestion: 'Expand knowledge of thread safety and concurrent collections.',
      },
      {
        area: 'Confidence',
        suggestion: 'Speaking pace slowed under pressure — practise timed responses.',
      },
    ],
    summary:
      'A strong junior candidate with good foundations in Java and clear communication. ' +
      'Minor gaps in advanced concurrency and confidence under time pressure. Recommended for interview stage two.',
  },
};

const MOCK_BY_ROLE: ByRoleItem[] = [
  {
    job_id: '11111111-1111-1111-1111-111111111111',
    job_title: 'Junior Java Developer',
    interview_count: 89,
    avg_composite: 7.23,
    avg_communication: 7.4,
    avg_technical: 7.0,
    avg_problem_solving: 7.2,
    avg_confidence: 7.3,
  },
  {
    job_id: '22222222-2222-2222-2222-222222222222',
    job_title: 'Sales Associate',
    interview_count: 72,
    avg_composite: 6.58,
    avg_communication: 7.1,
    avg_technical: 5.8,
    avg_problem_solving: 6.5,
    avg_confidence: 6.9,
  },
  {
    job_id: '33333333-3333-3333-3333-333333333333',
    job_title: 'Data Entry Operator',
    interview_count: 56,
    avg_composite: 6.15,
    avg_communication: 6.3,
    avg_technical: 5.9,
    avg_problem_solving: 6.1,
    avg_confidence: 6.4,
  },
];

const MOCK_BY_LANGUAGE: ByLanguageItem[] = [
  { language: 'en', interview_count: 121, avg_composite: 6.91 },
  { language: 'hi', interview_count: 63, avg_composite: 6.44 },
  { language: 'te', interview_count: 33, avg_composite: 6.72 },
];

const MOCK_SCORE_DISTRIBUTION: ScoreDistributionResponse = {
  buckets: [
    { label: '0-2', count: 4 },
    { label: '2-4', count: 11 },
    { label: '4-6', count: 38 },
    { label: '6-8', count: 97 },
    { label: '8-10', count: 33 },
  ],
  avg_communication: 7.02,
  avg_technical: 6.48,
  avg_problem_solving: 6.75,
  avg_confidence: 6.93,
};

function makeTrends(): TrendsResponse {
  const items: TrendItem[] = [];
  const now = new Date('2026-06-02');
  for (let i = 29; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const ds = d.toISOString().slice(0, 10);
    const count = Math.max(0, Math.round(3 + Math.sin(i * 0.4) * 2 + Math.random() * 2));
    items.push({
      date: ds,
      interview_count: count,
      avg_composite: count > 0 ? Math.round((6 + Math.random() * 2) * 100) / 100 : null,
    });
  }
  return { items, date_from: '2026-05-04', date_to: '2026-06-02' };
}

const MOCK_TRENDS: TrendsResponse = makeTrends();

// ---------------------------------------------------------------------------
// Query string builder
// ---------------------------------------------------------------------------

function buildQs(filters: InterviewFilters): string {
  const params = new URLSearchParams();
  if (filters.page !== undefined) params.set('page', String(filters.page));
  if (filters.per_page !== undefined) params.set('per_page', String(filters.per_page));
  if (filters.date_from) params.set('date_from', filters.date_from);
  if (filters.date_to) params.set('date_to', filters.date_to);
  if (filters.status) params.set('status', filters.status);
  if (filters.language) params.set('language', filters.language);
  if (filters.min_score !== undefined) params.set('min_score', String(filters.min_score));
  if (filters.max_score !== undefined) params.set('max_score', String(filters.max_score));
  if (filters.q) params.set('q', filters.q);
  if (filters.sort_by) params.set('sort_by', filters.sort_by);
  if (filters.sort_desc !== undefined) params.set('sort_desc', String(filters.sort_desc));
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/** GET /admin/overview — all KPI tiles in one request */
export async function getOverview(): Promise<OverviewResponse> {
  if (USE_MOCK) {
    await simulateDelay(350);
    return MOCK_OVERVIEW;
  }
  return adminGet<OverviewResponse>('/admin/overview');
}

// ---------------------------------------------------------------------------
// System health — GET /admin/system/health (backs the AdminOverview status board)
// ---------------------------------------------------------------------------

export interface SystemServiceHealth {
  name: string;
  kind: 'service' | 'datastore';
  status: 'operational' | 'degraded' | 'down';
  latency_ms: number | null;
  detail: string | null;
}

export interface SystemHealthResponse {
  overall: 'operational' | 'degraded';
  services: SystemServiceHealth[];
  checked_at: string;
}

const MOCK_SYSTEM_HEALTH: SystemHealthResponse = {
  overall: 'operational',
  services: [
    { name: 'admin_ops', kind: 'service', status: 'operational', latency_ms: 0, detail: null },
    { name: 'interview_core', kind: 'service', status: 'operational', latency_ms: 24, detail: null },
    { name: 'data_gateway', kind: 'service', status: 'operational', latency_ms: 18, detail: null },
    { name: 'feedback_billing', kind: 'service', status: 'operational', latency_ms: 21, detail: null },
    { name: 'postgres', kind: 'datastore', status: 'operational', latency_ms: null, detail: null },
    { name: 'redis', kind: 'datastore', status: 'operational', latency_ms: null, detail: null },
  ],
  checked_at: new Date().toISOString(),
};

/** GET /admin/system/health — peer-service + datastore status rollup. */
export async function getSystemHealth(): Promise<SystemHealthResponse> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return MOCK_SYSTEM_HEALTH;
  }
  return adminGet<SystemHealthResponse>('/admin/system/health');
}

// ---------------------------------------------------------------------------
// Interview transcript — GET /admin/interviews/{id}/transcript
// ---------------------------------------------------------------------------

export interface TranscriptTurn {
  turn_number: number;
  speaker: string; // interviewer | candidate
  text: string | null;
  created_at: string | null;
}

export interface TranscriptResponse {
  session_id: string;
  turns: TranscriptTurn[];
}

/** GET /admin/interviews/{sessionId}/transcript — ordered conversation turns. */
export async function getInterviewTranscript(sessionId: string): Promise<TranscriptResponse> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return { session_id: sessionId, turns: [] };
  }
  return adminGet<TranscriptResponse>(`/admin/interviews/${sessionId}/transcript`);
}

/** GET /admin/interviews — paginated, filterable interview list */
export async function listInterviews(
  filters: InterviewFilters = {},
): Promise<InterviewListResponse> {
  if (USE_MOCK) {
    await simulateDelay(300);
    const q = filters.q?.toLowerCase();
    let items = MOCK_INTERVIEWS.filter((item) => {
      if (filters.status && item.status !== filters.status) return false;
      if (filters.language && item.language !== filters.language) return false;
      if (filters.min_score !== undefined && (item.composite_score ?? -1) < filters.min_score)
        return false;
      if (filters.max_score !== undefined && (item.composite_score ?? 99) > filters.max_score)
        return false;
      if (q) {
        const nameMatch = item.candidate_name?.toLowerCase().includes(q) ?? false;
        const emailMatch = item.candidate_email.toLowerCase().includes(q);
        if (!nameMatch && !emailMatch) return false;
      }
      return true;
    });

    if (filters.sort_by === 'composite_score') {
      items = [...items].sort((a, b) => {
        const av = a.composite_score ?? -1;
        const bv = b.composite_score ?? -1;
        return filters.sort_desc !== false ? bv - av : av - bv;
      });
    } else {
      items = [...items].sort((a, b) => {
        const ad = new Date(a.created_at).getTime();
        const bd = new Date(b.created_at).getTime();
        return filters.sort_desc !== false ? bd - ad : ad - bd;
      });
    }

    const page = filters.page ?? 1;
    const perPage = filters.per_page ?? 20;
    const start = (page - 1) * perPage;
    const paged = items.slice(start, start + perPage);

    return { items: paged, total: items.length, page, per_page: perPage };
  }
  return adminGet<InterviewListResponse>(`/admin/interviews${buildQs(filters)}`);
}

/** GET /admin/interviews/{session_id} — full drill-in detail */
export async function getInterviewDetail(sessionId: string): Promise<InterviewDetailResponse> {
  if (USE_MOCK) {
    await simulateDelay(400);
    const found = MOCK_INTERVIEWS.find((i) => i.session_id === sessionId);
    if (!found) throw new Error(`Session ${sessionId} not found`);
    return { ...MOCK_INTERVIEW_DETAIL, session_id: sessionId };
  }
  return adminGet<InterviewDetailResponse>(`/admin/interviews/${sessionId}`);
}

/** GET /admin/analytics/by-role */
export async function getByRole(): Promise<ByRoleItem[]> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return MOCK_BY_ROLE;
  }
  return adminGet<ByRoleItem[]>('/admin/analytics/by-role');
}

/** GET /admin/analytics/by-language */
export async function getByLanguage(): Promise<ByLanguageItem[]> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return MOCK_BY_LANGUAGE;
  }
  return adminGet<ByLanguageItem[]>('/admin/analytics/by-language');
}

/** GET /admin/analytics/score-distribution */
export async function getScoreDistribution(): Promise<ScoreDistributionResponse> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return MOCK_SCORE_DISTRIBUTION;
  }
  return adminGet<ScoreDistributionResponse>('/admin/analytics/score-distribution');
}

/** GET /admin/analytics/trends?date_from=&date_to= */
export async function getTrends(
  dateFrom?: string,
  dateTo?: string,
): Promise<TrendsResponse> {
  if (USE_MOCK) {
    await simulateDelay(350);
    return MOCK_TRENDS;
  }
  const params = new URLSearchParams();
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  const qs = params.toString();
  return adminGet<TrendsResponse>(`/admin/analytics/trends${qs ? `?${qs}` : ''}`);
}

/**
 * Trigger download of GET /admin/interviews/export.csv.
 * Creates a temporary <a> element and clicks it — relies on the browser's
 * native download handling. Auth header is passed via the Bearer token.
 * In mock mode, synthesises a minimal CSV from MOCK_INTERVIEWS.
 */
export async function exportInterviewsCsv(filters: InterviewFilters = {}): Promise<void> {
  if (USE_MOCK) {
    const header =
      'session_id,candidate_email,candidate_name,job_title,status,language,composite_score,created_at,completed_at,duration_seconds\n';
    const rows = MOCK_INTERVIEWS.map((i) =>
      [
        i.session_id,
        i.candidate_email,
        i.candidate_name ?? '',
        i.job_title ?? '',
        i.status,
        i.language,
        i.composite_score ?? '',
        i.created_at,
        i.completed_at ?? '',
        i.duration_seconds ?? '',
      ].join(','),
    ).join('\n');
    const blob = new Blob([header + rows], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'interviews.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return;
  }

  const qs = buildQs(filters);
  const res = await adminGetRaw(`/admin/interviews/export.csv${qs}`);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'interviews.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
