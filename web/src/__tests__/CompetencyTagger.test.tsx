// CompetencyTagger — PH4-D1. Up to 8 competencies, drawn from the shared
// catalogue or typed and slugified the same way the server validates an id.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const listBankCompetencies = vi.fn();
vi.mock('../api/questionBanks', () => ({
  listBankCompetencies: (...a: unknown[]) => listBankCompetencies(...a) as unknown,
}));

import { CompetencyTagger } from '../components/bank/CompetencyTagger';
import { slugifyCompetencyId } from '../lib/competencyId';

function renderTagger(value: { id: string; name: string }[], onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CompetencyTagger value={value} onChange={onChange} />
    </QueryClientProvider>,
  );
  return onChange;
}

beforeEach(() => {
  vi.clearAllMocks();
  listBankCompetencies.mockResolvedValue([{ id: 'sql', name: 'SQL' }]);
});

describe('slugifyCompetencyId', () => {
  it('lowercases, replaces separators, and trims stray underscores', () => {
    expect(slugifyCompetencyId('System Design')).toBe('system_design');
    expect(slugifyCompetencyId('  REST APIs!! ')).toBe('rest_apis');
  });
});

describe('CompetencyTagger', () => {
  it('adds a typed competency, slugified, on Add', async () => {
    const user = userEvent.setup();
    const onChange = renderTagger([]);
    await user.type(screen.getByLabelText(/Competencies/), 'System Design');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    expect(onChange).toHaveBeenCalledWith([{ id: 'system_design', name: 'System Design' }]);
  });

  it('adds a suggestion from the shared catalogue', async () => {
    const user = userEvent.setup();
    const onChange = renderTagger([]);
    await user.type(screen.getByLabelText(/Competencies/), 'sq');
    await user.click(await screen.findByRole('button', { name: 'SQL' }));
    expect(onChange).toHaveBeenCalledWith([{ id: 'sql', name: 'SQL' }]);
  });

  it('removes a selected competency', async () => {
    const user = userEvent.setup();
    const onChange = renderTagger([{ id: 'sql', name: 'SQL' }]);
    await user.click(screen.getByLabelText('Remove competency SQL'));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('hides the add control once 8 are selected', () => {
    const eight = Array.from({ length: 8 }, (_, i) => ({ id: `c${i}`, name: `Comp ${i}` }));
    renderTagger(eight);
    expect(screen.queryByLabelText(/Competencies/)).not.toBeInTheDocument();
  });
});
