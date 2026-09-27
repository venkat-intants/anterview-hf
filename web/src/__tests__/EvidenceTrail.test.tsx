// EvidenceTrail (/hr/enrolments/:enrolmentId/evidence) — PH5-E5. A list, not
// a node-link diagram: stage grouping, Human/AI/Candidate/System badges,
// hidden-content reasons, the decision picker, the "recorded after" section,
// and the ai_involvement line.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  DecisionTrace,
  EvidenceGraph,
  EvidenceNode,
  TraceEvidenceItem,
} from '../api/evidence';
import { ApiError } from '../api/client';

const getEvidenceGraph = vi.fn();
const getDecisionTrace = vi.fn();
vi.mock('../api/evidence', async () => {
  const actual = await vi.importActual<typeof import('../api/evidence')>('../api/evidence');
  return {
    ...actual,
    getEvidenceGraph: (...a: unknown[]) => getEvidenceGraph(...a) as unknown,
    getDecisionTrace: (...a: unknown[]) => getDecisionTrace(...a) as unknown,
  };
});

import EvidenceTrail from '../pages/hr/EvidenceTrail';

function node(over: Partial<EvidenceNode> = {}): EvidenceNode {
  return {
    id: 'application:en-1',
    kind: 'application',
    stage: {
      name: 'application',
      round_id: null,
      round_title: null,
      round_kind: null,
      workflow_version: null,
    },
    source: { table: 'enrolments', id: 'en-1' },
    provenance: {
      produced_by: 'candidate',
      actor: null,
      method: 'submitted an application',
      attribution: 'direct',
    },
    occurred_at: '2026-06-01T00:00:00.000Z',
    recorded_at: '2026-06-01T00:00:00.000Z',
    lifecycle: 'live',
    content: { source: 'referral', status_now: 'hired' },
    content_hidden_reason: null,
    href: '/hr/applicants/ap-1?enrolment=en-1#history',
    ...over,
  };
}

function graphFixture(over: Partial<EvidenceGraph> = {}): EvidenceGraph {
  return {
    schema_version: 1,
    generated_at: '2026-09-23T00:00:00.000Z',
    scope: { company_id: 'co-1', enrolment_id: 'en-1', applicant_id: 'ap-1' },
    candidate: { name: 'Asha Rao', erased: false },
    requisition: { id: 'req-1', title: 'Backend Engineer' },
    workflow: { id: 'wf-1', version: 2 },
    nodes: [],
    edges: [],
    decisions: [{ id: 1234, outcome: 'hired', decided_at: '2026-08-15T10:00:00.000Z' }],
    omitted: [],
    ...over,
  };
}

function evidenceItem(over: Partial<TraceEvidenceItem> = {}): TraceEvidenceItem {
  return { node: node(), path: [], changed_after_decision: null, ...over };
}

function traceFixture(over: Partial<DecisionTrace> = {}): DecisionTrace {
  return {
    schema_version: 1,
    decision: {
      id: 1234,
      enrolment_id: 'en-1',
      outcome: 'hired',
      reversal: false,
      decided_at: '2026-08-15T10:00:00.000Z',
      decided_by: { user_id: 'u-1', name: 'Priya Menon', role: 'hr_manager' },
      automated: false,
      reason_code: 'skills_fit',
      reason_label: 'Skills / competency fit',
      reason: 'Strong system design round.',
    },
    other_decisions: [],
    evidence: [
      evidenceItem({
        node: node({
          id: 'screening_ats:en-1',
          kind: 'screening_ats',
          stage: {
            name: 'screening',
            round_id: null,
            round_title: null,
            round_kind: null,
            workflow_version: null,
          },
          provenance: {
            produced_by: 'ai',
            actor: null,
            method: 'automated resume screening against the role',
            attribution: 'direct',
          },
          content: { ats_overall: 82, ats_recommendation: 'shortlist' },
          href: '/hr/applicants/ap-1?enrolment=en-1#screening',
        }),
      }),
      evidenceItem({
        node: node({
          id: 'human_scorecard:sc-1',
          kind: 'human_scorecard',
          stage: {
            name: 'interview',
            round_id: 'round-1',
            round_title: 'Panel',
            round_kind: 'human_review',
            workflow_version: 2,
          },
          provenance: {
            produced_by: 'human',
            actor: { user_id: 'u-2', name: 'Ravi Shah', role: 'interviewer' },
            method: 'scorecard against the round’s frozen criteria',
            attribution: 'direct',
          },
          content: { interviewer: 'Ravi Shah', summary: 'Strong communicator.' },
          href: '/hr/applicants/ap-1?enrolment=en-1#human-interview',
        }),
      }),
    ],
    after_decision: [],
    edges: [],
    by_stage: { screening: 1, interview: 1 },
    ai_involvement: { ai_produced_evidence: 1, decided_by: 'human' },
    omitted: [
      { kind: 'interviewer_notes', reason: 'Private to the interviewer; never shown to HR.' },
    ],
    ...over,
  };
}

function renderPage(initialPath = '/hr/enrolments/en-1/evidence') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/hr/enrolments/:enrolmentId/evidence" element={<EvidenceTrail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getEvidenceGraph.mockResolvedValue(graphFixture());
  getDecisionTrace.mockResolvedValue(traceFixture());
});

describe('EvidenceTrail — wording', () => {
  it('never says "based on" — states only what existed, and that it cannot show what was read', async () => {
    renderPage();
    expect(
      await screen.findByText(
        'This shows what existed when this was decided. It cannot show what the decider actually read.',
      ),
    ).toBeInTheDocument();
    expect(await screen.findByText('Available when this was decided')).toBeInTheDocument();
    expect(screen.queryByText(/what the decision was based on/i)).not.toBeInTheDocument();
  });
});

describe('EvidenceTrail — stage grouping and badges', () => {
  it('groups evidence by stage and shows Human/AI provenance badges', async () => {
    renderPage();

    const screeningHeading = await screen.findByText('Screening');
    const interviewHeading = await screen.findByText('Interview');
    expect(screeningHeading).toBeInTheDocument();
    expect(interviewHeading).toBeInTheDocument();

    expect(screen.getByText('AI')).toBeInTheDocument();
    expect(screen.getByText('Human')).toBeInTheDocument();
    expect(screen.getByText('Resume screening')).toBeInTheDocument();
    expect(screen.getByText('Interviewer scorecard')).toBeInTheDocument();
  });

  it('gives each evidence row an Open link to its source screen', async () => {
    renderPage();
    await screen.findByText('Resume screening');
    const links = screen.getAllByRole('link', { name: 'Open' });
    expect(links.length).toBeGreaterThanOrEqual(2);
    expect(links[0].getAttribute('href')).toContain('#screening');
  });
});

describe('EvidenceTrail — hidden content', () => {
  it('shows the hidden-content reason instead of an empty box', async () => {
    getDecisionTrace.mockResolvedValue(
      traceFixture({
        evidence: [
          evidenceItem({
            node: node({
              id: 'human_scorecard:sc-2',
              kind: 'human_scorecard',
              stage: {
                name: 'interview',
                round_id: 'round-1',
                round_title: 'Panel',
                round_kind: 'human_review',
                workflow_version: 2,
              },
              content: null,
              content_hidden_reason: 'redacted on erasure',
            }),
          }),
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText('redacted on erasure')).toBeInTheDocument();
  });
});

describe('EvidenceTrail — decision picker', () => {
  it('shows a picker for more than one decision, and switches the trace on selection', async () => {
    getEvidenceGraph.mockResolvedValue(
      graphFixture({
        decisions: [
          { id: 1234, outcome: 'hired', decided_at: '2026-08-15T10:00:00.000Z' },
          { id: 1301, outcome: 'rejected', decided_at: '2026-07-01T10:00:00.000Z' },
        ],
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Available when this was decided');
    expect(getDecisionTrace).toHaveBeenCalledWith(1234); // most recent by default

    const rejectedTab = await screen.findByRole('tab', { name: /Rejected/ });
    await user.click(rejectedTab);

    await waitFor(() => expect(getDecisionTrace).toHaveBeenCalledWith(1301));
  });

  it('does not show a picker for a single decision', async () => {
    renderPage();
    await screen.findByText('Available when this was decided');
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('preselects the decision named in ?decision=', async () => {
    getEvidenceGraph.mockResolvedValue(
      graphFixture({
        decisions: [
          { id: 1234, outcome: 'hired', decided_at: '2026-08-15T10:00:00.000Z' },
          { id: 1301, outcome: 'rejected', decided_at: '2026-07-01T10:00:00.000Z' },
        ],
      }),
    );
    renderPage('/hr/enrolments/en-1/evidence?decision=1301');
    await waitFor(() => expect(getDecisionTrace).toHaveBeenCalledWith(1301));
  });

  it('says so when no decision has been recorded yet', async () => {
    getEvidenceGraph.mockResolvedValue(graphFixture({ decisions: [] }));
    renderPage();
    expect(
      await screen.findByText(
        'No hire or reject decision has been recorded for this application yet.',
      ),
    ).toBeInTheDocument();
    expect(getDecisionTrace).not.toHaveBeenCalled();
  });
});

describe('EvidenceTrail — recorded after the decision', () => {
  it('shows a collapsed section naming what came later', async () => {
    getDecisionTrace.mockResolvedValue(
      traceFixture({
        after_decision: [
          {
            node: node({
              id: 'offer:o-1',
              kind: 'offer',
              stage: {
                name: 'offer',
                round_id: null,
                round_title: null,
                round_kind: null,
                workflow_version: null,
              },
              content: { status: 'sent' },
            }),
            reason: 'submitted after the decision',
          },
        ],
      }),
    );
    renderPage();
    expect(await screen.findByText('Recorded after the decision (1)')).toBeInTheDocument();
    expect(screen.getByText('Offer')).toBeInTheDocument();
  });
});

describe('EvidenceTrail — omitted footnote', () => {
  it('names what is never shown, and why', async () => {
    renderPage();
    expect(
      await screen.findByText(
        /interviewer notes — Private to the interviewer; never shown to HR\./,
      ),
    ).toBeInTheDocument();
  });
});

describe('EvidenceTrail — ai_involvement line', () => {
  it('states the count of AI-produced evidence and that a person decided', async () => {
    renderPage();
    expect(await screen.findByText(/1 piece of evidence was produced by AI\./)).toBeInTheDocument();
    expect(screen.getByText(/A named person made the decision\./)).toBeInTheDocument();
  });

  it('pluralises for more than one', async () => {
    getDecisionTrace.mockResolvedValue(
      traceFixture({ ai_involvement: { ai_produced_evidence: 3, decided_by: 'human' } }),
    );
    renderPage();
    expect(
      await screen.findByText(/3 pieces of evidence were produced by AI\./),
    ).toBeInTheDocument();
  });

  it('handles zero AI-produced evidence in words', async () => {
    getDecisionTrace.mockResolvedValue(
      traceFixture({ ai_involvement: { ai_produced_evidence: 0, decided_by: 'human' } }),
    );
    renderPage();
    expect(
      await screen.findByText(/No evidence available when this was decided was produced by AI\./),
    ).toBeInTheDocument();
  });
});

describe('EvidenceTrail — not found', () => {
  it('says the application could not be found rather than throwing', async () => {
    getEvidenceGraph.mockRejectedValue(new ApiError('Not found', 404));
    renderPage();
    expect(
      await screen.findByText(
        'This application could not be found, or you do not have access to it.',
      ),
    ).toBeInTheDocument();
  });
});
