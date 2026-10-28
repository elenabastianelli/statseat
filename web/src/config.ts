/**
 * The app always calls the backend on its own origin (`/api/...`):
 * - production: the FastAPI container serves both the PWA and the API;
 * - `npm run dev`: Vite forwards /api to the backend named by STATSEAT_BACKEND (vite.config.ts), by default the
 *   S-0 mock on http://127.0.0.1:8000. To use the real backend (S-7), set STATSEAT_BACKEND in web/.env.local
 *   (see .env.example): that is the only place to change. Same origin also works from a phone on the local network.
 */
export const API_BASE = "";

/** Name of the app: a placeholder until the team picks the final one (L-2d). Change it here only. */
export const APP_NAME = "statseat";

/** How often the app asks the backend for the match state (phase, minute, unlocked sections). */
export const STATE_POLL_MS = 5000;
/** How often the Live section refreshes its numbers while the ball is in play. */
export const LIVE_POLL_MS = 10000;
