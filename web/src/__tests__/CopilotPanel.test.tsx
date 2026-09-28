// Tests for the console copilot's citation wiring (PH5-E1) — the evidence
// banner, the inline [S…] marker, and the shared source strip, all driven by
// what askAgent actually returns rather than assumed from the reply text.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AgentChatResponse, Proposal } from '../api/agent';

const askAgent = vi.fn();
const getAgentStatus = vi.fn();
vi.mock('../api/agent', async () => {
  const actual = await vi.importActual<typeof import('../api/agent')>('../api/agent');
  return {
    ...actual,
    askAgent: (...a: unknown[]) => askAgent(...a) as unknown,
    getAgentStatus: (...a: unknown[]) => getAgentStatus(...a) as unknown,
  };
});

import CopilotPanel from '../components/agent/CopilotPanel';

const EVIDENCED_REPLY: AgentChatResponse = {
  agent: 'hr_copilot',
  reply: 'The notice period is 30 days[S1].',
  proposals: [],
  citations: [
    {
      kind: 'document',
      id: 'doc-1',
      label: 'Employee handbook',
      href: '/hr/documents/doc-1',
      ref: 'S1',
      locator: 'v3 · page 4',
    },
  ],
  tools_used: [{ name: 'search_company_documents', ok: true, duration_ms: 40 }],
  stop_reason: 'completed',
  evidence_used: true,
  citation_state: 'sourced',
};

const UNSOURCED_REPLY: AgentChatResponse = {
  agent: 'hr_copilot',
  reply: 'I think that is unlikely, but I did not check.',
  proposals: [],
  citations: [],
  tools_used: [],
  stop_reason: 'completed',
  evidence_used: false,
  citation_state: 'unread',
};

/** Read records, but the model wrote no marker tying a claim to any of them —
 * the PH5-E1 criteria 4/5 gap. `citations` is non-empty (records WERE read)
 * while the reply carries no `[S_]` marker at all. */
const UNATTRIBUTED_REPLY: AgentChatResponse = {
  agent: 'hr_copilot',
  reply: 'Most candidates for this role tend to have strong references.',
  proposals: [],
  citations: [
    {
      kind: 'document',
      id: 'doc-1',
      label: 'Employee handbook',
      href: '/hr/documents/doc-1',
      ref: 'S1',
      locator: 'v3 · page 4',
    },
  ],
  tools_used: [{ name: 'search_company_documents', ok: true, duration_ms: 40 }],
  stop_reason: 'completed',
  evidence_used: true,
  citation_state: 'unattributed',
};

const A_PROPOSAL: Proposal = {
  id: 'p-1',
  kind: 'interview_invite',
  title: 'Interview invite — Asha Rao',
  summary: 'Backend Engineer · EN · emailed to the candidate',
  commit: {
    method: 'POST',
    path: '/hr/interviews',
    body: { applicant_id: 'a-1', language: 'en' },
    label: 'Send invite',
  },
  rationale: 'Scored highest on the written exam.',
  citations: [],
  risk_note: 'Sends a real email to the candidate. This cannot be unsent.',
  created_at: '2026-09-02T00:00:00.000Z',
};

/** Same reply as UNATTRIBUTED_REPLY, but the turn also drafted a proposal —
 * this is what pins that the CARD, not just the reply above it, carries the
 * caution: a wiring bug that forgot to thread citationState down to
 * ProposalCard would pass every other test here and still show a confident
 * proposal beneath an unattributed answer. */
const UNATTRIBUTED_REPLY_WITH_PROPOSAL: AgentChatResponse = {
  ...UNATTRIBUTED_REPLY,
  proposals: [A_PROPOSAL],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CopilotPanel open onClose={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function ask(text: string): Promise<void> {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Message'), text);
  await user.click(screen.getByRole('button', { name: 'Ask' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getAgentStatus.mockResolvedValue({
    enabled: true,
    model_configured: true,
    console: 'hr_manager',
    surfaces: [],
    capabilities: ['read', 'draft'],
    note: '',
  });
});

describe('CopilotPanel — citations (PH5-E1)', () => {
  it('shows the "answered from your records" banner and a source chip when evidence was used', async () => {
    askAgent.mockResolvedValue(EVIDENCED_REPLY);
    renderPanel();
    await ask('what is the notice period?');

    expect(
      await screen.findByText(/Answered from your records/),
    ).toBeInTheDocument();
    const links = await screen.findAllByRole('link');
    expect(links.length).toBeGreaterThanOrEqual(1);
    for (const link of links) {
      expect(link).toHaveAttribute('href', '/hr/documents/doc-1');
    }
  });

  it('shows the "no records were read" banner when the answer is unsourced', async () => {
    askAgent.mockResolvedValue(UNSOURCED_REPLY);
    renderPanel();
    await ask('is that role easy to hire for?');

    expect(
      await screen.findByText(/No records were read for this answer/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('cautions rather than confirms when records were read but nothing is attributed', async () => {
    // The adversarial case PH5-E1 criteria 4/5 exist for: evidence_used is
    // true (a citation came back), but no [S_] marker survived — the server
    // says 'unattributed', not 'sourced', and the banner must say so plainly.
    askAgent.mockResolvedValue(UNATTRIBUTED_REPLY);
    renderPanel();
    await ask('what should I expect from candidates for this role?');

    expect(
      await screen.findByText(/Records were read for this answer, but no claim in it is tied/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Answered from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^No records were read/)).not.toBeInTheDocument();
    // The source strip still renders — the record really was read, only
    // nothing in the prose is tied to it.
    const links = await screen.findAllByRole('link');
    expect(links.length).toBeGreaterThanOrEqual(1);
  });

  it('carries the caution down to a proposal drafted in the same unattributed turn', async () => {
    // The wiring test: the turn's citation state must reach ProposalCard, not
    // stop at the reply's own banner.
    askAgent.mockResolvedValue(UNATTRIBUTED_REPLY_WITH_PROPOSAL);
    renderPanel();
    await ask('who should I invite next?');

    await screen.findByText('Interview invite — Asha Rao');
    const cautions = await screen.findAllByText(
      /Records were read for this answer, but no claim in it is tied/,
    );
    // One above the reply, one inside the proposal card.
    expect(cautions).toHaveLength(2);
  });

  it('does not show an evidence banner for the plain error message on a failed request', async () => {
    askAgent.mockRejectedValue(new Error('network down'));
    renderPanel();
    await ask('anything');

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.queryByText(/Answered from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No records were read/)).not.toBeInTheDocument();
  });
});
