# Browser E2E — currently empty, on purpose

`text-interview.spec.ts` was deleted (2026-08-03). It described a **text**
interview — register → consent → five typed turns into `#candidate-input` → a
complete screen — and that product no longer exists: `src/pages/Interview.tsx`
is a LiveKit voice + video page rendering `InterviewIntro` + `LiveKitInterview`.
Every selector it used had also gone stale (`#full_name` / `#email` /
`#password` — `Register.tsx` labels its inputs by placeholder now; there is no
`#candidate-input` anywhere in `src/`).

Nothing ran it (no workflow references Playwright; `npm run e2e` is manual), so
it failed on its first line while reading as end-to-end coverage. A spec that
cannot pass is worse than no spec — an engineer told to "run the E2E smoke
before release" debugs a working app.

## What a real rewrite needs first

1. **Stable hooks in `web/src`.** Assert on `data-testid`, not ids the styling
   pass keeps deleting. The registration form and the LiveKit interview page
   both need them added.
2. **Fake media.** The interview leg needs a mic/camera:
   `--use-fake-device-for-media-stream --use-fake-ui-for-media-stream`, plus a
   LiveKit room the CI network can reach.
3. **A budget decision.** One full run hits real Gemini + Sarvam + the avatar
   provider (~₹1/run, and it burns Tavus avatar minutes). Keep it off `push`;
   `workflow_dispatch` only, per the cost cap in CLAUDE.md.

Until (1)–(3) are settled, the interview path is covered by the service-level
tests under `services/*/tests/`, not from a browser.

`web/playwright.config.ts`, the `e2e` script and the `@playwright/test`
devDependency are still in place for that rewrite.
