// Mock API responses — used when VITE_USE_MOCK=true (default)
// Simulates data_gateway, interview_core, and feedback_billing responses
// without a running backend.

import type { LoginResponse, MeResponse, LogoutResponse } from '../types/auth';
import type { JobsListResponse, CreateSessionResponse } from '../types/interview';
import type { SessionListResponse } from './sessions';
import type { ScorecardListResponse } from './scorecard';
import type { ResumeVersionItem, ResumeCurrentResponse } from './resume';

const MOCK_USER_ID = '11111111-1111-1111-1111-111111111111';
const MOCK_ACCESS_TOKEN = 'mock-access-token';

export const mockAuthResponse: LoginResponse = {
  access_token: MOCK_ACCESS_TOKEN,
  expires_in: 900,
  user_id: MOCK_USER_ID,
  roles: ['candidate'],
};

export const mockMeResponse: MeResponse = {
  user_id: MOCK_USER_ID,
  full_name: 'Test Candidate',
  email: 'test@intants.com',
  roles: ['candidate'],
  has_resume: true,
};

export const mockLogoutResponse: LogoutResponse = {
  ok: true,
};

/** Simulate a network delay to make mock feel realistic */
export function simulateDelay(ms = 400): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export const mockJobsResponse: JobsListResponse = {
  items: [
    {
      id: '11111111-1111-1111-1111-111111111111',
      title: 'Junior Java Developer',
      description:
        'Entry-level backend Java developer role. You will work on Spring Boot microservices, write unit tests, and collaborate with senior engineers.',
      level: 'entry',
      language: 'en',
      is_active: true,
    },
    {
      id: '22222222-2222-2222-2222-222222222222',
      title: 'Sales Associate',
      description:
        'Customer-facing sales role. You will handle inbound leads, conduct product demos, and maintain CRM records for a SaaS B2B product.',
      level: 'entry',
      language: 'en',
      is_active: true,
    },
    {
      id: '33333333-3333-3333-3333-333333333333',
      title: 'Data Entry Operator',
      description:
        'Accuracy-focused data entry role. You will digitise physical records, validate data quality, and maintain spreadsheet dashboards.',
      level: 'entry',
      language: 'en',
      is_active: true,
    },
  ],
  total: 3,
  page: 1,
  per_page: 20,
};

export const mockCreateSessionResponse: CreateSessionResponse = {
  session_id: 'mock-sess-uuid-' + Math.random().toString(36).slice(2, 10),
  language: 'en',
};

// ---------------------------------------------------------------------------
// Sessions list mock — 3 sample sessions (newest first)
// ---------------------------------------------------------------------------

export const mockSessionsResponse: SessionListResponse = {
  items: [
    {
      session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      job_title: 'Junior Java Developer',
      language: 'en',
      status: 'completed',
      started_at: '2026-05-29T10:00:00Z',
      completed_at: '2026-05-29T10:12:34Z',
      duration_seconds: 754,
      created_at: '2026-05-29T09:58:00Z',
      scorecard_id: '00000000-0000-0000-0000-000000000001',
    },
    {
      session_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
      job_title: 'Sales Associate',
      language: 'hi',
      status: 'completed',
      started_at: '2026-05-27T14:05:00Z',
      completed_at: '2026-05-27T14:16:20Z',
      duration_seconds: 680,
      created_at: '2026-05-27T14:03:00Z',
      scorecard_id: '00000000-0000-0000-0000-000000000002',
    },
    {
      session_id: 'cccccccc-cccc-cccc-cccc-cccccccccccc',
      job_title: 'Data Entry Operator',
      language: 'te',
      status: 'abandoned',
      started_at: '2026-05-25T09:30:00Z',
      completed_at: null,
      duration_seconds: null,
      created_at: '2026-05-25T09:28:00Z',
      scorecard_id: null,
    },
  ],
  total: 3,
  page: 1,
  per_page: 20,
};

// ---------------------------------------------------------------------------
// Scorecards list mock — 2 sample scorecards (newest first)
// ---------------------------------------------------------------------------

export const mockScorecardsResponse: ScorecardListResponse = {
  items: [
    {
      scorecard_id: '00000000-0000-0000-0000-000000000001',
      session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      composite_score: 7.05,
      created_at: '2026-05-29T10:13:00Z',
      summary:
        'A solid entry-level candidate who meets tier expectations on most axes. ' +
        'Communication and problem-solving stand out as strengths.',
      job_title: 'Junior Java Developer',
    },
    {
      scorecard_id: '00000000-0000-0000-0000-000000000002',
      session_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
      composite_score: 6.5,
      created_at: '2026-05-27T14:17:00Z',
      summary:
        'Good communication skills demonstrated in Hindi. ' +
        'Technical product knowledge could be deeper for a sales role.',
      job_title: 'Sales Associate',
    },
  ],
  total: 2,
  page: 1,
  per_page: 20,
};

// ---------------------------------------------------------------------------
// Resume mocks — 2 versions (current + older)
// ---------------------------------------------------------------------------

export const mockResumesResponse: ResumeVersionItem[] = [
  {
    resume_id: 'rrrrrrrr-1111-1111-1111-rrrrrrrrrrrr',
    filename: 'priya_sharma_resume_v2.pdf',
    resume_s3_key:
      'resumes/11111111-1111-1111-1111-111111111111/rrrrrrrr-1111-1111-1111-rrrrrrrrrrrr.pdf',
    text_length: 2840,
    is_current: true,
    uploaded_at: '2026-05-28T08:30:00Z',
    created_at: '2026-05-28T08:30:00Z',
    download_url: null,
  },
  {
    resume_id: 'rrrrrrrr-2222-2222-2222-rrrrrrrrrrrr',
    filename: 'priya_sharma_resume_v1.pdf',
    resume_s3_key:
      'resumes/11111111-1111-1111-1111-111111111111/rrrrrrrr-2222-2222-2222-rrrrrrrrrrrr.pdf',
    text_length: 2210,
    is_current: false,
    uploaded_at: '2026-05-15T11:00:00Z',
    created_at: '2026-05-15T11:00:00Z',
    download_url: null,
  },
];

export const mockCurrentResume: ResumeCurrentResponse = {
  resume_id: 'rrrrrrrr-1111-1111-1111-rrrrrrrrrrrr',
  filename: 'priya_sharma_resume_v2.pdf',
  resume_s3_key:
    'resumes/11111111-1111-1111-1111-111111111111/rrrrrrrr-1111-1111-1111-rrrrrrrrrrrr.pdf',
  text_length: 2840,
  uploaded_at: '2026-05-28T08:30:00Z',
  created_at: '2026-05-28T08:30:00Z',
  download_url: null,
};
