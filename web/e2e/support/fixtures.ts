// Shared fixtures for the e2e specs: this run's tenant, signing in, and the few
// API calls that set a test up faster than clicking through the UI would.
//
// Set-up goes through the API; the behaviour under test goes through the UI.

import fs from 'node:fs';
import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test';
import { API_URL, TENANT_FILE, TEST_HOOKS_TOKEN } from './env';

export type Role = 'platform_owner' | 'super_admin' | 'hr_manager' | 'interviewer' | 'candidate';

export interface Account {
  email: string;
  password: string;
  user_id: string;
}

export interface Tenant {
  run_id: string;
  company: { id: string; name: string; slug: string };
  accounts: Record<Role, Account>;
}

function readTenant(): Tenant {
  if (!fs.existsSync(TENANT_FILE)) {
    throw new Error(`No tenant at ${TENANT_FILE} — run the suite with \`npm run e2e\` so global setup provisions one.`);
  }
  return JSON.parse(fs.readFileSync(TENANT_FILE, 'utf8')) as Tenant;
}

// data_gateway allows 5 sign-in attempts a minute per IP, and every spec signs
// in from the same one. Without this hint the suite fails as a timeout on the
// login page, which reads like a broken login.
const RATE_LIMITED =
  'Sign-in was rate limited (429). data_gateway allows 5 sign-ins a minute per IP; start it for the ' +
  'e2e run with RATE_LIMIT_LOGIN_PER_MINUTE=1000 (see e2e/README.md).';

/**
 * Sign in through the real login form and wait to leave it.
 *
 * Takes credentials rather than an Account, so a spec can sign in as a
 * candidate whose account the journey itself created.
 */
export async function signIn(
  page: Page,
  account: { email: string; password: string },
): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(account.email);
  await page.getByTestId('login-password').fill(account.password);
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith('/auth/login') && r.request().method() === 'POST',
    ),
    page.getByTestId('login-submit').click(),
  ]);
  if (response.status() === 429) throw new Error(RATE_LIMITED);
  await page.waitForURL((url) => !url.pathname.startsWith('/login'));
}

// One API sign-in per account per worker: set-up calls do not need a fresh
// session each, and every sign-in counts against the rate limit above.
const tokens = new Map<string, string>();

/** An API client signed in as one account. */
export class Api {
  private constructor(
    private readonly request: APIRequestContext,
    private readonly token: string,
  ) {}

  static async as(request: APIRequestContext, account: Account): Promise<Api> {
    const cached = tokens.get(account.email);
    if (cached) return new Api(request, cached);
    const res = await request.post(`${API_URL}/auth/login`, {
      data: { email: account.email, password: account.password },
    });
    if (res.status() === 429) throw new Error(RATE_LIMITED);
    expect(res.ok(), `API sign-in for ${account.email} returned ${res.status()}`).toBeTruthy();
    const body = (await res.json()) as { access_token: string };
    tokens.set(account.email, body.access_token);
    return new Api(request, body.access_token);
  }

  private headers(): Record<string, string> {
    return { Authorization: `Bearer ${this.token}` };
  }

  async get<T>(route: string): Promise<T> {
    const res = await this.request.get(`${API_URL}${route}`, { headers: this.headers() });
    expect(res.ok(), `GET ${route} returned ${res.status()}: ${await res.text()}`).toBeTruthy();
    return (await res.json()) as T;
  }

  async post<T>(route: string, data?: unknown): Promise<T> {
    const res = await this.request.post(`${API_URL}${route}`, { headers: this.headers(), data });
    expect(res.ok(), `POST ${route} returned ${res.status()}: ${await res.text()}`).toBeTruthy();
    return (await res.json()) as T;
  }

  async put<T>(route: string, data: unknown): Promise<T> {
    const res = await this.request.put(`${API_URL}${route}`, { headers: this.headers(), data });
    expect(res.ok(), `PUT ${route} returned ${res.status()}: ${await res.text()}`).toBeTruthy();
    return (await res.json()) as T;
  }

  async patch<T>(route: string, data: unknown): Promise<T> {
    const res = await this.request.patch(`${API_URL}${route}`, { headers: this.headers(), data });
    expect(res.ok(), `PATCH ${route} returned ${res.status()}: ${await res.text()}`).toBeTruthy();
    return (await res.json()) as T;
  }

  /** For asserting refusals: returns the raw status and body instead of failing. */
  async patchRaw(route: string, data: unknown): Promise<{ status: number; body: string }> {
    const res = await this.request.patch(`${API_URL}${route}`, { headers: this.headers(), data });
    return { status: res.status(), body: await res.text() };
  }

  /** For asserting refusals: returns the raw status and body instead of failing. */
  async postRaw(route: string, data?: unknown): Promise<{ status: number; body: string }> {
    const res = await this.request.post(`${API_URL}${route}`, { headers: this.headers(), data });
    return { status: res.status(), body: await res.text() };
  }
}

/**
 * Run data_gateway's background passes now instead of waiting for their timers:
 * the reconciler scores applications and ingests uploads; the reminder sweep
 * records interview results. Needs TEST_HOOKS_ENABLED on the server and
 * E2E_TEST_HOOKS_TOKEN here.
 */
export async function runBackgroundPasses(
  request: APIRequestContext,
  which: ('reconcile' | 'reminders')[] = ['reconcile'],
): Promise<void> {
  if (!TEST_HOOKS_TOKEN) {
    throw new Error(
      'E2E_TEST_HOOKS_TOKEN is not set. Start data_gateway with TEST_HOOKS_ENABLED=true and ' +
        'TEST_HOOKS_TOKEN, and give the suite the same token (see e2e/README.md).',
    );
  }
  for (const pass of which) {
    const send = () =>
      request.post(`${API_URL}/test-hooks/${pass}`, {
        headers: { 'X-Test-Hooks-Token': TEST_HOOKS_TOKEN },
      });
    // One retry on a dropped connection. The request context reuses keep-alive
    // connections, and uvicorn closes an idle one after 5 seconds, so a call
    // that lands on a connection the server is closing fails with ECONNRESET
    // before the server sees it. A pass is safe to run twice; a lost call is
    // not, because the spec then waits on work that never started.
    const res = await send().catch(async (err: unknown) => {
      if (!String(err).includes('ECONNRESET')) throw err;
      return send();
    });
    expect(
      res.ok(),
      `test hook ${pass} returned ${res.status()} — is data_gateway running with TEST_HOOKS_ENABLED and the same token?`,
    ).toBeTruthy();
  }
}

export interface Opening {
  id: string;
  title: string;
}

/** A fresh opening with a unique title, owned by the HR manager. */
export async function createOpening(api: Api, prefix: string): Promise<Opening> {
  const title = `${prefix} ${Date.now().toString(36)}`;
  const body = await api.post<{ id: string; title: string }>('/hr/requisitions', {
    title,
    level: 'mid',
    target_hires: 1,
  });
  return { id: body.id, title: body.title };
}

interface ExamStructure {
  rounds: { id: string; title: string; sections: { question_count: number }[] }[];
}

interface WorkflowDetail {
  id: string;
  version: number;
  status: string;
  rounds: { id: string; title: string; kind: string }[];
}

export interface LiveOpening extends Opening {
  /** The workflow a candidate who applies will join. */
  workflowId: string;
  /** Its single MCQ round, so a spec can talk about "the Aptitude round". */
  roundId: string;
  examId: string;
}

/**
 * An opening a candidate can actually apply to and be assessed by: one MCQ
 * round of generated questions, published, taking public applications.
 *
 * Takes both accounts it needs: ``api`` is the HR manager who owns the
 * opening, ``approver`` the super admin who approves it.
 *
 * All of it through the API — this is the starting position for a journey, not
 * the behaviour under test. Question generation is free and repeatable because
 * data_gateway runs with AI_FAKE_MODE (see README).
 */
export async function createLiveMcqOpening(
  api: Api,
  approver: Api,
  prefix: string,
  opts: { passThreshold?: number; questions?: number } = {},
): Promise<LiveOpening> {
  const passThreshold = opts.passThreshold ?? 60;
  const questions = opts.questions ?? 4;
  const opening = await createOpening(api, prefix);

  // An opening cannot take public applications until it is approved, and HR
  // cannot approve their own: the super admin does. Two accounts, as in life.
  await api.post(`/hr/requisitions/${opening.id}/approval/submit`, { note: null });
  await approver.post(`/hr/requisitions/${opening.id}/approval/approve`, { note: null });

  const exam = await api.post<{ id: string }>('/hr/exams', {
    title: `Aptitude — ${opening.title}`,
    kind: 'mcq',
    pass_threshold: passThreshold,
    time_limit_seconds: 1800,
    target_job_title: opening.title,
  });
  const generated = await api.post<{ questions: unknown[] }>(
    `/hr/exams/${exam.id}/questions/generate`,
    { topic: opening.title, num_questions: questions, difficulty: 'medium', language: 'en' },
  );
  await api.post(`/hr/exams/${exam.id}/questions/bulk`, { questions: generated.questions });
  const structure = await api.get<ExamStructure>(`/hr/exams/${exam.id}/structure`);
  const examRoundId = structure.rounds[0].id;
  // A draft exam round mints links candidates cannot open — publish it, as the
  // exam editor does.
  await api.patch(`/hr/exams/${exam.id}/rounds/${examRoundId}`, { status: 'published' });

  const draft = await api.post<WorkflowDetail>(`/hr/requisitions/${opening.id}/workflows`, {});
  await api.patch(`/hr/workflows/${draft.id}`, {
    auto_score_on_apply: true,
    auto_assign_first_round: true,
    auto_advance_rounds: true,
  });
  const withRound = await api.post<WorkflowDetail>(`/hr/workflows/${draft.id}/rounds`, {
    title: 'Aptitude',
    kind: 'mcq',
    pass_threshold: passThreshold,
    deadline_days: 5,
    exam_round_id: examRoundId,
  });
  // PH4-O6: a version must be reviewed and approved by the company's super
  // admin before HR can publish it. The same super admin who approved the
  // opening does the review — a different account from the one authoring it.
  await api.post(`/hr/workflows/${draft.id}/submit-review`, { note: null });
  await approver.post(`/admin/workflow-reviews/${draft.id}/approve`, { note: null });
  await api.post(`/hr/workflows/${draft.id}/publish`);
  await api.patch(`/hr/requisitions/${opening.id}`, { public_apply_enabled: true });

  const roundId = withRound.rounds.find((r) => r.kind === 'mcq')!.id;
  return { ...opening, workflowId: draft.id, roundId, examId: exam.id };
}

/**
 * An opening whose only round is a human review, with a checklist.
 *
 * The counterpart to createLiveMcqOpening: nothing here is scored by machine,
 * so the round advances only when a person says it does (C3/C4).
 */
export async function createLiveReviewOpening(
  api: Api,
  approver: Api,
  prefix: string,
): Promise<LiveOpening> {
  const opening = await createOpening(api, prefix);
  await api.post(`/hr/requisitions/${opening.id}/approval/submit`, { note: null });
  await approver.post(`/hr/requisitions/${opening.id}/approval/approve`, { note: null });

  const draft = await api.post<WorkflowDetail>(`/hr/requisitions/${opening.id}/workflows`, {});
  await api.patch(`/hr/workflows/${draft.id}`, {
    auto_score_on_apply: true,
    auto_assign_first_round: true,
    auto_advance_rounds: true,
  });
  const withRound = await api.post<WorkflowDetail>(`/hr/workflows/${draft.id}/rounds`, {
    title: REVIEW_ROUND_TITLE,
    kind: 'human_review',
    deadline_days: 5,
    criteria: [
      { id: 'communication', name: 'Communication', kind: 'behavioural', weight: 0.6 },
      { id: 'ownership', name: 'Ownership', kind: 'behavioural', weight: 0.4 },
    ],
  });
  // PH4-O6: same review gate as createLiveMcqOpening.
  await api.post(`/hr/workflows/${draft.id}/submit-review`, { note: null });
  await approver.post(`/admin/workflow-reviews/${draft.id}/approve`, { note: null });
  await api.post(`/hr/workflows/${draft.id}/publish`);
  await api.patch(`/hr/requisitions/${opening.id}`, { public_apply_enabled: true });

  const roundId = withRound.rounds.find((r) => r.kind === 'human_review')!.id;
  return { ...opening, workflowId: draft.id, roundId, examId: '' };
}

/**
 * An opening whose round is a CODING test, with generated problems.
 *
 * Needs a code runner: data_gateway executes submissions through
 * EXECUTION_PROVIDER (self-hosted Piston locally — scripts/piston-up.ps1).
 * In AI_FAKE_MODE the generated problem is "read two integers and print their
 * sum", with sample and hidden tests, so a spec can solve it deterministically.
 */
export async function createLiveCodingOpening(
  api: Api,
  approver: Api,
  prefix: string,
): Promise<LiveOpening> {
  const opening = await createOpening(api, prefix);
  await api.post(`/hr/requisitions/${opening.id}/approval/submit`, { note: null });
  await approver.post(`/hr/requisitions/${opening.id}/approval/approve`, { note: null });

  const exam = await api.post<{ id: string }>('/hr/exams', {
    title: `Coding — ${opening.title}`,
    kind: 'coding',
    pass_threshold: 60,
    time_limit_seconds: 1800,
    target_job_title: opening.title,
  });
  const generated = await api.post<{ questions: Record<string, unknown>[] }>(
    `/hr/exams/${exam.id}/coding-questions/generate`,
    {
      topic: opening.title,
      num_questions: 1,
      difficulty: 'medium',
      language: 'en',
      allowed_languages: ['python'],
    },
  );
  // Generated problems come back for PREVIEW; HR saves the ones they want.
  for (const question of generated.questions) {
    await api.post(`/hr/exams/${exam.id}/coding-questions`, question);
  }

  const structure = await api.get<ExamStructure>(`/hr/exams/${exam.id}/structure`);
  const examRoundId = structure.rounds[0].id;
  await api.patch(`/hr/exams/${exam.id}/rounds/${examRoundId}`, { status: 'published' });

  const draft = await api.post<WorkflowDetail>(`/hr/requisitions/${opening.id}/workflows`, {});
  await api.patch(`/hr/workflows/${draft.id}`, {
    auto_score_on_apply: true,
    auto_assign_first_round: true,
    auto_advance_rounds: true,
  });
  const withRound = await api.post<WorkflowDetail>(`/hr/workflows/${draft.id}/rounds`, {
    title: 'Coding',
    kind: 'coding',
    pass_threshold: 60,
    deadline_days: 5,
    exam_round_id: examRoundId,
  });
  // PH4-O6: same review gate as createLiveMcqOpening.
  await api.post(`/hr/workflows/${draft.id}/submit-review`, { note: null });
  await approver.post(`/admin/workflow-reviews/${draft.id}/approve`, { note: null });
  await api.post(`/hr/workflows/${draft.id}/publish`);
  await api.patch(`/hr/requisitions/${opening.id}`, { public_apply_enabled: true });

  const roundId = withRound.rounds.find((r) => r.kind === 'coding')!.id;
  return { ...opening, workflowId: draft.id, roundId, examId: exam.id };
}

/** The review round's title, shared by the helper and the spec that reads it. */
export const REVIEW_ROUND_TITLE = 'Panel review';

export interface RoundResult {
  title: string;
  percent: number | null;
  passed: boolean | null;
  /** 'human' when a person graded the round, otherwise the system did. */
  graded_by: string;
}

export interface QueueRow {
  enrolment_id: string;
  full_name: string;
  status: string;
  held: boolean;
  held_reason: string | null;
  /** Parked on a human_review round, waiting for someone's verdict. */
  awaiting_review: boolean;
  review_round_title: string | null;
  round_results: RoundResult[];
}

/** The opening's decision queue, as HR's page reads it. */
export function decisionQueue(api: Api, openingId: string): Promise<QueueRow[]> {
  return api.get<QueueRow[]>(`/hr/requisitions/${openingId}/decision-queue`);
}

export interface Enrolment {
  id: string;
  applicant_id: string;
  full_name: string;
  status: string;
  current_round_id: string | null;
}

/** One named candidate's application to this opening. Fails if it is not there. */
export async function enrolmentFor(
  api: Api,
  openingId: string,
  fullName: string,
): Promise<Enrolment> {
  const rows = await api.get<Enrolment[]>(`/hr/requisitions/${openingId}/enrolments`);
  const mine = rows.find((r) => r.full_name === fullName);
  expect(
    mine,
    `no application for ${fullName} on this opening — found: ${rows.map((r) => r.full_name).join(', ') || '(none)'}`,
  ).toBeTruthy();
  return mine!;
}

export interface StageMove {
  from_status: string | null;
  to_status: string;
  automated: boolean;
  actor: string | null;
  reason: string | null;
  /** PH4-O4: the category chosen with a final decision, and its label as chosen. */
  reason_code: string | null;
  reason_label: string | null;
}

/**
 * The append-only stage ledger for one application.
 *
 * The specs assert against this as well as the screen because it is where
 * "a person decided this, and here is why" is actually recorded — the screen
 * can only show what a person typed.
 */
export function stageHistory(api: Api, enrolmentId: string): Promise<StageMove[]> {
  return api.get<StageMove[]>(`/hr/enrolments/${enrolmentId}/history`);
}

export const test = base.extend<{ tenant: Tenant }>({
  // eslint-disable-next-line no-empty-pattern
  tenant: async ({}, use) => {
    await use(readTenant());
  },
});

export { expect };
