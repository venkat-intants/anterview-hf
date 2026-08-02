// agent.ts — console copilot, proposal commit, and candidate assessment panel.
//
// THE COMMIT MODEL — read this before changing anything here.
//
// The backend agent has NO write tools. It cannot create, send, or change
// anything. When it wants something to happen it returns a `Proposal` carrying
// the exact request that would do it, and THIS FILE fires that request — from
// the browser, with the signed-in user's own credentials, against the same
// authorised endpoint they would hit by filling the form by hand.
//
// So `commitProposal` is the only place an agent-originated action becomes
// real, and it only runs from an explicit human click. Never call it
// automatically, in a useEffect, or in a loop over `run.proposals`.

import { apiGet, apiPost, apiRequest } from './client';

/** Where a claim came from — rendered as a clickable source chip. */
export interface Citation {
  kind:
    | 'applicant'
    | 'scorecard'
    | 'exam'
    | 'exam_attempt'
    | 'interview'
    | 'job'
    | 'analytics'
    | 'audit'
    | 'role_profile';
  id: string;
  label: string;
  href: string | null;
}

/** The request a human commits. Always a relative path on our own API. */
export interface CommitSpec {
  method: 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  path: string;
  body: Record<string, unknown>;
  label: string;
}

/** A drafted action. Inert until the user approves it. */
export interface Proposal {
  id: string;
  kind:
    | 'interview_invite'
    | 'exam'
    | 'exam_questions'
    | 'email'
    | 'job_description'
    | 'shortlist'
    | 'pipeline_decision'
    | 'note';
  title: string;
  summary: string;
  commit: CommitSpec;
  rationale: string;
  citations: Citation[];
  /** Present when committing reaches the candidate (email). Show it loudly. */
  risk_note: string | null;
  created_at: string;
}

export interface AgentChatResponse {
  agent: string;
  reply: string;
  proposals: Proposal[];
  citations: Citation[];
  tools_used: { name: string; ok: boolean; duration_ms: number }[];
  stop_reason: 'completed' | 'max_steps' | 'token_budget' | 'llm_error' | 'no_llm';
}

export interface AgentStatus {
  enabled: boolean;
  model_configured: boolean;
  console: string;
  capabilities: string[];
  note: string;
}

export interface HistoryTurn {
  role: 'user' | 'assistant';
  text: string;
}

/** Is the assistant available, and as which console? */
export function getAgentStatus(): Promise<AgentStatus> {
  return apiGet<AgentStatus>('/agent/status');
}

/** Ask this console's copilot. `history` is replayed for continuity. */
export function askAgent(message: string, history: HistoryTurn[] = []): Promise<AgentChatResponse> {
  // The server caps history at 12 turns; trimming here too keeps the request
  // small and makes the cap visible on this side rather than a silent surprise.
  return apiPost<AgentChatResponse>('/agent/chat', {
    message,
    history: history.slice(-12),
  });
}

/**
 * Enact a proposal. ONLY call this from an explicit user click.
 *
 * Fires the proposal's own method/path/body with the user's credentials. If
 * they lack permission for that endpoint it fails exactly as a manual attempt
 * would — a proposal cannot widen anyone's authority.
 */
export function commitProposal<T = unknown>(proposal: Proposal): Promise<T> {
  const { method, path, body } = proposal.commit;
  return apiRequest<T>(method, path, body);
}

// ── Assessment panel ────────────────────────────────────────────────────────

export type SignalName = 'resume' | 'exam' | 'coding' | 'interview';

export interface SignalAssessment {
  signal: SignalName;
  /** False = that round has not happened. NOT the same as scoring zero. */
  available: boolean;
  score_0_100: number | null;
  confidence: number;
  strengths: string[];
  concerns: string[];
  evidence: string[];
  citations: Citation[];
}

export interface Contradiction {
  between: [SignalName, SignalName];
  detail: string;
  suggested_check: string;
  severity: 'low' | 'medium' | 'high';
}

export interface PanelVerdict {
  applicant_id: string;
  applicant_label: string;
  signals: SignalAssessment[];
  contradictions: Contradiction[];
  summary: string;
  coverage_gaps: string[];
  suggested_next_step: string;
  confidence: number;
  /** Always 'human_only'. The API has no field that can express a verdict. */
  decision_authority: 'human_only';
  citations: Citation[];
}

/** Run the specialist panel on one candidate. */
export function assessCandidate(applicantId: string): Promise<PanelVerdict> {
  return apiPost<PanelVerdict>(`/agent/panel/${applicantId}`, {});
}

/** Trigger the nightly watcher sweep now. Platform owner only. */
export function runWatchersNow(): Promise<{
  companies: number;
  findings: number;
  notifications: number;
}> {
  return apiPost('/agent/watchers/run', {});
}

export const SIGNAL_LABEL: Record<SignalName, string> = {
  resume: 'Resume / ATS',
  exam: 'Written exam',
  coding: 'Coding test',
  interview: 'Interview',
};
