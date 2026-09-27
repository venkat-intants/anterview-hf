// Tests for the console copilot's citation wiring (PH5-E1) — the evidence
// banner, the inline [S…] marker, and the shared source strip, all driven by
// what askAgent actually returns rather than assumed from the reply text.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AgentChatResponse } from '../api/agent';

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
};

const UNSOURCED_REPLY: AgentChatResponse = {
  agent: 'hr_copilot',
  reply: 'I think that is unlikely, but I did not check.',
  proposals: [],
  citations: [],
  tools_used: [],
  stop_reason: 'completed',
  evidence_used: false,
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

  it('does not show an evidence banner for the plain error message on a failed request', async () => {
    askAgent.mockRejectedValue(new Error('network down'));
    renderPanel();
    await ask('anything');

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.queryByText(/Answered from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No records were read/)).not.toBeInTheDocument();
  });
});
