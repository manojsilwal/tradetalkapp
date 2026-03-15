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
      errMsg = body.detail || body.error || errMsg;
    } catch { /* ignore json parse errors */ }
    throw new Error(errMsg);
  }
  return res.json();
}
