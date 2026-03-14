/**
 * Central API config — reads VITE_API_BASE_URL at build time (production)
 * or falls back to localhost for local development.
 *
 * Set in frontend/.env.local for local dev:
 *   VITE_API_BASE_URL=http://localhost:8000
 */
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
