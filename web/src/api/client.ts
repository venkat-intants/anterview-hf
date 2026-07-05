// client.ts — central API client with automatic token injection and single-flight
// 401 → refresh → retry logic.
//
// All API modules should use this instead of their own local fetch wrappers.
//
// Key behaviours:
//  - Always sends credentials:'include' so the httpOnly refresh cookie travels.
//  - Attaches Authorization: Bearer <token> when a token exists.
//  - On 401 from any call (except /auth/refresh and /auth/login themselves):
//      1. Fires a single refresh request; concurrent 401s share the same promise.
//      2. On success: stores new token, retries the original request once.
//      3. On failure (401/403 from the refresh): clears auth, toasts, redirects.
//  - CSRF double-submit: the refresh call reads `csrf_token` from document.cookie
//    and sends it as the X-CSRF-Token header (required by the backend).
//  - Mock mode: when VITE_USE_MOCK=true the refresh endpoint is never hit; callers
//    rely on mock data paths instead.

import { getToken, setToken, clearToken } from './tokenStore';
import { toast } from '../lib/toast';

// Fail-CLOSED: mock data is used only when VITE_USE_MOCK is explicitly 'true'.
// An unset/missing var → real backend (so a mis-configured prod deploy surfaces
// as API errors, never as silently-served fake data).
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

// Paths that must NOT trigger a refresh loop when they 401.
const SKIP_REFRESH_PATHS = ['/auth/refresh', '/auth/login', '/auth/register'];

// ---------------------------------------------------------------------------
// Single-flight refresh state
// ---------------------------------------------------------------------------

let _refreshPromise: Promise<boolean> | null = null;

/** Read a cookie value by name from document.cookie. Returns null if absent. */
function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.split('; ').find((row) => row.startsWith(`${name}=`));
  return match ? (match.split('=')[1] ?? null) : null;
}

/**
 * Attempt a token refresh via POST /auth/refresh.
 * Returns true on success, false on any failure.
 * Multiple concurrent callers share the same in-flight promise.
 */
export function attemptRefresh(apiBase: string): Promise<boolean> {
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = (async (): Promise<boolean> => {
    try {
      const csrf = readCookie('csrf_token');
      if (!csrf) {
        // No readable csrf_token cookie ⇒ no valid logged-in session to refresh.
        // A genuine session always carries the JS-readable csrf_token cookie
        // alongside the httpOnly refresh_token. POSTing without it would hit a
        // stale refresh_token cookie and return a confusing 403
        // "CSRF validation failed" — so short-circuit to a clean "not logged in".
        return false;
      }
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
      };
      const res = await fetch(`${apiBase}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers,
      });
      if (!res.ok) return false;
      const body = (await res.json()) as {
        access_token: string;
        expires_in: number;
        user_id: string;
        roles: string[];
      };
      setToken(body.access_token);
      return true;
    } catch {
      return false;
    } finally {
      _refreshPromise = null;
    }
  })();

  return _refreshPromise;
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

interface ClientOptions extends Omit<RequestInit, 'headers'> {
  /** Extra headers beyond the defaults; merged after auth injection. */
  headers?: Record<string, string>;
  /** If true, skip the token injection and 401-refresh flow (auth endpoints). */
  skipAuth?: boolean;
}

/** Parse a JSON body, tolerating empty responses (204 No Content). */
function parseJsonOrEmpty<T>(res: Response): Promise<T> {
  // `headers` is always present on a real Response; guard it so test doubles
  // that only set `status` still work. 204 short-circuits regardless.
  if (res.status === 204 || res.headers?.get('content-length') === '0') {
    return Promise.resolve(undefined as T);
  }
  return res.json() as Promise<T>;
}

async function clientFetch<T>(url: string, options: ClientOptions = {}): Promise<T> {
  const { skipAuth = false, headers: extraHeaders = {}, ...fetchOptions } = options;

  const token = getToken();
  const authHeaders: Record<string, string> =
    skipAuth || !token ? {} : { Authorization: `Bearer ${token}` };

  const response = await fetch(url, {
    ...fetchOptions,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...extraHeaders,
    },
  });

  // Check if this URL path is an auth endpoint that should not trigger refresh.
  const urlPath = (() => {
    try {
      return new URL(url).pathname;
    } catch {
      return url;
    }
  })();
  const isSkipPath = SKIP_REFRESH_PATHS.some((p) => urlPath.endsWith(p));

  if (response.status === 401 && !skipAuth && !isSkipPath && !USE_MOCK) {
    // Single-flight refresh against the auth service (data_gateway). The
    // /auth/refresh endpoint always lives on VITE_API_BASE_URL regardless of
    // which service returned the 401.
    // eslint-disable-next-line @typescript-eslint/no-unsafe-argument
    const refreshed = await attemptRefresh(import.meta.env.VITE_API_BASE_URL);

    if (refreshed) {
      // Retry the original request once with the new token
      const newToken = getToken();
      const retryResponse = await fetch(url, {
        ...fetchOptions,
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(newToken ? { Authorization: `Bearer ${newToken}` } : {}),
          ...extraHeaders,
        },
      });

      if (!retryResponse.ok) {
        const errorBody = (await retryResponse.json().catch(() => ({}))) as {
          detail?: string;
        };
        throw new Error(errorBody.detail ?? `HTTP ${retryResponse.status}`);
      }

      return parseJsonOrEmpty<T>(retryResponse);
    } else {
      // Refresh failed — clear auth, toast, redirect
      clearToken();
      toast.error('Session expired — please sign in');
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
      throw new Error('Session expired');
    }
  }

  if (!response.ok) {
    const errorBody = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(errorBody.detail ?? `HTTP ${response.status}`);
  }

  return parseJsonOrEmpty<T>(response);
}

// ---------------------------------------------------------------------------
// Typed HTTP helpers
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const DATA_GATEWAY_BASE: string = import.meta.env.VITE_API_BASE_URL;
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const INTERVIEW_BASE: string = import.meta.env.VITE_INTERVIEW_API_URL;
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const FEEDBACK_BASE: string =
  import.meta.env.VITE_FEEDBACK_API_URL || import.meta.env.VITE_API_BASE_URL;

function buildUrl(base: string, path: string): string {
  return `${base}${path}`;
}

/** GET request against data_gateway (VITE_API_BASE_URL). */
export function apiGet<T>(path: string, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(DATA_GATEWAY_BASE, path), opts);
}

/** POST request against data_gateway. */
export function apiPost<T>(path: string, body: unknown, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(DATA_GATEWAY_BASE, path), {
    ...opts,
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** PUT request against data_gateway. */
export function apiPut<T>(path: string, body: unknown, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(DATA_GATEWAY_BASE, path), {
    ...opts,
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

/** PATCH request against data_gateway. */
export function apiPatch<T>(path: string, body: unknown, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(DATA_GATEWAY_BASE, path), {
    ...opts,
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

/** DELETE request against data_gateway. */
export function apiDelete<T>(path: string, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(DATA_GATEWAY_BASE, path), {
    ...opts,
    method: 'DELETE',
  });
}

/** POST request against interview_core (VITE_INTERVIEW_API_URL). */
export function interviewPost<T>(path: string, body: unknown, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(INTERVIEW_BASE, path), {
    ...opts,
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** POST request against feedback_billing (VITE_FEEDBACK_API_URL). */
export function feedbackGet<T>(path: string, opts?: ClientOptions): Promise<T> {
  return clientFetch<T>(buildUrl(FEEDBACK_BASE, path), opts);
}

/**
 * Multipart upload via XHR (needed for real upload-progress callbacks).
 * Falls through to fetch when onProgress is not needed.
 *
 * The caller is responsible for NOT setting Content-Type — the browser must
 * set multipart/form-data with the correct boundary.
 */
export function uploadWithProgress<T>(
  url: string,
  formData: FormData,
  onProgress?: (pct: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const token = getToken();
    const xhr = new XMLHttpRequest();

    if (onProgress) {
      xhr.upload.onprogress = (event: ProgressEvent) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText) as T;
          resolve(data);
        } catch {
          reject(new Error('Invalid response from server'));
        }
      } else {
        let detail = `HTTP ${xhr.status}`;
        try {
          const body = JSON.parse(xhr.responseText) as { detail?: string };
          if (body.detail) detail = body.detail;
        } catch {
          // leave default
        }
        reject(new Error(detail));
      }
    };

    xhr.onerror = () => {
      reject(new Error('Network error — could not reach server'));
    };

    xhr.open('POST', url);
    // Do NOT set Content-Type — browser sets multipart/form-data with boundary
    if (token) {
      xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    }
    xhr.withCredentials = true;
    xhr.send(formData);
  });
}

/** Raw clientFetch for callers that need full control over the URL. */
export { clientFetch };
