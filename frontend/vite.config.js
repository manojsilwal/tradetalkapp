import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy TradeTalk API paths to FastAPI so tools (e.g. FaultHunter) can use one origin:
//   --target-base-url http://127.0.0.1:5173
// Requires: backend on VITE_DEV_PROXY_TARGET (default http://127.0.0.1:8000) and `npm run dev` on 5173.
const API_TARGET = process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'

const API_PATH_PREFIXES = [
  '/decision-terminal',
  '/macro',
  '/advisor',
  '/trace',
  '/debate',
  '/backtest',
  '/analyze',
  // NOTE: Do not proxy `/chat` — the app route is GET /chat (SPA). Chat REST paths are
  // called via VITE_API_BASE_URL → :8000 (see frontend/src/api.js).
  '/metrics',
  '/strategies',
  '/auth',
  '/knowledge',
  '/portfolio',
  '/progress',
  '/challenges',
  '/learning',
  '/academy',
  '/notifications',
  '/debug',
  '/preferences',
  '/openapi.json',
  '/docs',
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
