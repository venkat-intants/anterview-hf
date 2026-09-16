// Shared fixtures for the e2e specs: this run's tenant, signing in, and the few
// API calls that set a test up faster than clicking through the UI would.
//
// Set-up goes through the API; the behaviour under test goes through the UI.

import fs from 'node:fs';
import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test';
import { API_URL, TENANT_FILE } from './env';

export type Role = 'platform_owner' | 'super_admin' | 'hr_manager' | 'candidate';

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

/** Sign in through the real login form and wait to leave it. */
export async function signIn(page: Page, account: Account): Promise<void> {
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

  /** For asserting refusals: returns the raw status and body instead of failing. */
  async patchRaw(route: string, data: unknown): Promise<{ status: number; body: string }> {
    const res = await this.request.patch(`${API_URL}${route}`, { headers: this.headers(), data });
    return { status: res.status(), body: await res.text() };
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

export const test = base.extend<{ tenant: Tenant }>({
  // eslint-disable-next-line no-empty-pattern
  tenant: async ({}, use) => {
    await use(readTenant());
  },
});

export { expect };
