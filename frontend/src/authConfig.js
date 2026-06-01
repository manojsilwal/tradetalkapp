/**
 * When false (default), gamification tabs render without AuthGate.
 * Set VITE_AUTH_REQUIRED=true in frontend/.env.local to re-enable sign-in walls.
 */
export const AUTH_REQUIRED = import.meta.env.VITE_AUTH_REQUIRED === 'true';

export const GUEST_USER = {
  user_id: 'guest',
  email: '',
  name: 'Guest',
  avatar: '',
  guest: true,
};
