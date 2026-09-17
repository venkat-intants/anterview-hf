// InterviewerRoute — requires the 'interviewer' role OR 'hr_manager'.
//
// HR managers can also be assigned as interviewers and use this console
// (D4-1) — they do not need a separate 'interviewer' role for that, so this
// guard admits both. Non-interviewer, non-HR authenticated users are
// redirected to their own home; unauthenticated to /login.
//
// Thin wrapper over RoleRoute, which holds the shared guard body.

import RoleRoute from './RoleRoute';

export default function InterviewerRoute() {
  return <RoleRoute roles={['interviewer', 'hr_manager']} />;
}
