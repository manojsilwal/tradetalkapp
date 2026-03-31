/**
 * Central API config and authenticated fetch helper.
 *
 * Set in frontend/.env.local for local dev:
 *   VITE_API_BASE_URL=http://localhost:8000
 *   VITE_GOOGLE_CLIENT_ID=<your_google_client_id>
 *
 * Production builds sometimes set the base to `https://host/api`; the FastAPI app
 * serves routes at the root (`/macro`, `/trace`, …), so strip a trailing `/api`.
 */
function normalizeApiBaseUrl(raw) {
  const fallback = 'http://localhost:8000';
  if (!raw || typeof raw !== 'string') return fallback;
  let u = raw.trim();
  if (!u) return fallback;
  u = u.replace(/\/+$/, '');
  if (u.endsWith('/api')) {
    u = u.slice(0, -4);
    u = u.replace(/\/+$/, '');
  }
  return u || fallback;
}

export const API_BASE_URL = normalizeApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
);

export const GOOGLE_CLIENT_ID =
  import.meta.env.VITE_GOOGLE_CLIENT_ID || '';

/** localStorage key for the JWT session token */
const TOKEN_KEY = 'k2_token';

export const getToken    = ()        => localStorage.getItem(TOKEN_KEY);
export const setToken    = (t)       => localStorage.setItem(TOKEN_KEY, t);
export const clearToken  = ()        => localStorage.removeItem(TOKEN_KEY);

/** Fired once ~15m after a retryable API failure so UIs can refetch (see scheduleBackendRetryOnce). */
export const BACKEND_RETRY_EVENT = 'tradetalk-backend-retry';

const BACKEND_RETRY_MS = 15 * 60 * 1000;

let backendRetryTimeoutId = null;

function isOurApiUrl(url) {
  const s = String(url);
  return s.startsWith(API_BASE_URL);
}

function isRetryableHttpStatus(status) {
  if (status === 0) return true;
  return status === 429 || status === 502 || status === 503 || status === 504;
}

function scheduleBackendRetryOnce() {
  if (typeof window === 'undefined') return;
  if (backendRetryTimeoutId !== null) return;
  backendRetryTimeoutId = window.setTimeout(() => {
    backendRetryTimeoutId = null;
    window.dispatchEvent(new CustomEvent(BACKEND_RETRY_EVENT));
  }, BACKEND_RETRY_MS);
}

/** SSE or other transports that bypass `fetch` can call this so the app still retries in 15m. */
export function notifyBackendUnreachable() {
  scheduleBackendRetryOnce();
}

/** Clears a pending 15m retry after a successful call to our API. */
export function notifyBackendRecovered() {
  if (typeof window === 'undefined') return;
  if (backendRetryTimeoutId !== null) {
    clearTimeout(backendRetryTimeoutId);
    backendRetryTimeoutId = null;
  }
}

function maybeScheduleBackendRetry(url, status, networkError) {
  if (!isOurApiUrl(url)) return;
  if (networkError != null) {
    scheduleBackendRetryOnce();
    return;
  }
  if (status != null && isRetryableHttpStatus(status)) {
    scheduleBackendRetryOnce();
  }
}

function buildAuthHeaders(extra = {}) {
  const token = getToken();
  return {
    'Content-Type': 'application/json',
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/**
 * Authenticated fetch — automatically injects Authorization: Bearer <token>.
 * Falls back to a plain fetch if no token is stored (public endpoints).
 * Throws on non-2xx responses with the server's error message when available.
 *
 * On network failure or retryable server errors (502/503/504/429), schedules a single
 * {@link BACKEND_RETRY_EVENT} in 15 minutes so the app can try again without hammering the backend.
 */
export async function apiFetch(url, options = {}) {
  const headers = buildAuthHeaders(options.headers || {});
  let res;
  try {
    res = await fetch(url, { ...options, headers });
  } catch (e) {
    maybeScheduleBackendRetry(url, null, e);
    throw e;
  }
  if (!res.ok) {
    maybeScheduleBackendRetry(url, res.status, null);
    let errMsg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      errMsg = body.detail || body.error || errMsg;
    } catch { /* ignore json parse errors */ }
    throw new Error(errMsg);
  }
  notifyBackendRecovered();
  return res.json();
}

/**
 * Like `fetch`, but applies the same auth headers, retry scheduling, and recovery
 * notification as {@link apiFetch}. Returns the `Response` (caller reads body / stream).
 */
export async function apiFetchResponse(url, options = {}) {
  const headers = buildAuthHeaders(options.headers || {});
  let res;
  try {
    res = await fetch(url, { ...options, headers });
  } catch (e) {
    maybeScheduleBackendRetry(url, null, e);
    throw e;
  }
  if (!res.ok) {
    maybeScheduleBackendRetry(url, res.status, null);
    let errMsg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      errMsg = body.detail || body.error || errMsg;
    } catch { /* ignore */ }
    throw new Error(errMsg);
  }
  notifyBackendRecovered();
  return res;
}
