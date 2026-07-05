// Auth API — switches between mock and real backend via VITE_USE_MOCK env var.
//
// All real-backend calls go through the central apiClient which handles:
//   - credentials:'include' (cookies)
//   - Authorization: Bearer injection
//   - 401 → single-flight refresh → retry
//
// login/register/getMe/logout keep the same signatures for zero call-site churn.
// The `token` parameter on getMe/logout is kept (but ignored) because the client
// injects the current token from the store automatically.

import type {
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  RegisterResponse,
  MeResponse,
  LogoutResponse,
} from '../types/auth';
import { mockAuthResponse, mockMeResponse, mockLogoutResponse, simulateDelay } from './mock';
import { apiGet, apiPost } from './client';
import { setToken } from './tokenStore';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

export async function login(request: LoginRequest): Promise<LoginResponse> {
  if (USE_MOCK) {
    await simulateDelay();
    return mockAuthResponse;
  }
  // skipAuth: true — no Bearer header on login; the response sets cookies.
  const res = await apiPost<LoginResponse>('/auth/login', request, { skipAuth: true });
  // Store the access token immediately so the very next authenticated call
  // (e.g. getMe) carries the Bearer header. Without this, getMe fires before
  // the store is populated → 401 → bounced back to /login.
  setToken(res.access_token);
  return res;
}

export async function register(request: RegisterRequest): Promise<RegisterResponse> {
  if (USE_MOCK) {
    await simulateDelay();
    return mockAuthResponse;
  }
  const res = await apiPost<RegisterResponse>('/auth/register', request, { skipAuth: true });
  // Populate the store before any follow-up authenticated call — but ONLY when the
  // server signed us in. When email verification is required there's no token (the
  // user must confirm their email first), so we leave the store empty.
  if (res.access_token) setToken(res.access_token);
  return res;
}

/**
 * Fetch the current user's profile.
 * The `_token` parameter is accepted for backwards-compatibility but ignored —
 * the central client injects the current token automatically.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export async function getMe(_token?: string): Promise<MeResponse> {
  if (USE_MOCK) {
    await simulateDelay(200);
    return mockMeResponse;
  }
  return apiGet<MeResponse>('/auth/me');
}

/**
 * Sign out — calls POST /auth/logout which clears both server-side cookies.
 * The `_token` parameter is accepted for backwards-compatibility but ignored.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export async function logout(_token?: string): Promise<LogoutResponse> {
  if (USE_MOCK) {
    await simulateDelay(200);
    return mockLogoutResponse;
  }
  return apiPost<LogoutResponse>('/auth/logout', {});
}

/**
 * Set a new password and clear the must-change flag.
 *
 * When `currentPassword` is supplied it is sent as `current_password` in the
 * request body — required for normal (non-bootstrap) password changes so a stolen
 * access token cannot permanently take over the account.
 *
 * When `currentPassword` is omitted the backend allows the call ONLY if the
 * account has `must_change_password=true` (the HR bootstrap first-change flow).
 * The ChangePassword.tsx page (must_change_password gate) uses this path and
 * intentionally omits `currentPassword`.
 */
export async function changePassword(
  newPassword: string,
  currentPassword?: string,
): Promise<{ ok: boolean }> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return { ok: true };
  }
  const body: Record<string, string> = { new_password: newPassword };
  if (currentPassword !== undefined) {
    body.current_password = currentPassword;
  }
  return apiPost<{ ok: boolean }>('/auth/change-password', body);
}

/**
 * Request a password-reset link. Always resolves ok — the backend returns the
 * same response whether or not the email exists (anti-enumeration).
 */
export async function forgotPassword(email: string): Promise<{ ok: boolean }> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return { ok: true };
  }
  // skipAuth: public endpoint, no Bearer token.
  return apiPost<{ ok: boolean }>('/auth/forgot-password', { email }, { skipAuth: true });
}

/** Set a new password from a reset-link token (public). */
export async function resetPassword(
  token: string,
  newPassword: string,
): Promise<{ ok: boolean }> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return { ok: true };
  }
  return apiPost<{ ok: boolean }>(
    '/auth/reset-password',
    { token, new_password: newPassword },
    { skipAuth: true },
  );
}

/** Confirm an email address from the signup link token (public). */
export async function verifyEmail(token: string): Promise<{ ok: boolean }> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return { ok: true };
  }
  return apiPost<{ ok: boolean }>('/auth/verify-email', { token }, { skipAuth: true });
}

/** Re-send the email-verification link to the signed-in user. */
export async function resendVerification(): Promise<{ ok: boolean }> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return { ok: true };
  }
  return apiPost<{ ok: boolean }>('/auth/resend-verification', {});
}
