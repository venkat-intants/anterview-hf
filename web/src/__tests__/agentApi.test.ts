// api/agent.ts: what actually goes over the wire for a copilot question and a
// proposal commit (audit CP-F1, CP-F2, CP-L4).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { HistoryTurn, Proposal } from '../api/agent';

const apiPost = vi.fn();
const apiRequest = vi.fn();
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    apiPost: (...a: unknown[]) => apiPost(...a) as unknown,
    apiRequest: (...a: unknown[]) => apiRequest(...a) as unknown,
    dataGatewayBase: () => '',
  };
});

import { AGENT_MESSAGE_MAX_CHARS, askAgent, commitProposal } from '../api/agent';

const ID = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41';

const INVITE: Proposal = {
  id: 'proposal-7',
  kind: 'interview_invite',
  title: 'Interview invite - Asha',
  summary: '',
  commit: {
    method: 'POST',
    path: '/hr/interviews',
    body: { applicant_id: ID, enrolment_id: ID, language: 'en' },
    label: 'Send invite',
  },
  rationale: '',
  citations: [],
  risk_note: null,
  created_at: new Date().toISOString(),
};

beforeEach(() => {
  vi.clearAllMocks();
  apiPost.mockResolvedValue({});
  apiRequest.mockResolvedValue({});
});

describe('askAgent', () => {
  it('trims every history turn to the server cap and keeps the last 12', async () => {
    const history: HistoryTurn[] = Array.from({ length: 15 }, (_, i) => ({
      role: i % 2 === 0 ? 'user' : 'assistant',
      text: `${i}:${'x'.repeat(3000)}`,
    }));
    await askAgent('next?', history);

    const body = apiPost.mock.calls[0][1] as { history: { text: string }[] };
    expect(body.history).toHaveLength(12);
    expect(body.history[0].text.startsWith('3:')).toBe(true);
    for (const turn of body.history) {
      expect(turn.text.length).toBeLessThanOrEqual(AGENT_MESSAGE_MAX_CHARS);
    }
  });
});

describe('commitProposal', () => {
  it('sends the proposal id as the Idempotency-Key', async () => {
    await commitProposal(INVITE);
    expect(apiRequest).toHaveBeenCalledWith('POST', '/hr/interviews', INVITE.commit.body, {
      headers: { 'Idempotency-Key': 'proposal-7' },
    });
  });

  it('refuses a proposal whose request is not the one its kind is for', async () => {
    const mislabelled: Proposal = {
      ...INVITE,
      commit: { ...INVITE.commit, method: 'PATCH', path: `/hr/applicants/${ID}` },
    };
    await expect(commitProposal(mislabelled)).rejects.toThrow(/does not match/);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
