/**
 * Central API config and authenticated fetch helper.
 *
 * Set in frontend/.env.local for local dev:
 *   VITE_API_BASE_URL=http://localhost:8000
 *   VITE_GOOGLE_CLIENT_ID=<your_google_client_id>
 */
const DEFAULT_LOCAL_API = 'http://localhost:8000';
const DEFAULT_PRODUCTION_API = 'https://tradetalk-api-933081724691.us-central1.run.app';

function isVercelProductionHost() {
  if (typeof window === 'undefined') return false;
  const host = window.location.hostname || '';
  return host.endsWith('.vercel.app') || host.includes('vercel.app');
}

/** Stale build env (localhost, Cloudflare tunnel, ngrok) must not win on production Vercel. */
function isNonProductionApiUrl(url) {
  if (!url) return true;
  if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i.test(url)) return true;
  if (/trycloudflare\.com|ngrok-free\.app|ngrok\.io/i.test(url)) return true;
  if (/tradetalkapp-backend\.onrender\.com/i.test(url)) return true;
  return false;
}

function resolveApiBaseUrl() {
  const fromEnv = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/$/, '');
  if (isVercelProductionHost()) {
    if (!isNonProductionApiUrl(fromEnv)) return fromEnv;
    return DEFAULT_PRODUCTION_API;
  }
  if (fromEnv) return fromEnv;
  return DEFAULT_LOCAL_API;
}

export const API_BASE_URL = resolveApiBaseUrl();

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
    if (res.status === 401) {
      clearToken();
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('auth-expired'));
      }
    }
    let errMsg = `HTTP ${res.status}`;
    let insufficientData = false;
    try {
      const body = await res.json();
      if (body.error === 'insufficient_data') {
        // Truthful-data contract: the backend refused to fabricate a result.
        insufficientData = true;
        errMsg = body.message || 'Not enough live data to produce a result. Please try again later.';
      } else {
        errMsg = typeof body.detail === 'string' ? body.detail : (body.detail?.message || body.error || errMsg);
      }
    } catch { /* ignore json parse errors */ }
    const err = new Error(errMsg);
    err.isInsufficientData = insufficientData;
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** apiFetch with AbortController timeout — prevents hung LLM routes from blocking the UI forever.
 *  @param {string} url
 *  @param {object} options
 *  @param {number} timeoutMs
 *  @param {AbortSignal|null} externalSignal — optional signal from a caller's AbortController (e.g. for cancel-session)
 */
export async function apiFetchTimed(url, options = {}, timeoutMs = 90000, externalSignal = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // Combine timeout signal with external (user cancel) signal when available
  let signal = controller.signal;
  if (externalSignal) {
    try {
      // AbortSignal.any is the standard way to compose signals (Chrome 116+, Firefox 124+)
      signal = AbortSignal.any
        ? AbortSignal.any([controller.signal, externalSignal])
        : controller.signal;
      // Fallback: manually abort our controller when external signal fires
      if (!AbortSignal.any && externalSignal) {
        externalSignal.addEventListener('abort', () => controller.abort(), { once: true });
      }
    } catch (_) { /* keep timeout-only signal */ }
  }

  try {
    return await apiFetch(url, { ...options, signal });
  } catch (e) {
    if (e?.name === 'AbortError') {
      if (externalSignal?.aborted) throw new Error('Request cancelled by user');
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}


/** JSON POST with auth headers (fire-and-forget safe). */
export async function apiPost(url, body, options = {}) {
  return apiFetch(url, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
    ...options,
  });
}

/**
 * Multipart POST (e.g. image upload). Do not set Content-Type — browser sets boundary.
 */
export async function apiPostMultipart(url, formData, timeoutMs = 120000) {
  const token = getToken();
  const headers = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(url, { method: 'POST', body: formData, headers, signal: controller.signal });
  } catch (e) {
    clearTimeout(timer);
    if (e.name === 'AbortError') {
      throw new Error(`Upload timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw e;
  }
  clearTimeout(timer);
  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      errMsg =
        typeof body.detail === 'string'
          ? body.detail
          : body.detail?.message || body.error || errMsg;
    } catch {
      /* ignore */
    }
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
      if (res.status === 401) {
        clearToken();
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new Event('auth-expired'));
        }
      }
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
