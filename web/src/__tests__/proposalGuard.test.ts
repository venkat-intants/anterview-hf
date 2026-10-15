// A copilot proposal's method/path come from the server and are fired with the
// user's bearer token. On the production Space build the API base is '' so the
// path alone is the URL, and the WHATWG parser reads `/\evil.example/x` and
// `/<TAB>/evil.example/x` as another host. commitProposal must refuse those
// before anything is sent, while every path the backend really emits passes.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { assertSafeProposalRequest, ProposalRejectedError } from '../api/proposalGuard';

const ORIGIN = 'https://space.example';
const ID = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41';

const HOSTILE = [
  '/\\evil.example/x',
  '/\t/evil.example/x',
  '/\n/evil.example/x',
  '/\r/evil.example/x',
  '//evil.example/x',
  'https://evil.example/x',
  '/hr/x/../../auth/logout',
  '/hr/..',
  '/hr/./x',
  '/hr//x',
  '/hr/%2e%2e/auth/logout',
  '/hr/x?next=//evil.example',
  '/hr/x#frag',
  '/hr/x\\..\\auth',
  '/auth/logout', // not under /hr/
  '/admin/companies',
  'hr/applicants',
  '',
];

// Every path shape the seven... six real CommitSpec constructors produce
// (services/data_gateway/app/agents/tools.py and workflow_tools.py).
const REAL: Array<[string, string]> = [
  ['POST', '/hr/interviews'],
  ['PATCH', `/hr/applicants/${ID}`],
  ['POST', `/hr/requisitions/${ID}/workflows/apply`],
  ['POST', `/hr/workflows/${ID}/rounds`],
  ['PUT', `/hr/workflows/${ID}/rounds/${ID}/criteria`],
  ['PATCH', `/hr/workflows/${ID}`],
];

describe('assertSafeProposalRequest', () => {
  describe.each([
    ['empty base (production Space build)', ''],
    ['absolute base', 'https://space.example'],
    ['absolute base with a path prefix', 'https://space.example/api'],
  ])('%s', (_label, base) => {
    it.each(HOSTILE)('refuses %j', (bad) => {
      expect(() => assertSafeProposalRequest('POST', bad, base, ORIGIN)).toThrow(
        ProposalRejectedError,
      );
    });

    it.each(REAL)('accepts %s %s', (method, path) => {
      expect(assertSafeProposalRequest(method, path, base, ORIGIN)).toEqual({ method, path });
    });
  });

  it.each(['DELETE', 'GET', 'post', 'TRACE', ''])('refuses method %j', (m) => {
    expect(() => assertSafeProposalRequest(m, '/hr/interviews', '', ORIGIN)).toThrow(
      ProposalRejectedError,
    );
  });

  it('honours a deployment API base on another host (the path is anchored to the base)', () => {
    // A base on another host is the deployment's choice, not the proposal's:
    // the resolution check is anchored to the base, so a safe path still passes.
    expect(
      assertSafeProposalRequest('POST', '/hr/interviews', 'https://api.example', ORIGIN).path,
    ).toBe('/hr/interviews');
  });

  it('confirms the premise: the unsafe forms really do leave the origin', () => {
    // If a runtime ever stops doing this the guard is merely redundant, but the
    // test documents WHY the plain `${base}${path}` concatenation was unsafe.
    expect(new URL('/\\evil.example/x', ORIGIN).host).toBe('evil.example');
    expect(new URL('/\t/evil.example/x', ORIGIN).host).toBe('evil.example');
  });
});

describe('commitProposal', () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  const proposal = (method: string, path: string) =>
    ({
      id: 'p1',
      kind: 'shortlist',
      title: 't',
      summary: 's',
      rationale: '',
      risk_note: null,
      reaches_candidate: false,
      citations: [],
      commit: { method, path, body: { a: 1 }, label: 'Go' },
    }) as never;

  it.each(['', 'http://localhost:8002'])(
    'sends nothing and rejects a hostile path (base %j)',
    async (base) => {
      vi.stubEnv('VITE_API_BASE_URL', base);
      const fetchMock = vi.fn();
      vi.stubGlobal('fetch', fetchMock);
      const { commitProposal } = await import('../api/agent');

      for (const bad of ['/\\evil.example/x', '/\t/evil.example/x', '/hr/x/../../auth/logout']) {
        await expect(commitProposal(proposal('POST', bad))).rejects.toThrow(/not sent/);
      }
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it.each(['', 'http://localhost:8002'])(
    'still sends a genuine proposal, to the API origin (base %j)',
    async (base) => {
      vi.stubEnv('VITE_API_BASE_URL', base);
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true }),
        headers: new Headers(),
      });
      vi.stubGlobal('fetch', fetchMock);
      const { commitProposal } = await import('../api/agent');

      await commitProposal(proposal('PATCH', `/hr/applicants/${ID}`));

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(`${base}/hr/applicants/${ID}`);
      expect(init.method).toBe('PATCH');
    },
  );
});
