// CodeEvidencePanel + CodeSimilarityCompare — PH4-D3.
//
// The property this whole screen exists to get right: A SIGNAL IS NEVER A
// FINDING. Similarity signals are automated and unreviewed, integrity
// findings are recorded by a named person — the two live in visually
// separate sections and neither borrows the other's language. Alongside
// that: no coverage figure is ever shown (it is not available for any
// language), a token-level report says it is an approximation rather than a
// measurement, source is not fetched before HR asks to see it, and a
// finding's rationale is refused client-side under the server's own 20-char
// floor before it ever reaches the network.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CodeEvidence, SimilarityCompare } from '../api/codeEvidence';

const api = {
  getCodeEvidence: vi.fn(),
  getCodeSource: vi.fn(),
  triggerCodeAnalysis: vi.fn(),
  getSimilarityCompare: vi.fn(),
  recordCodeIntegrityFinding: vi.fn(),
};
vi.mock('../api/codeEvidence', async () => {
  const actual = await vi.importActual<typeof import('../api/codeEvidence')>('../api/codeEvidence');
  return {
    ...actual,
    getCodeEvidence: (...a: unknown[]) => api.getCodeEvidence(...a) as unknown,
    getCodeSource: (...a: unknown[]) => api.getCodeSource(...a) as unknown,
    triggerCodeAnalysis: (...a: unknown[]) => api.triggerCodeAnalysis(...a) as unknown,
    getSimilarityCompare: (...a: unknown[]) => api.getSimilarityCompare(...a) as unknown,
    recordCodeIntegrityFinding: (...a: unknown[]) => api.recordCodeIntegrityFinding(...a) as unknown,
  };
});

vi.mock('../components/CodeEditor', () => ({
  default: ({ value }: { value: string }) => <div data-testid="code-editor">{value}</div>,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

import CodeEvidencePanel from '../components/hr/CodeEvidencePanel';

const EVIDENCE: CodeEvidence = {
  attempt_id: 'att-1',
  code_redacted: false,
  reports: [
    {
      coding_question_id: 'q-py',
      language: 'python',
      analyser: 'python-ast',
      analyser_version: 'cq-1.0.0',
      status: 'complete',
      metrics: {
        kind: 'python-ast',
        functions: [
          { name: 'solve', line: 1, cyclomatic_complexity: 3, nesting_depth: 1, length_lines: 12, parameter_count: 2 },
        ],
        function_count: 1,
        complexity_total: 3,
        complexity_avg: 3,
        halstead: {
          distinct_operators: 4, distinct_operands: 6, total_operators: 10, total_operands: 12,
          vocabulary: 10, length: 22, volume: 73.1, difficulty: 4, effort: 292.4,
        },
        maintainability_index: 81.2,
        lines_of_code: 12,
        duplication_ratio: 0,
      },
      findings: [{ rule: 'unused_import', severity: 'info', line: 2, message: '`os` is imported but never used.' }],
      coverage: {
        available: false,
        reason: 'Coverage requires instrumented execution. This analyser is pure static analysis and never runs candidate code.',
      },
      error_class: null,
      source_sha256: 'abc123',
      created_at: '2026-08-01T00:00:00.000Z',
    },
    {
      coding_question_id: 'q-js',
      language: 'javascript',
      analyser: 'pygments-tokens',
      analyser_version: 'cq-1.0.0',
      status: 'complete',
      metrics: {
        kind: 'token-approximate',
        decision_points: 5,
        decision_point_density: 0.12,
        max_nesting_depth: 2,
        comment_ratio: 0.05,
        long_line_count: 0,
        lines_of_code: 20,
        halstead: {
          distinct_operators: 4, distinct_operands: 6, total_operators: 10, total_operands: 12,
          vocabulary: 10, length: 22, volume: 73.1, difficulty: 4, effort: 292.4,
        },
        maintainability_index: 70.0,
        duplication_ratio: 0.02,
      },
      findings: [],
      coverage: {
        available: false,
        reason: 'Coverage requires instrumented execution. This analyser is pure static analysis and never runs candidate code.',
      },
      error_class: null,
      source_sha256: 'def456',
      created_at: '2026-08-01T00:00:00.000Z',
    },
  ],
  test_results: {
    'q-py': { points: 10, raw: 10, language: 'python', submitted: true, tests: [{}, {}] },
    'q-js': { points: 10, raw: 4, language: 'javascript', submitted: true },
  },
  integrity: {
    integrity_score: 92,
    proctoring_summary: { counts: { tab_blur: 1 }, violations: 1 },
    event_counts: { tab_blur: 1 },
  },
  similarity_signals: [
    {
      id: 'sig-1',
      coding_question_id: 'q-py',
      attempt_low_id: 'att-1',
      attempt_high_id: 'att-2',
      reference_kind: 'submission',
      containment_low: 0.92,
      containment_high: 0.81,
      jaccard: 0.7,
      shared_fingerprints: 40,
      tokens_low: 120,
      tokens_high: 130,
      algorithm_version: 'sim-1.0.0',
      created_at: '2026-08-02T00:00:00.000Z',
      caption:
        'Automated, unreviewed — similar code is not evidence of misconduct on its own. Many candidates independently using the same tools or references can look alike.',
    },
  ],
  findings: [
    {
      id: 'find-1',
      coding_question_id: 'q-py',
      signal_id: 'sig-1',
      outcome: 'no_concern',
      rationale: 'Talked to both candidates; independently derived, no concern.',
      recorded_by_user_id: 'u-hr-1',
      created_at: '2026-08-03T00:00:00.000Z',
      redacted: false,
    },
  ],
};

const COMPARE: SimilarityCompare = {
  signal_id: 'sig-1',
  low: { language: 'python', excerpt: 'def solve():\n    return 1\n' },
  high: { language: 'python', excerpt: 'def other():\n    return 2\n' },
  matched_regions: [{ low_start: 1, low_end: 1, high_start: 1, high_end: 1 }],
  caption: 'Automated, unreviewed — similar code is not evidence of misconduct on its own.',
};

function renderPanel(examId = 'e-1', attemptId = 'att-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CodeEvidencePanel examId={examId} attemptId={attemptId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getCodeEvidence.mockResolvedValue(EVIDENCE);
  api.getCodeSource.mockResolvedValue({ language: 'python', source: 'def solve():\n    pass\n', redacted: false });
  api.getSimilarityCompare.mockResolvedValue(COMPARE);
  api.recordCodeIntegrityFinding.mockResolvedValue({ id: 'find-2' });
});

// Both fixture coding questions render every card's heading (each shows an
// empty state when it has nothing) — scope to q-py's own card, the one that
// actually carries a signal and a finding, rather than an ambiguous match.
function questionCard(shortQuestionId: string): HTMLElement {
  const marker = screen.getByText(shortQuestionId);
  return marker.closest('div')!.parentElement as HTMLElement;
}

describe('CodeEvidencePanel — a signal is never a finding', () => {
  it('keeps similarity signals and integrity findings in separate, distinctly labelled sections', async () => {
    renderPanel();
    await screen.findAllByText('Code quality');
    const card = questionCard('q-py');

    const signalsHeading = within(card).getByText('Similarity signals (automated, unreviewed)');
    const findingsHeading = within(card).getByText('Integrity findings (recorded by people)');

    const signalsSection = signalsHeading.closest('div')!.parentElement as HTMLElement;
    const findingsSection = findingsHeading.closest('div')!.parentElement as HTMLElement;

    // The signal's own fixed caption lives under the signals heading...
    expect(
      within(signalsSection).getByText(/similar code is not evidence of misconduct on its own/i),
    ).toBeInTheDocument();
    // ...and the recorded rationale lives under the findings heading, not the
    // signals one — the two must never collapse into a single list.
    expect(
      within(findingsSection).getByText(/independently derived, no concern/i),
    ).toBeInTheDocument();
    expect(
      within(signalsSection).queryByText(/independently derived, no concern/i),
    ).not.toBeInTheDocument();
  });

  it("labels the finding with who recorded it, not a similarity number", async () => {
    renderPanel();
    await screen.findAllByText('Code quality');
    const card = questionCard('q-py');
    const findingsHeading = within(card).getByText('Integrity findings (recorded by people)');
    const section = findingsHeading.closest('div')!.parentElement as HTMLElement;
    expect(within(section).getByText('No concern')).toBeInTheDocument();
    expect(within(section).getByText(/recorded by u-hr-1/i)).toBeInTheDocument();
  });
});

describe('CodeEvidencePanel — coverage and the approximation label', () => {
  it('never shows a coverage figure — only the reason it is unavailable', async () => {
    renderPanel();
    await screen.findAllByText('Code quality');

    // No percentage or "X% covered" style figure anywhere on the page.
    expect(screen.queryByText(/\bcoverage\b.*\d+%/i)).not.toBeInTheDocument();
    expect(
      screen.getAllByText(/Test coverage: not available/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Coverage requires instrumented execution/i).length,
    ).toBeGreaterThan(0);
  });

  it('labels the Python report as a full analysis and the JavaScript report as a token-level approximation', async () => {
    renderPanel();
    expect(await screen.findByText('Full analysis — Python AST')).toBeInTheDocument();
    expect(await screen.findByText('Token-level approximation — javascript')).toBeInTheDocument();
  });

  it('shows the sandbox test results, labelled as results, not coverage', async () => {
    renderPanel();
    const resultsHeadings = await screen.findAllByText('Test results');
    expect(resultsHeadings.length).toBe(2);
    expect(screen.getByText(/^10\/10 pts/)).toBeInTheDocument();
    expect(screen.getByText('4/10 pts')).toBeInTheDocument();
  });
});

describe('CodeEvidencePanel — source is not prefetched', () => {
  it('does not fetch source until HR clicks "View source"', async () => {
    renderPanel();
    await screen.findAllByText('Code quality');
    expect(api.getCodeSource).not.toHaveBeenCalled();

    const user = userEvent.setup();
    const [viewButton] = screen.getAllByRole('button', { name: 'View source' });
    await user.click(viewButton);

    await waitFor(() => expect(api.getCodeSource).toHaveBeenCalledTimes(1));
  });
});

describe('CodeSimilarityCompare — record a finding', () => {
  async function openCompare() {
    const user = userEvent.setup();
    renderPanel();
    await user.click((await screen.findAllByRole('button', { name: /compare & record a finding/i }))[0]);
    await screen.findByRole('dialog');
    return user;
  }

  it('highlights only the matched lines, on both sides, as plain text', async () => {
    await openCompare();
    await waitFor(() => expect(api.getSimilarityCompare).toHaveBeenCalledWith('sig-1'));

    const lowMatched = (await screen.findByText('def solve():')).closest('div') as HTMLElement;
    const lowUnmatched = screen.getByText('return 1').closest('div') as HTMLElement;
    expect(lowMatched.className).toMatch(/ui-warn/);
    expect(lowUnmatched.className).not.toMatch(/ui-warn/);
  });

  it('refuses a rationale under the server\'s 20-character floor before calling the server', async () => {
    const user = await openCompare();
    await waitFor(() => expect(api.getSimilarityCompare).toHaveBeenCalled());

    await user.type(screen.getByPlaceholderText(/what you reviewed/i), 'too short');
    await user.click(screen.getByRole('button', { name: 'Record finding' }));

    expect(await screen.findByText(/at least 20 characters/i)).toBeInTheDocument();
    expect(api.recordCodeIntegrityFinding).not.toHaveBeenCalled();
  });

  it('records a finding with the outcome, rationale and the signal it was opened from', async () => {
    const user = await openCompare();
    await waitFor(() => expect(api.getSimilarityCompare).toHaveBeenCalled());

    await user.click(screen.getByRole('radio', { name: 'Confirmed' }));
    await user.type(
      screen.getByPlaceholderText(/what you reviewed/i),
      'Reviewed both submissions line by line; identical helper structure with only variable renames.',
    );
    await user.click(screen.getByRole('button', { name: 'Record finding' }));

    await waitFor(() => expect(api.recordCodeIntegrityFinding).toHaveBeenCalledTimes(1));
    expect(api.recordCodeIntegrityFinding).toHaveBeenCalledWith(
      expect.objectContaining({
        attempt_id: 'att-1',
        coding_question_id: 'q-py',
        signal_id: 'sig-1',
        outcome: 'confirmed',
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Finding recorded');
  });

  it("shows the server's own refusal verbatim", async () => {
    api.recordCodeIntegrityFinding.mockRejectedValue(new Error('This changed while you were working. Reload and try again.'));
    const user = await openCompare();
    await waitFor(() => expect(api.getSimilarityCompare).toHaveBeenCalled());

    await user.type(
      screen.getByPlaceholderText(/what you reviewed/i),
      'Reviewed both submissions line by line; identical helper structure with only variable renames.',
    );
    await user.click(screen.getByRole('button', { name: 'Record finding' }));

    expect(
      await screen.findByText('This changed while you were working. Reload and try again.'),
    ).toBeInTheDocument();
  });

  it('notes that matched regions are not available for a reference-solution comparison', async () => {
    // Swap the fixture's one signal to a reference-solution kind so the panel
    // renders the corresponding label and the dialog explains the empty regions.
    const withReference: CodeEvidence = {
      ...EVIDENCE,
      similarity_signals: [
        { ...EVIDENCE.similarity_signals[0], reference_kind: 'reference_solution', attempt_high_id: null },
      ],
    };
    api.getCodeEvidence.mockResolvedValue(withReference);
    api.getSimilarityCompare.mockResolvedValue({ ...COMPARE, matched_regions: [] });

    const user = userEvent.setup();
    renderPanel();

    expect(await screen.findByText('Matches the reference solution')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /compare & record a finding/i }));
    await screen.findByRole('dialog');

    expect(
      await screen.findByText(/not available for a reference-solution comparison/i),
    ).toBeInTheDocument();
  });
});
