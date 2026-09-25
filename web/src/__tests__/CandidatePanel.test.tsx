// Tests for the specialist assessment panel's citations (PH5-E1).
//
// `SignalAssessment.citations` has existed in the API since the panel
// shipped, but the component rendered none of them. That is the gap this
// closes: a per-signal source strip under each row, using the same shared
// renderer every other surface now uses.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { PanelVerdict } from '../api/agent';

const assessCandidate = vi.fn();
vi.mock('../api/agent', async () => {
  const actual = await vi.importActual<typeof import('../api/agent')>('../api/agent');
  return { ...actual, assessCandidate: (...a: unknown[]) => assessCandidate(...a) as unknown };
});

import CandidatePanel from '../components/agent/CandidatePanel';

const VERDICT: PanelVerdict = {
  applicant_id: 'ap-1',
  applicant_label: 'Asha Rao',
  signals: [
    {
      signal: 'resume',
      available: true,
      score_0_100: 78,
      confidence: 0.8,
      strengths: ['Five years of backend work'],
      concerns: [],
      evidence: [],
      citations: [
        {
          kind: 'applicant',
          id: 'ap-1',
          label: 'Asha Rao',
          href: '/hr/applicants/ap-1',
          ref: '',
          locator: null,
        },
      ],
    },
    {
      signal: 'exam',
      available: false,
      score_0_100: null,
      confidence: 0,
      strengths: [],
      concerns: [],
      evidence: [],
      citations: [],
    },
  ],
  contradictions: [],
  summary: 'Consistent across signals.',
  coverage_gaps: [],
  suggested_next_step: 'Proceed to interview.',
  confidence: 0.75,
  decision_authority: 'human_only',
  citations: [],
};

function renderPanel() {
  return render(
    <MemoryRouter>
      <CandidatePanel applicantId="ap-1" applicantName="Asha Rao" />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  assessCandidate.mockResolvedValue(VERDICT);
});

describe('CandidatePanel — citations', () => {
  it('renders a source chip under a signal that has one, linking to the record', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: /Run assessment/ }));

    const link = await screen.findByRole('link', { name: /Asha Rao/ });
    expect(link).toHaveAttribute('href', '/hr/applicants/ap-1');
  });

  it('renders nothing extra under a signal with no citations, such as one not taken', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: /Run assessment/ }));

    expect(await screen.findByText('Not taken yet')).toBeInTheDocument();
    // Only the one citation from the resume signal, none from the exam row.
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });
});
