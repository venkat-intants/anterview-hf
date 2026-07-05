# Intants Web — React PWA

React 18 + TypeScript + Vite PWA for the Intants AI Interview platform.

## Prerequisites

- Node.js 20+
- npm 10+

## Running locally

```bash
cd web
npm install
npm run dev
```

App serves at http://localhost:5174.

## Mock vs real API

By default, all API calls use the mock layer (no backend required).
To switch to the real `data_gateway` backend (port 8002):

```bash
# In web/.env
VITE_USE_MOCK=false
```

Leave it as `VITE_USE_MOCK=true` (or unset) while `data_gateway` is being built.

## Scripts

| Command | Description |
|---|---|
| `npm run dev` | Start Vite dev server on :5174 |
| `npm run build` | TypeScript check + Vite production build |
| `npm run preview` | Serve production build locally |
| `npm run test` | Run Vitest (non-watch) |
| `npm run typecheck` | `tsc --noEmit` strict check |
| `npm run lint` | ESLint (max-warnings 0) |
| `npm run format` | Prettier write |

## Auth flow

- `access_token` stored in React Context (in-memory) — never localStorage
- Unauthenticated `/dashboard` redirects to `/login`
- Token is lost on page refresh (Sprint 2 will add silent refresh via cookie)

## i18n

EN/HI/TE via `react-i18next`. Keys live in `src/i18n/`. (Wired in Sprint 2.)
