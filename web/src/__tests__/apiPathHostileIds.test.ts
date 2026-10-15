// Route params reach API paths. React Router decodes %2F, so a page can be
// handed `../../auth/me?` as an "id". Every API module that interpolates an id
// into a path must refuse it BEFORE a request is built (pathId / pathSegment /
// pathInt), and must still build the right URL from a real id.
//
// One representative function per module, plus the helper's own rules. The
// per-module sweep is what catches a NEW module that forgets the helper only if
// it is added here, so the second block greps the source for any template
// literal path that interpolates a bare id.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathId, pathInt, pathSegment } from '../api/pathId';

const ID = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41';
const HOSTILE = ['../../auth/me', '..%2F..%2Fauth%2Fme%3F', '..', '.', '', `${ID}/../x`, 'a/b'];

type Call = (id: string) => unknown;

function okResponse(): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(''),
    headers: new Headers(),
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn().mockImplementation(() => Promise.resolve(okResponse()));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

async function loadCases(): Promise<Array<[string, Call, string]>> {
  const admin = await import('../api/admin');
  const agent = await import('../api/agent');
  const applicants = await import('../api/applicants');
  const applications = await import('../api/applications');
  const checkins = await import('../api/checkins');
  const companyBoard = await import('../api/companyBoard');
  const exams = await import('../api/exams');
  const hr = await import('../api/hr');
  const integrity = await import('../api/integrity');
  const interviewInvites = await import('../api/interviewInvites');
  const notifications = await import('../api/notifications');
  const pipeline = await import('../api/pipeline');
  const profile = await import('../api/profile');
  const publicApply = await import('../api/publicApply');
  const questions = await import('../api/questions');
  const requisitions = await import('../api/requisitions');
  const resume = await import('../api/resume');
  const rooms = await import('../api/rooms');
  const scorecard = await import('../api/scorecard');
  const workflows = await import('../api/workflows');
  return [
    ['admin.getInterviewDetail', (i) => admin.getInterviewDetail(i), `/admin/interviews/${ID}`],
    ['agent.assessCandidate', (i) => agent.assessCandidate(i), `/agent/panel/${ID}`],
    ['applicants.getApplicant', (i) => applicants.getApplicant(i), `/hr/applicants/${ID}`],
    [
      'applications.getMyApplication',
      (i) => applications.getMyApplication(i),
      `/users/me/applications/${ID}`,
    ],
    [
      'checkins.getEnrolmentCheckins',
      (i) => checkins.getEnrolmentCheckins(i),
      `/hr/enrolments/${ID}/checkins`,
    ],
    [
      'companyBoard.getCompanyRequisitionDashboard',
      (i) => companyBoard.getCompanyRequisitionDashboard(i),
      `/admin/requisitions/${ID}/dashboard`,
    ],
    ['exams.getExam', (i) => exams.getExam(i), `/hr/exams/${ID}`],
    ['exams.listAttempts', (i) => exams.listAttempts(i), `/hr/exams/${ID}/attempts`],
    ['hr.deleteCompany', (i) => hr.deleteCompany(i), `/admin/companies/${ID}`],
    ['hr.setFeatureFlag (slug)', (i) => hr.setFeatureFlag(i, true), '/admin/feature-flags/'],
    ['integrity.getSessionIntegrity', (i) => integrity.getSessionIntegrity(i), ''],
    [
      'interviewInvites.revokeInvite',
      (i) => interviewInvites.revokeInvite(i),
      `/hr/interviews/${ID}/revoke`,
    ],
    [
      'notifications.markNotificationRead',
      (i) => notifications.markNotificationRead(i),
      `/notifications/${ID}/read`,
    ],
    [
      'pipeline.setApplicantDecision',
      (i) => pipeline.setApplicantDecision(i, 'hired', 'x'),
      `/hr/applicants/${ID}/decision`,
    ],
    ['profile.getUserProfile', (i) => profile.getUserProfile(i), `/users/${ID}/profile`],
    ['publicApply.getPosting', (i) => publicApply.getPosting(i), `/apply/${ID}`],
    ['questions.updateQuestion', (i) => questions.updateQuestion(i, {}), `/hr/questions/${ID}`],
    [
      'requisitions.getRequisition',
      (i) => requisitions.getRequisition(i),
      `/hr/requisitions/${ID}`,
    ],
    ['resume.deleteResume', (i) => resume.deleteResume(i), `/users/me/resumes/${ID}`],
    ['rooms.getRoomToken', (i) => rooms.getRoomToken(i), `/api/rooms/${ID}/token`],
    ['scorecard.getScorecard', (i) => scorecard.getScorecard(i), `/api/scorecards/${ID}`],
    ['workflows.getWorkflow', (i) => workflows.getWorkflow(i), `/hr/workflows/${ID}`],
  ];
}

describe('every API module refuses a hostile id before sending anything', () => {
  it('covers the representative function in each module', async () => {
    const cases = await loadCases();
    for (const [name, call, goodPath] of cases) {
      for (const bad of HOSTILE) {
        fetchMock.mockClear();
        // Slug-style params (feature-flag key) accept most strings but never a
        // dot segment or an unencoded slash; UUID params refuse all of them.
        const isSlug = name.includes('(slug)');
        const dotOnly = bad === '..' || bad === '.' || bad === '';
        if (isSlug && !dotOnly) {
          await Promise.resolve(call(bad)).catch(() => undefined);
          for (const [url] of fetchMock.mock.calls as Array<[string]>) {
            expect(url, name).not.toMatch(/feature-flags\/[^?]*\//);
            expect(url, name).not.toMatch(/\.\.\//);
          }
          continue;
        }
        let threw = false;
        try {
          await call(bad);
        } catch {
          threw = true;
        }
        // integrity swallows errors by design (best-effort) but must still not send.
        expect(
          threw || name.startsWith('integrity.'),
          `${name} accepted ${JSON.stringify(bad)}`,
        ).toBe(true);
        expect(
          fetchMock,
          `${name} sent a request for ${JSON.stringify(bad)}`,
        ).not.toHaveBeenCalled();
      }
      // And a real id still builds the right URL.
      if (goodPath && !name.includes('(slug)') && !name.startsWith('integrity.')) {
        fetchMock.mockClear();
        await Promise.resolve(call(ID)).catch(() => undefined);
        expect(fetchMock, name).toHaveBeenCalled();
        const [url] = fetchMock.mock.calls[0] as [string];
        expect(url, name).toContain(goodPath);
      }
    }
  });
});

describe('pathId / pathSegment / pathInt', () => {
  it('pathId passes a UUID and refuses everything else', () => {
    expect(pathId(ID)).toBe(ID);
    for (const bad of HOSTILE) expect(() => pathId(bad)).toThrow(/valid record/);
  });

  it('pathSegment percent-encodes a slug and refuses dot segments', () => {
    expect(pathSegment('auto-reject')).toBe('auto-reject');
    expect(pathSegment('a/b')).toBe('a%2Fb');
    expect(pathSegment('x?y#z')).toBe('x%3Fy%23z');
    // A literal '%2e%2e' VALUE is encoded again, so it stays a literal segment.
    expect(pathSegment('%2e%2e')).toBe('%252e%252e');
    for (const bad of ['..', '.', '', '...']) {
      expect(() => pathSegment(bad), JSON.stringify(bad)).toThrow(/valid record/);
    }
  });

  it('pathInt accepts a non-negative integer only', () => {
    expect(pathInt(42)).toBe('42');
    for (const bad of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(() => pathInt(bad)).toThrow(/valid record/);
    }
  });
});

describe('no API module interpolates a bare id into a path', () => {
  it('every `/${...}` placeholder in web/src/api is wrapped by a path helper', () => {
    const dir = join(__dirname, '..', 'api');
    const offenders: string[] = [];
    for (const f of readdirSync(dir)) {
      if (!f.endsWith('.ts') || ['mock.ts', 'proposalGuard.ts', 'pathId.ts'].includes(f)) continue;
      readFileSync(join(dir, f), 'utf8')
        .split('\n')
        .forEach((line, n) => {
          const t = line.trim();
          if (t.startsWith('//') || t.startsWith('*')) return;
          for (const m of line.matchAll(/\/\$\{([^}]*)\}/g)) {
            const inner = m[1] ?? '';
            if (!/^(pathId|pathSegment|pathInt)\(/.test(inner))
              offenders.push(`${f}:${n + 1} ${m[0]}`);
          }
        });
    }
    expect(offenders).toEqual([]);
  });
});
