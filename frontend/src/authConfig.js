/**
 * When false (default), gamification tabs render without AuthGate.
 * Set VITE_AUTH_REQUIRED=true in frontend/.env.local to re-enable sign-in walls.
 */
export const AUTH_REQUIRED = import.meta.env.VITE_AUTH_REQUIRED === 'true';

/** Vite dev server (`npm run dev`) — unlock all nav + admin routes without sign-in. */
export const LOCAL_DEV_MODE = import.meta.env.DEV === true && !AUTH_REQUIRED;

export const GUEST_USER = {
  user_id: 'guest',
  email: '',
  name: 'Guest',
  avatar: '',
  guest: true,
  is_admin: false,
};

/** Synthetic session for local Vite dev — AdminGate and sidebar treat as admin. */
export const LOCAL_DEV_USER = {
  user_id: 'local-dev',
  email: 'dev@tradetalk.local',
  name: 'Local Dev',
  avatar: '',
  guest: false,
  is_admin: true,
  dev_mode: true,
};

export function defaultAnonymousUser() {
  return LOCAL_DEV_MODE ? LOCAL_DEV_USER : GUEST_USER;
}
