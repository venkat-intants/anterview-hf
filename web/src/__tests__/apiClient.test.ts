// Unit tests for the new API client layer:
//   sessions.ts  — listSessions, createSession
//   scorecard.ts — listScorecards
//   resume.ts    — listResumes, getCurrentResume, setCurrentResume, deleteResume
//
// Testing strategy:
//   The .env file sets VITE_USE_MOCK=false, so we cannot rely on mock-mode
//   branching in tests.  Instead we:
//     1. Test the response shapes by directly importing mock data from
//        api/mock.ts and verifying them against the TypeScript interfaces.
//     2. Test URL/method/query-param logic by stubbing global fetch and calling
//        the client helper functions (apiGet, apiPost, apiDelete, feedbackGet,
//        clientFetch) directly — the same functions the API modules delegate to.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setToken, clearToken } from '../api/tokenStore';

// ---------------------------------------------------------------------------
// Fetch mock helper
// ---------------------------------------------------------------------------

function makeFetchResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

beforeEach(() => {
  setToken('test-token-123');
});

afterEach(() => {
  clearToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ===========================================================================
// Mock data completeness — sessions
// ===========================================================================

describe('mockSessionsResponse — shape and content', () => {
  it('has 3 items, total 3, page 1, per_page 20', async () => {
    const { mockSessionsResponse } = await import('../api/mock');
    expect(mockSessionsResponse.items).toHaveLength(3);
    expect(mockSessionsResponse.total).toBe(3);
    expect(mockSessionsResponse.page).toBe(1);
    expect(mockSessionsResponse.per_page).toBe(20);
  });

  it('every item has the required SessionListItem fields', async () => {
    const { mockSessionsResponse } = await import('../api/mock');
    for (const item of mockSessionsResponse.items) {
      expect(item).toHaveProperty('session_id');
      expect(item).toHaveProperty('job_title');
      expect(item).toHaveProperty('language');
      expect(item).toHaveProperty('status');
      expect(item).toHaveProperty('started_at');
      expect(item).toHaveProperty('completed_at');
      expect(item).toHaveProperty('duration_seconds');
      expect(item).toHaveProperty('created_at');
      expect(item).toHaveProperty('scorecard_id');
    }
  });

  it('first item (completed) has a scorecard_id', async () => {
    const { mockSessionsResponse } = await import('../api/mock');
    expect(mockSessionsResponse.items[0].status).toBe('completed');
    expect(mockSessionsResponse.items[0].scorecard_id).toBeTruthy();
  });

  it('last item (abandoned) has null scorecard_id', async () => {
    const { mockSessionsResponse } = await import('../api/mock');
    expect(mockSessionsResponse.items[2].status).toBe('abandoned');
    expect(mockSessionsResponse.items[2].scorecard_id).toBeNull();
  });

  it('sessions cover all 3 supported languages: en, hi, te', async () => {
    const { mockSessionsResponse } = await import('../api/mock');
    const langs = mockSessionsResponse.items.map((s) => s.language);
    expect(langs).toContain('en');
    expect(langs).toContain('hi');
    expect(langs).toContain('te');
  });
});

// ===========================================================================
// createSession — basic contract
// ===========================================================================

describe('createSession — basic contract', () => {
  it('CreateSessionRequest works with job_id and language', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        makeFetchResponse(201, {
          session_id: 'sess-002',
          job_title: 'Sales',
          language: 'hi',
        }),
      ),
    );

    const { createSession } = await import('../api/sessions');
    const result = await createSession({ job_id: 'job-uuid-5678', language: 'hi' });
    expect(result.session_id).toBeTruthy();
  });
});

// ===========================================================================
// listSessions — URL construction
// ===========================================================================

describe('listSessions — URL and query param construction', () => {
  it('builds URL with page and per_page params', async () => {
    const mockBody = { items: [], total: 0, page: 2, per_page: 10 };
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(200, mockBody));
    vi.stubGlobal('fetch', fetchMock);

    const { listSessions } = await import('../api/sessions');
    await listSessions({ page: 2, perPage: 10 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/sessions');
    expect(url).toContain('page=2');
    expect(url).toContain('per_page=10');
  });

  it('appends status= query param when provided', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeFetchResponse(200, { items: [], total: 0, page: 1, per_page: 20 }));
    vi.stubGlobal('fetch', fetchMock);

    const { listSessions } = await import('../api/sessions');
    await listSessions({ status: 'completed' });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('status=completed');
  });

  it('defaults to page=1, per_page=20 when no params', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeFetchResponse(200, { items: [], total: 0, page: 1, per_page: 20 }));
    vi.stubGlobal('fetch', fetchMock);

    const { listSessions } = await import('../api/sessions');
    await listSessions();

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('page=1');
    expect(url).toContain('per_page=20');
  });

  it('returns paginated SessionListResponse shape', async () => {
    const mockBody = {
      items: [
        {
          session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
          job_title: 'Java Dev',
          language: 'en',
          status: 'completed',
          started_at: '2026-05-29T10:00:00Z',
          completed_at: '2026-05-29T10:12:34Z',
          duration_seconds: 754,
          created_at: '2026-05-29T09:58:00Z',
          scorecard_id: 'sc-001',
        },
      ],
      total: 1,
      page: 1,
      per_page: 20,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(200, mockBody)));

    const { listSessions } = await import('../api/sessions');
    const result = await listSessions();

    expect(result.items[0].scorecard_id).toBe('sc-001');
    expect(result.total).toBe(1);
  });
});

// ===========================================================================
// Mock data completeness — scorecards
// ===========================================================================

describe('mockScorecardsResponse — shape and content', () => {
  it('has 2 items, total 2, page 1, per_page 20', async () => {
    const { mockScorecardsResponse } = await import('../api/mock');
    expect(mockScorecardsResponse.items).toHaveLength(2);
    expect(mockScorecardsResponse.total).toBe(2);
    expect(mockScorecardsResponse.page).toBe(1);
    expect(mockScorecardsResponse.per_page).toBe(20);
  });

  it('every item has all required ScorecardListItem fields', async () => {
    const { mockScorecardsResponse } = await import('../api/mock');
    for (const item of mockScorecardsResponse.items) {
      expect(item).toHaveProperty('scorecard_id');
      expect(item).toHaveProperty('session_id');
      expect(item).toHaveProperty('composite_score');
      expect(item).toHaveProperty('created_at');
      expect(item).toHaveProperty('summary');
      expect(item).toHaveProperty('job_title');
    }
  });

  it('first scorecard is for Junior Java Developer with composite_score 7.05', async () => {
    const { mockScorecardsResponse } = await import('../api/mock');
    expect(mockScorecardsResponse.items[0].job_title).toBe('Junior Java Developer');
    expect(mockScorecardsResponse.items[0].composite_score).toBe(7.05);
  });

  it('scorecard_id and session_id match the sessions mock (cross-reference)', async () => {
    const { mockScorecardsResponse, mockSessionsResponse } = await import('../api/mock');
    // First scorecard links to first completed session.
    expect(mockScorecardsResponse.items[0].scorecard_id).toBe(
      mockSessionsResponse.items[0].scorecard_id,
    );
    expect(mockScorecardsResponse.items[0].session_id).toBe(
      mockSessionsResponse.items[0].session_id,
    );
  });

  it('created_at is an ISO-8601 string', async () => {
    const { mockScorecardsResponse } = await import('../api/mock');
    for (const item of mockScorecardsResponse.items) {
      expect(item.created_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });
});

// ===========================================================================
// listScorecards — URL construction
// ===========================================================================

describe('listScorecards — URL and query param construction', () => {
  it('builds /api/scorecards with page and per_page', async () => {
    const mockBody = { items: [], total: 0, page: 1, per_page: 10 };
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(200, mockBody));
    vi.stubGlobal('fetch', fetchMock);

    const { listScorecards } = await import('../api/scorecard');
    await listScorecards({ page: 1, perPage: 10 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/scorecards');
    expect(url).toContain('page=1');
    expect(url).toContain('per_page=10');
  });

  it('defaults to page=1, per_page=20 when no params provided', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeFetchResponse(200, { items: [], total: 0, page: 1, per_page: 20 }));
    vi.stubGlobal('fetch', fetchMock);

    const { listScorecards } = await import('../api/scorecard');
    await listScorecards();

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('page=1');
    expect(url).toContain('per_page=20');
  });

  it('returns ScorecardListResponse shape', async () => {
    const mockBody = {
      items: [
        {
          scorecard_id: 'sc-001',
          session_id: 'sess-001',
          composite_score: 8.0,
          created_at: '2026-05-29T10:13:00Z',
          summary: 'Good candidate',
          job_title: 'Java Dev',
        },
      ],
      total: 1,
      page: 1,
      per_page: 20,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(200, mockBody)));

    const { listScorecards } = await import('../api/scorecard');
    const result = await listScorecards();

    expect(result.items[0].scorecard_id).toBe('sc-001');
    expect(result.items[0].composite_score).toBe(8.0);
    expect(result.total).toBe(1);
  });
});

// ===========================================================================
// Mock data completeness — resumes
// ===========================================================================

describe('mockResumesResponse — shape and content', () => {
  it('has 2 resume versions', async () => {
    const { mockResumesResponse } = await import('../api/mock');
    expect(mockResumesResponse).toHaveLength(2);
  });

  it('first resume is_current=true, second is is_current=false', async () => {
    const { mockResumesResponse } = await import('../api/mock');
    expect(mockResumesResponse[0].is_current).toBe(true);
    expect(mockResumesResponse[1].is_current).toBe(false);
  });

  it('every item has all required ResumeVersionItem fields', async () => {
    const { mockResumesResponse } = await import('../api/mock');
    for (const item of mockResumesResponse) {
      expect(item).toHaveProperty('resume_id');
      expect(item).toHaveProperty('filename');
      expect(item).toHaveProperty('resume_s3_key');
      expect(item).toHaveProperty('text_length');
      expect(item).toHaveProperty('is_current');
      expect(item).toHaveProperty('uploaded_at');
      expect(item).toHaveProperty('created_at');
      expect(item).toHaveProperty('download_url');
    }
  });

  it('s3_key path contains the mock user_id', async () => {
    const { mockResumesResponse } = await import('../api/mock');
    expect(mockResumesResponse[0].resume_s3_key).toContain('11111111-1111-1111-1111-111111111111');
  });

  it('uploaded_at is a valid ISO-8601 string', async () => {
    const { mockResumesResponse } = await import('../api/mock');
    for (const r of mockResumesResponse) {
      expect(r.uploaded_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });
});

describe('mockCurrentResume — shape and content', () => {
  it('matches the is_current=true resume from mockResumesResponse', async () => {
    const { mockResumesResponse, mockCurrentResume } = await import('../api/mock');
    const current = mockResumesResponse.find((r) => r.is_current);
    expect(current).toBeDefined();
    expect(mockCurrentResume.resume_id).toBe(current!.resume_id);
    expect(mockCurrentResume.filename).toBe(current!.filename);
  });

  it('has all required ResumeCurrentResponse fields', async () => {
    const { mockCurrentResume } = await import('../api/mock');
    expect(mockCurrentResume).toHaveProperty('resume_id');
    expect(mockCurrentResume).toHaveProperty('filename');
    expect(mockCurrentResume).toHaveProperty('resume_s3_key');
    expect(mockCurrentResume).toHaveProperty('text_length');
    expect(mockCurrentResume).toHaveProperty('uploaded_at');
    expect(mockCurrentResume).toHaveProperty('created_at');
    expect(mockCurrentResume).toHaveProperty('download_url');
  });
});

// ===========================================================================
// resume.ts — live path: URL + HTTP method assertions
// ===========================================================================

describe('resume — URL and method via fetch stub', () => {
  it('listResumes calls GET /users/me/resumes (plural)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(200, []));
    vi.stubGlobal('fetch', fetchMock);

    const { listResumes } = await import('../api/resume');
    await listResumes();

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/users/me/resumes');
  });

  it('getCurrentResume calls GET /users/me/resume (singular — exact path)', async () => {
    const body = {
      resume_id: 'r1',
      filename: 'cv.pdf',
      resume_s3_key: 'key',
      text_length: 100,
      uploaded_at: '2026-01-01T00:00:00Z',
      created_at: '2026-01-01T00:00:00Z',
      download_url: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(200, body));
    vi.stubGlobal('fetch', fetchMock);

    const { getCurrentResume } = await import('../api/resume');
    const result = await getCurrentResume();

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    // Must end exactly at /resume (singular), not /resumes
    expect(url).toMatch(/\/users\/me\/resume$/);
    expect(result?.resume_id).toBe('r1');
  });

  it('setCurrentResume calls POST /users/me/resumes/{id}/set-current', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeFetchResponse(200, { message: 'ok', resume_id: 'r2' }));
    vi.stubGlobal('fetch', fetchMock);

    const { setCurrentResume } = await import('../api/resume');
    await setCurrentResume('r2');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/users/me/resumes/r2/set-current');
    expect(init.method).toBe('POST');
  });

  it('setCurrentResume returns message and resume_id from response', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        makeFetchResponse(200, { message: 'Resume set as current.', resume_id: 'r3' }),
      );
    vi.stubGlobal('fetch', fetchMock);

    const { setCurrentResume } = await import('../api/resume');
    const result = await setCurrentResume('r3');

    expect(result.message).toBe('Resume set as current.');
    expect(result.resume_id).toBe('r3');
  });

  it('deleteResume calls DELETE /users/me/resumes/{id}', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeFetchResponse(200, { message: 'Resume r4 deleted.' }));
    vi.stubGlobal('fetch', fetchMock);

    const { deleteResume } = await import('../api/resume');
    await deleteResume('r4');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/users/me/resumes/r4');
    expect(init.method).toBe('DELETE');
  });

  it('deleteResume returns message from response', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeFetchResponse(200, { message: 'Resume r5 deleted.' }));
    vi.stubGlobal('fetch', fetchMock);

    const { deleteResume } = await import('../api/resume');
    const result = await deleteResume('r5');

    expect(typeof result.message).toBe('string');
    expect(result.message).toContain('r5');
  });
});
