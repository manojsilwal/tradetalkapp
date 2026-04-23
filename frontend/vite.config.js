import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy TradeTalk API paths to FastAPI so tools (e.g. FaultHunter) can use one origin:
//   --target-base-url http://127.0.0.1:5173
// Requires: backend on VITE_DEV_PROXY_TARGET (default http://127.0.0.1:8000) and `npm run dev` on 5173.
const API_TARGET = process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'

// Only paths that are NOT React Router `<Route path>` entries. If a prefix matches an SPA
// route (same first segment), full navigation to e.g. /decision-terminal returns JSON from
// FastAPI instead of index.html — client calls still use VITE_API_BASE_URL → :8000 (api.js).
const API_PATH_PREFIXES = [
  '/advisor',
  '/trace',
  '/analyze',
  '/metrics',
  '/strategies',
  '/auth',
  '/knowledge',
  '/progress',
  '/notifications',
  '/debug',
  '/preferences',
  '/openapi.json',
  '/docs',
  '/sepl',
  '/resources',
]

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PATH_PREFIXES.map((path) => [path, { target: API_TARGET, changeOrigin: true }])
    ),
  },
})
