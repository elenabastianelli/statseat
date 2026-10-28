# statseat PWA (`web/`)

The fan app of InsideTheGame: React 19 + Vite + TypeScript, installable PWA, mobile-first. It talks only to the
statseat API described in [`app/contract/`](../app/contract/README.md) (contract v0.3.0).

## Run

```bash
# 1. the backend, from the repo root: the match engine or the API contract mock, both on port 8000
.venv/bin/python -m app.engine                 # real backend: replays SYN-001 at 10x from pre-match (about 30 s to load)
.venv/bin/python -m app.contract.mock          # or: fixed fake data (MOCK_PHASE=half_time by default)

# 2. the app (Node 22), from web/
npm install
npm run dev                                    # http://localhost:5173 (and the Network address for a phone)
```

Log in with a code printed on the SYN-001 tickets ([`synthetic/SYN-001/tickets/README.md`](../synthetic/SYN-001/tickets/README.md),
e.g. a DEMO code for the phase panel and Inspector); with the mock, use the fake codes of
[`app/contract/README.md`](../app/contract/README.md#tickets). Other commands: `npm test` (Vitest), `npm run build` (type check + production build with
the service worker), `npm run gen:api` (regenerate `src/api/schema.d.ts` after a contract change).

## Backend address: one place

The app always calls `/api/...` on its own origin (`src/config.ts`). In production the FastAPI container serves both
the PWA and the API. In development Vite forwards `/api` to `STATSEAT_BACKEND` (default `http://127.0.0.1:8000`, the
mock): to use the real backend, put `STATSEAT_BACKEND=<url>` in `web/.env.local` (see `.env.example`). Same origin
also makes the dev server usable from a phone on the same network.

## What it does

| Feature | Where |
|---|---|
| Login with the ticket code and a random device id (409 on a second device for silver and gold) | `components/Login.tsx`, `api/client.ts` |
| Tier rules read from the session: mode switch only if `allowed_modes` has more than one mode; Inspector and phase panel only for demo (the judges use demo tickets) | `components/Shell.tsx`, `lib/phases.ts` |
| Sections by phase from `GET /api/match/state` (polled every 5 s); the app opens the section of the new phase; locked tabs say when they open | `components/Shell.tsx` |
| Pre-match (line-ups, officials, follow players), Live, Data & Statistics (charts in the backend's order), Games (half-time only: list, the sponsor's prize, leaderboard), Recap | `sections/` |
| Ask Archie, always on screen; "Open the chart" goes to the chart Archie points to. Archie (approved by the team on 25/10) stands on the top edge of the chat and rises when it opens (no animation with reduced motion); his head is the button and avatar icon | `components/ArchieSheet.tsx`, `public/archie-character.svg`, `public/archie.svg` |
| Account (person icon in the top bar): My ticket from `GET /api/ticket` (image from `/api/ticket/image`, drawn placeholder until the match generator prints it), the language, the **3 favourite players** (chosen there or with the stars of Pre-match, at most 3, a clear warning, then confirmed once and locked: contract v0.2.6) and the **final report in PDF** (`GET /api/recap/pdf`, built by the server; the button opens after the final whistle) | `components/Account.tsx`, `lib/favourites.ts` |
| Metrics that come back `null` (contract v0.2.1) show "Not available" and the reason from `unavailable` | `components/Status.tsx` |
| UI in eight languages from a menu (English by default; French, Spanish, German, Italian, Dutch, Chinese, Russian translated once with a model from `en.json` and reviewed); every phase, section, tier, mode and event of the contract has a label, and every language has the keys and variables of English (tested) | `i18n/`, `components/LanguageSwitch.tsx` |
| Pre-match: both starting line-ups drawn on one pitch (home at the bottom, away at the top, captain and followed players marked), then the full lists with the star to follow a player; the match overview is cached (network first), so Pre-match opens even with a weak signal | `sections/PreMatch.tsx`, `components/LineupPitch.tsx`, `lib/formation.ts`, `vite.config.ts` |
| Live (during play only): the key numbers per team (goals, shots, on target, penalties, yellow and red cards), highlights and the event timeline. Data & Statistics from kick-off: tables first, then the charts of the mode; in Casual the Analyst charts follow, blurred ("Switch to Analyst" on gold, "Unlock with Gold" on silver) | `sections/Live.tsx`, `sections/DataStats.tsx` |
| Club crests on the scoreboard and on each Pre-match team card, from `GET /api/crests/{club_id}` (public image, alt text the team name); if the image does not load, a shield in the club colours with three letters | `components/Crest.tsx` |
| Tabs: the match first (Pre-match, Live, Data & Statistics, Recap), Games last and smaller | `lib/phases.ts` |
| The QR link of a ticket (`…?ticket=<code>`) opens the login with the code already filled in; the code is then removed from the address bar | `components/Login.tsx` |
| App name statseat (`APP_NAME` in `src/config.ts`) and its logo `public/icon-512.png`, a fan in the stands surrounded by data; the PNG icons for iPhone (`apple-touch-icon.png`) and Android (192 and maskable) are made from it with `../generator/.venv/bin/python scripts/gen_pwa_icons.py` | `config.ts`, `scripts/gen_pwa_icons.py` |

## Look and icons

- **Theme**: colours as variables at the top of `src/index.css`, inspired by a floodlit night match
  (deep violet, electric green) and by Microsoft's Fluent design (clear blue, quiet neutrals), with no logo, name or
  typeface of either. Base text is 17 px with high contrast, for use in the sun. `--bg` must stay equal to `BG` in
  `src/lib/colours.ts`.
- **Background**: `public/pitch-bg.svg`, a pitch drawn by us, blurred, faded and tilted away behind the content.
- **Icons**: Fluent UI System Icons (Microsoft, MIT licence), no emoji. `npm run gen:icons` copies the path data of
  the icons listed in `scripts/gen-icons.mjs` from the dev dependency `@fluentui/svg-icons` into
  `src/components/icons.generated.ts`; use them with `<Icon name="…" />` (`src/components/Icon.tsx`).
- **Flags**: the same script copies the country flags of the players' nationalities (listed in
  `scripts/gen-icons.mjs`) from the dev dependency `country-flag-icons` (MIT licence) into
  `src/components/flags.generated.ts`, as images, not emoji, so Windows draws them too. `<Flag country="…" />`
  (`src/components/Flag.tsx`) shows them in Tic Tac Goal; the fictional home country has no flag and gets the globe.

## Not yet

- Game screens (on the same endpoints) and the new Data & Statistics screens of contract v0.3.0 (the Data & Statistics screens: mirror chart, Player Focus, tables of all players). Polls were dropped on 25/10 (contract v0.2.3); crowd noise and the pass network too (v0.2.5); the 2D replay is a roadmap item (v0.3.0).
- Archie is final: `public/archie-character.svg` and its head `public/archie.svg`, changed together.
