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
const commitProposal = vi.fn();
vi.mock('../api/agent', async () => {
  const actual = await vi.importActual<typeof import('../api/agent')>('../api/agent');
  return {
    ...actual,
    askAgent: (...a: unknown[]) => askAgent(...a) as unknown,
    getAgentStatus: (...a: unknown[]) => getAgentStatus(...a) as unknown,
    commitProposal: (...a: unknown[]) => commitProposal(...a) as unknown,
  };
});
vi.mock('../lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

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
  title: 'Interview invite - Asha Rao',
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

describe('CopilotPanel - citations (PH5-E1)', () => {
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

    await screen.findByText('Interview invite - Asha Rao');
    // The full sentence once, above the reply; the card carries the compact
    // form of the same state rather than repeating it word for word.
    expect(
      screen.getAllByText(/Records were read for this answer, but no claim in it is tied/),
    ).toHaveLength(1);
    expect(screen.getByText(/Drafted after reading records, but not tied/)).toBeInTheDocument();
  });

  it('does not show an evidence banner for the plain error message on a failed request', async () => {
    askAgent.mockRejectedValue(new TypeError('Failed to fetch'));
    renderPanel();
    await ask('anything');

    // Once in the conversation and once in the screen-reader announcement.
    expect(await screen.findAllByText(/Could not reach the server/)).toHaveLength(2);
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Answered from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No records were read/)).not.toBeInTheDocument();
  });
});

const COMPLETED_REPLY_WITH_PROPOSAL: AgentChatResponse = {
  ...EVIDENCED_REPLY,
  reply: 'Asha is ready for an interview.',
  proposals: [A_PROPOSAL],
};

function renderToggleablePanel(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (open: boolean) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CopilotPanel open={open} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const utils = render(view(true));
  return { ...utils, setOpen: (open: boolean) => utils.rerender(view(open)), onClose };
}

describe('CopilotPanel - conversation state', () => {
  it('keeps an approved proposal approved after the panel is closed and reopened', async () => {
    // Audit CP-F1: the card used to remount as "Needs your approval" with a
    // live "Send invite", and a second click revoked the candidate's link.
    askAgent.mockResolvedValue(COMPLETED_REPLY_WITH_PROPOSAL);
    commitProposal.mockResolvedValue({});
    const user = userEvent.setup();
    const { setOpen } = renderToggleablePanel();
    await ask('who should I invite?');
    await user.click(await screen.findByRole('button', { name: 'Send invite' }));
    await screen.findByText('Completed');

    setOpen(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    setOpen(true);

    expect(await screen.findByText('Completed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send invite' })).not.toBeInTheDocument();
    expect(commitProposal).toHaveBeenCalledTimes(1);
  });

  it('tells the model what was already approved, and drops a re-draft of it', async () => {
    // Audit CP-F4: without this a follow-up re-drafted the same invite as a
    // fresh card with a live button.
    askAgent.mockResolvedValue(COMPLETED_REPLY_WITH_PROPOSAL);
    commitProposal.mockResolvedValue({});
    const user = userEvent.setup();
    renderPanel();
    await ask('who should I invite?');
    await user.click(await screen.findByRole('button', { name: 'Send invite' }));
    await screen.findByText('Completed');

    askAgent.mockResolvedValue({
      ...COMPLETED_REPLY_WITH_PROPOSAL,
      reply: 'Asha again.',
      proposals: [{ ...A_PROPOSAL, id: 'p-2' }],
    });
    await ask('who else?');

    const history = askAgent.mock.calls[1][1] as { role: string; text: string }[];
    expect(history[1].text).toContain(
      '[Already approved by the user in this conversation: Interview invite - Asha Rao]',
    );
    expect(await screen.findByText(/Already approved earlier in this chat/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send invite' })).not.toBeInTheDocument();
  });

  it('keeps a failed exchange out of the history it replays', async () => {
    // Audit CP-F2: a message the server refused once was resent, and refused,
    // with every later message.
    askAgent.mockRejectedValueOnce(new Error('Message must be 2000 characters or fewer.'));
    askAgent.mockResolvedValueOnce(UNSOURCED_REPLY);
    renderPanel();
    await ask('first try');
    expect(await screen.findByText(/It will not be included as context/)).toBeInTheDocument();

    await ask('second try');
    await screen.findByText(UNSOURCED_REPLY.reply);
    expect(askAgent.mock.calls[1][1]).toEqual([]);
  });

  it('caps a long assistant reply in the history it replays', async () => {
    askAgent.mockResolvedValueOnce({ ...UNSOURCED_REPLY, reply: 'x'.repeat(5000) });
    askAgent.mockResolvedValueOnce(UNSOURCED_REPLY);
    renderPanel();
    await ask('summarise everything');
    await screen.findByText(/No records were read/);
    await ask('and then?');

    const history = askAgent.mock.calls[1][1] as { role: string; text: string }[];
    expect(history[1].text).toHaveLength(2000);
  });

  it('stops the composer at the server cap', () => {
    renderPanel();
    expect(screen.getByLabelText('Message')).toHaveAttribute('maxlength', '2000');
  });

  it('starts a new chat that forgets the old turns', async () => {
    askAgent.mockResolvedValue(UNSOURCED_REPLY);
    const user = userEvent.setup();
    renderPanel();
    await ask('hello');
    await screen.findByText(UNSOURCED_REPLY.reply);

    await user.click(screen.getByRole('button', { name: 'New chat' }));
    expect(screen.queryByText(UNSOURCED_REPLY.reply)).not.toBeInTheDocument();
    expect(screen.getByText('How can I help?')).toBeInTheDocument();

    await ask('again');
    expect(askAgent.mock.calls[1][1]).toEqual([]);
  });

  it('does not repeat the explanation already in an llm_error reply', async () => {
    askAgent.mockResolvedValue({
      ...UNSOURCED_REPLY,
      reply: 'The language model did not answer. It may be busy, so try again in a minute.',
      stop_reason: 'llm_error',
    });
    renderPanel();
    await ask('anything');
    await screen.findByText(/did not answer/);
    expect(screen.queryByText(/unreachable/)).not.toBeInTheDocument();
  });
});

describe('CopilotPanel - dialog behaviour', () => {
  it('announces an answer to screen readers when it arrives', async () => {
    askAgent.mockResolvedValue(UNSOURCED_REPLY);
    renderPanel();
    await ask('hello');
    expect(await screen.findByText('Assistant replied.')).toBeInTheDocument();
    expect(screen.getByText('Assistant replied.')).toHaveAttribute('role', 'status');
  });

  it('closes on Escape even when focus has left the dialog', async () => {
    const { onClose } = renderToggleablePanel();
    (document.activeElement as HTMLElement | null)?.blur();
    await userEvent.setup().keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  it('gives focus back to the composer once an answer lands', async () => {
    askAgent.mockResolvedValue(UNSOURCED_REPLY);
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: /Who should I interview next/ }));
    await screen.findByText(UNSOURCED_REPLY.reply);
    expect(screen.getByLabelText('Message')).toHaveFocus();
  });

  it('locks the page scroll while open and restores it on close', () => {
    document.body.style.overflow = 'auto';
    const { setOpen } = renderToggleablePanel();
    expect(document.body.style.overflow).toBe('hidden');
    setOpen(false);
    expect(document.body.style.overflow).toBe('auto');
  });
});
