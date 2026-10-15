// Shared copy for the HR-home Stages at risk widget and the full page.
/**
 * QA ANTHIRE-001/008. This list only covers applications in a stage that has an
 * SLA timer (a workflow round, or the final decision). An applicant waiting
 * BEFORE round one — new, or shortlisted with nothing started — is in no such
 * stage, so it can never appear here; the stalled rule on Attention required
 * covers it. Without saying so, "Nothing overdue" beside a stalled alert reads
 * as a contradiction.
 */
export const SLA_SCOPE_NOTE =
  'Covers stages with an SLA timer only. Applicants waiting before round one are flagged under Attention required.';
