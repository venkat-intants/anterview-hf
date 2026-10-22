// The assistant launcher: its disclosure state, focus return on close, and
// closing the panel when a source chip navigates (audit CP-F7, CP-F10).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
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

import CopilotLauncher from '../components/agent/CopilotLauncher';

const REPLY_WITH_SOURCE: AgentChatResponse = {
  agent: 'hr_copilot',
  reply: 'Asha scored highest.',
  proposals: [],
  citations: [
    {
      kind: 'applicant',
      id: 'a-1',
      label: 'Asha Rao',
      href: '/hr/applicants/a-1',
      ref: 'S1',
      locator: null,
    },
  ],
  tools_used: [],
  stop_reason: 'completed',
  evidence_used: true,
  citation_state: 'unattributed',
};

function Where(): JSX.Element {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function renderLauncher() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/hr']}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <Where />
                <CopilotLauncher />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
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

describe('CopilotLauncher', () => {
  it('says whether the panel is open and returns focus to itself on close', async () => {
    const user = userEvent.setup();
    renderLauncher();
    const launcher = await screen.findByRole('button', { name: 'Open assistant' });
    expect(launcher).toHaveAttribute('aria-expanded', 'false');
    expect(launcher).toHaveAttribute('aria-haspopup', 'dialog');

    await user.click(launcher);
    expect(screen.getByRole('dialog', { name: 'Assistant' })).toBeInTheDocument();
    expect(launcher).toHaveAttribute('aria-expanded', 'true');

    await user.click(screen.getByRole('button', { name: 'Close assistant' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });

  it('closes the panel when a source chip navigates, so the user sees the record', async () => {
    askAgent.mockResolvedValue(REPLY_WITH_SOURCE);
    const user = userEvent.setup();
    renderLauncher();
    await user.click(await screen.findByRole('button', { name: 'Open assistant' }));
    await user.type(screen.getByLabelText('Message'), 'who is best?');
    await user.click(screen.getByRole('button', { name: 'Ask' }));

    const chips = await screen.findAllByRole('link', { name: /Asha Rao/ });
    await user.click(chips[chips.length - 1]);

    expect(screen.getByTestId('where')).toHaveTextContent('/hr/applicants/a-1');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // The conversation survived the close.
    await user.click(screen.getByRole('button', { name: 'Open assistant' }));
    expect(screen.getByText('Asha scored highest.')).toBeInTheDocument();
  });
});
