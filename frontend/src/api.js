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
      errMsg = body.detail || body.error || errMsg;
    } catch { /* ignore json parse errors */ }
    throw new Error(errMsg);
  }
  return res.json();
}
