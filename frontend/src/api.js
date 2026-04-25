/**
 * Central API config and authenticated fetch helper.
 *
 * Set in frontend/.env.local for local dev:
 *   VITE_API_BASE_URL=http://localhost:8000
 *   VITE_GOOGLE_CLIENT_ID=<your_google_client_id>
 */
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const GOOGLE_CLIENT_ID =
  import.meta.env.VITE_GOOGLE_CLIENT_ID || '';

/** localStorage key for the JWT session token */
const TOKEN_KEY = 'k2_token';

export const getToken    = ()        => localStorage.getItem(TOKEN_KEY);
export const setToken    = (t)       => localStorage.setItem(TOKEN_KEY, t);
export const clearToken  = ()        => localStorage.removeItem(TOKEN_KEY);

/**
 * Authenticated fetch — automatically injects Authorization: Bearer <token>.
 * Falls back to a plain fetch if no token is stored (public endpoints).
 * Throws on non-2xx responses with the server's error message when available.
 */
export async function apiFetch(url, options = {}) {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  const res = await fetch(url, { ...options, headers });
  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      errMsg = typeof body.detail === 'string' ? body.detail : (body.detail?.message || body.error || errMsg);
    } catch { /* ignore json parse errors */ }
    throw new Error(errMsg);
  }
  return res.json();
}

/**
 * JSON fetch with optional timeout, auth header, and X-Request-ID on errors.
 * Use for long-running endpoints (e.g. POST /backtest) where callers need request_id for QA.
 */
export async function fetchJsonWithMeta(url, options = {}, timeoutMs = 180000) {
  const token = getToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  if (options.body != null) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }
  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers,
    });
    clearTimeout(timer);
    const requestId = res.headers.get('x-request-id') || '';
    const text = await res.text();

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      if (text) {
        try {
          const body = JSON.parse(text);
          if (body.detail !== undefined) {
            detail =
              typeof body.detail === 'string'
                ? body.detail
                : JSON.stringify(body.detail);
          } else if (body.message) {
            detail = String(body.message);
          }
        } catch {
          detail = text.slice(0, 800);
        }
      }
      const err = new Error(
        requestId ? `${detail}\n\nRequest ID: ${requestId}` : detail
      );
      err.requestId = requestId;
      err.status = res.status;
      throw err;
    }

    let data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = {};
      }
    }
    return { data, requestId };
  } catch (e) {
    clearTimeout(timer);
    if (e.name === 'AbortError') {
      const err = new Error(
        `Request timed out after ${Math.round(timeoutMs / 1000)}s. The server may still be working — check logs or retry.`
      );
      err.code = 'TIMEOUT';
      throw err;
    }
    const msg = String(e.message || '');
    if (
      msg === 'Failed to fetch' ||
      e.name === 'TypeError' ||
      msg.includes('NetworkError')
    ) {
      const err = new Error(
        `Network error — cannot reach the API at ${API_BASE_URL}. ` +
          `Confirm VITE_API_BASE_URL in your build, CORS settings, and that the backend is running.`
      );
      err.code = 'NETWORK';
      throw err;
    }
    throw e;
  }
}
