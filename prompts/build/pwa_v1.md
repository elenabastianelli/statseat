# statseat PWA v1

- **Date:** 2026-10-24
- **Tool:** Claude Code (Claude Opus 5.5)
- **Area:** `[APP > PWA v1]`

## Prompt (English version of the team prompt)

> Create the frontend of the statseat app in web/ (React + Vite + TypeScript, PWA, mobile-first) as in
> the project plan, using the contract and the mock server of app/contract/: login with the ticket code
> and one device only (demo and judge codes work on several devices); the ticket tier decides the version (silver =
> Casual only; gold = Analyst with switch to Casual; demo and judge = gold plus Inspector, demo also the phase panel);
> sections Pre-match (always), Live (only during play), Data & Statistics, Games and Polls (half-time and after the
> final whistle), Recap (after the final whistle), a "My ticket" page with the ticket image (from
> synthetic/SYN-001/tickets/ when available), an "Ask Archie" button always visible with a provisional cartoon icon.
> UI text in English with translation files (en, it). The backend address must live in one place, so that in the
> afternoon we can switch from the mock server to the real one. Tell me how to start it.

Clarifications from the orchestrating agent: types generated from `app/contract/openapi.json` (never hand-written);
tier rules read from the session (`allowed_modes`, `inspector_enabled`, `demo_panel_enabled`), never decided by the
app; sections and their unlock phase from `GET /api/match/state`; Archie reactive only, pointing to a chart instead of
drawing; metrics may be `null` with a reason in `unavailable` (contract v0.2.1 being prepared in the match engine): show the reason,
never a number; no personal data and never the ticket code on My ticket; the app must not modify `app/contract/`;
tests for translation parity and contract labels; everything in English.

## Result

- `web/`: React 19, Vite 8, TypeScript 5.9, vite-plugin-pwa (service worker that never caches `/api`).
- `src/api/`: generated `schema.d.ts` (`npm run gen:api`), type names, client with device id, bearer token and
  contract errors (401, 403, 409, 423).
- Backend address: the app calls `/api` on its own origin; in development Vite forwards it to `STATSEAT_BACKEND`
  (default: the mock). This also lets a phone on the same network use the dev server.
- Sections in `src/sections/`, Ask Archie sheet, My ticket (drawn placeholder until the match generator prints the images), demo
  phase panel, Inspector views, SVG charts without a chart library.
- 17 Vitest tests; checked with headless Chrome at phone size with demo and silver tickets in every phase.
