// Scorecard API — fetches scorecard data from feedback_billing service.
// Switches between mock and real backend via VITE_USE_MOCK env var.

import { simulateDelay, mockScorecardsResponse } from './mock';
import { feedbackGet } from './client';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

export interface ScoreBreakdown {
  communication: number;
  technical: number;
  problem_solving: number;
  confidence: number;
}

/** Per-axis "why this score" explanation. Empty strings for legacy scorecards. */
export interface AxisRationale {
  communication: string;
  technical: string;
  problem_solving: string;
  confidence: string;
}

export interface ImprovementItem {
  area: string;
  suggestion: string;
}

export interface ScorecardData {
  scorecard_id: string;
  session_id: string;
  composite_score: number;
  scores: ScoreBreakdown;
  /** Optional — present on scorecards generated after the rationale feature. */
  rationale?: AxisRationale;
  strengths: string[];
  improvements: ImprovementItem[];
  summary: string;
  report_pdf_url: string | null;
}

// ---------------------------------------------------------------------------
// Types — mirror feedback_billing ScorecardListItem / ScorecardListResponse
// ---------------------------------------------------------------------------

/** A single scorecard row in the paginated list response. */
export interface ScorecardListItem {
  scorecard_id: string;
  session_id: string;
  composite_score: number | null;
  /** ISO-8601 timestamp string. */
  created_at: string;
  /** First 200 characters of the full summary. */
  summary: string;
  /** Job title resolved from the linked session; null if job was deleted. */
  job_title: string | null;
}

export interface ScorecardListResponse {
  items: ScorecardListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface ListScorecardsParams {
  page?: number;
  perPage?: number;
}

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_SCORECARD: ScorecardData = {
  scorecard_id: '00000000-0000-0000-0000-000000000001',
  session_id: '00000000-0000-0000-0000-000000000002',
  composite_score: 7.05,
  scores: {
    communication: 7,
    technical: 6,
    problem_solving: 8,
    confidence: 7,
  },
  rationale: {
    communication:
      'Meets tier expectations (6-7). Answers were structured and easy to follow, with a clear intro-body-conclusion shape on most questions. To reach 8+, reduce filler words and tighten longer answers into 2-3 crisp points.',
    technical:
      'Below-to-meeting expectations (6). Demonstrated solid fundamentals but stayed high-level on system design and did not discuss trade-offs. To score higher, explain the "why" behind technical choices and name concrete tools/patterns.',
    problem_solving:
      'Exceeds tier expectations (8). Broke problems into steps and used concrete examples from past work, stating assumptions before solving. Strongest axis in this interview.',
    confidence:
      'Meets tier expectations (7). Composed and steady, with occasional hesitation on harder questions. A measured pace and fewer hedging phrases would push this to 8+.',
  },
  strengths: [
    'Clear communication throughout the interview',
    'Good use of concrete examples to support answers',
    'Structured thinking when approaching problems',
  ],
  improvements: [
    {
      area: 'Technical Depth',
      suggestion: 'Practice system design concepts and discuss trade-offs explicitly.',
    },
    {
      area: 'Confidence',
      suggestion: 'Speak at a measured pace and avoid filler words.',
    },
    {
      area: 'Problem Solving',
      suggestion: 'State your assumptions clearly before diving into a solution.',
    },
  ],
  summary:
    'A solid entry-level candidate who meets tier expectations on most axes. ' +
    'Communication and problem-solving stand out as strengths. ' +
    'Technical depth and confidence are areas for focused improvement.',
  report_pdf_url: null,
};

// ---------------------------------------------------------------------------
// getScorecard
// ---------------------------------------------------------------------------

/**
 * Fetch a scorecard by ID from the feedback_billing service.
 *
 * @param scorecardId - UUID of the scorecard row
 * @param _jwt - Accepted for backwards-compatibility; ignored (client injects token)
 * @returns ScorecardData including a pre-signed PDF URL if the PDF is ready
 */
export async function getScorecard(
  scorecardId: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _jwt?: string,
): Promise<ScorecardData> {
  if (USE_MOCK) {
    await simulateDelay(500);
    return { ...MOCK_SCORECARD, scorecard_id: scorecardId };
  }
  return feedbackGet<ScorecardData>(`/api/scorecards/${scorecardId}`);
}

// ---------------------------------------------------------------------------
// listScorecards
// ---------------------------------------------------------------------------

/**
 * Fetch the authenticated user's scorecards, newest-first.
 * Maps to GET /api/scorecards on feedback_billing (VITE_FEEDBACK_API_URL).
 *
 * @param params.page    1-indexed page number (default 1)
 * @param params.perPage Items per page, 1–100 (default 20)
 */
export async function listScorecards(
  params: ListScorecardsParams = {},
): Promise<ScorecardListResponse> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return mockScorecardsResponse;
  }

  const { page = 1, perPage = 20 } = params;
  const qs = new URLSearchParams();
  qs.set('page', String(page));
  qs.set('per_page', String(perPage));

  return feedbackGet<ScorecardListResponse>(`/api/scorecards?${qs.toString()}`);
}
