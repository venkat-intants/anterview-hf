# ADR-FE-001 — Which frontend design system to use for new work

> Which word, button, form control, state and layout a screen uses is in
> [`docs/UI-CONVENTIONS.md`](../../../docs/UI-CONVENTIONS.md). Since 2026-10-23
> `Pill` is retired (use `Button`) and the `ui/` primitives nothing imported
> (avatar, form, label, switch, tooltip, progress, separator) are deleted.

**Status:** Accepted · **Date:** 2026-08-07 · **Closes the cheap half of** `FE-4`
(`docs/code-review-2026-08-07-domains.md`)

## Context

`web/src/` carries **three** coexisting design systems. That is a fact about the
tree, not a proposal:

| System | What it is | Consumers at HEAD |
|---|---|---|
| `src/components/ui/` | shadcn/ui — Radix-backed accessible primitives (Button, Input, Badge, Dialog, Select, Skeleton, Table, Tabs, …) | 19 files |
| `src/design/` | The "AntHire" app kit — `AppShell`, `GlassCard`, `StatCard`, `SegTabs`, `Pill`, `ToggleSwitch`, `StatusTag`, `ScoreRing`, `Reveal`, `AuroraField` | 46 files |
| `src/landing/` | A near-copy of the same kit, built for the marketing page | **1 file** — `src/pages/Landing.tsx` |

They had **three byte-identical copies of `cn()`**, `src/main.tsx` globally loads
**two** `anthire.css` stylesheets (`landing/styles/` then `design/styles/`),
and six primitive names — `GlassCard`, `Pill`, `StatCard`, `ScoreRing`,
`WaveBars`, `Marquee` — exist in both `design/` and `landing/` with **different
props** (`landing`'s `StatCard` takes `icon/value/label`; `design`'s takes
`label/value/delta/trend`). Duplicated, then diverged.

The cost is not aesthetic. A contributor importing `GlassCard` gets one of two
different components depending on the path they happened to copy, and a fix to
one is invisible to the other.

## Decision

Only one of these was cheap to fix now; the rest is a product decision about the
marketing page's look, so it is recorded rather than done.

1. **`cn()` has exactly one implementation: `src/lib/utils.ts`.**
   `src/design/lib/cn.ts` and `src/landing/lib/cn.ts` are now thin re-exports.
   The paths are kept so existing importers resolve; **new code imports
   `cn` from `@/lib/utils`.**

2. **`src/components/ui/` is canonical for interaction primitives** — anything
   that takes input, traps focus, or has ARIA semantics: buttons, inputs,
   selects, dialogs, tables, tabs, switches. These are Radix-backed and
   accessible by construction. Do **not** hand-roll an equivalent in `design/`.

3. **`src/design/` is canonical for the authenticated product's layout and
   presentation** — shell, cards, stat tiles, status tags, motion wrappers. It
   is the system every staff console and candidate page already uses (46 files
   to `components/ui`'s 19), it is the one that receives design updates, and it
   composes `components/ui` rather than replacing it (see
   `pages/superadmin/PlatformOwnerConsole.tsx`, which uses `Button`/`Input`/
   `Badge` from `components/ui` inside `GlassCard` from `design/`).

4. **`src/landing/` is frozen — marketing only.** It has exactly one consumer.
   Do not import from `@/landing/*` outside `src/pages/Landing.tsx`, and do not
   add components to it. If the landing page needs a new primitive, take it from
   `design/`.

So: **new work uses `@/design` + `@/components/ui`.** That combination is what
"the Intants frontend" means; a fourth system is not needed to add a component.

## Consequences

- A new component has an unambiguous home, which is the whole point — this ADR
  exists so the next contributor does not add a fourth system by copying the
  nearest file.
- `cn` behaviour can no longer drift between systems.
- Nothing was rewritten. Existing components keep their current imports; this is
  a rule for what comes next, not a migration.

## Explicitly NOT decided here

These need a product call on how the marketing page should look, and a
non-trivial migration, so they stay open under `FE-4`:

- Merging the two `anthire.css` stylesheets that `main.tsx` loads globally.
  They are 164 (landing) and 53 (design) lines and the load order is
  load-bearing — `main.tsx` documents that `design/` must win on name overlap.
- Collapsing the six diverged primitive pairs into one implementation each.
  Their props differ, so this is a rewrite of `pages/Landing.tsx`, not a
  find-and-replace.
- Retiring `src/landing/` entirely. Freezing it (rule 4) bounds the damage
  without spending that budget now.

---

## Addendum — 2026-10-12: the visual identity changed, and rule 4 was overridden once

This records what the UI revamp on `feat/ui-revamp-handshake` changed about the
decisions above. Rules 1–3 stand unchanged.

**One identity, defined in one place.** The palette, type, radii and elevation
now live in `src/index.css` as CSS variables, with a light value and a dark
value under the same names. Light is the default: cool neutral canvas, white
cards, slate ink, and **one blue** that is the primary action, the focus ring,
links and — as a pale tint — the highlight (`--ui-highlight`, a fill with its
own ink, valued per mode, so never assume it is light). A fixed deep-blue
gradient (`--ui-grad-brand`, white text) is the brand surface: the logo mark,
the sign-in panel, and at most one promo card per screen. Colour beyond blue
means status only. The header comment in `index.css` is the reference; do not
restate the values elsewhere, because a second copy is the one that goes stale.

This paragraph first described a warm-paper canvas with a violet accent and a
lime highlight. That identity shipped to nobody: it was replaced by the blue
one on the same branch, the same day, at the owner's request.

**Type is self-hosted.** Inter, Plus Jakarta Sans and JetBrains Mono are served
from `web/public/fonts/` through `src/fonts.css` (latin and latin-ext subsets;
licence texts alongside). No page requests anything from Google Fonts.

**Screens are assembled from `src/design/components/page.tsx`.** `PageHeader`,
`SectionCard`, `EmptyState`, `ProgressMeter`, `Breadcrumbs`, `Stepper`,
`Toolbar` and `DataList` are the page-level building blocks. A detail page
carries breadcrumbs, a list carries a toolbar, a record's facts are a
`DataList`, a multi-step flow shows a `Stepper`. A new screen starts from these rather than hand-rolling a header, a card
shell or an empty placeholder.

**Removed.** `src/components/fx/`, `src/components/motion/` and
`src/design/components/AuroraField.tsx` (nothing imported them), the
`animate.css` dependency, the `cluely-*` Tailwind colours and shadows, and the
Geist and EB Garamond font downloads. `GlassCard` keeps its name because 100+
files import it, but it is now an ordinary bordered card: no glass, no glow.

**Rule 4 ("`src/landing/` is frozen") was deliberately overridden for this
change.** The landing page was restyled so that it matches the product a visitor
signs into. Its `--lp-*` variables in `landing/styles/anthire.css` are now
aliases of the app tokens above rather than a separate palette, which is what
keeps the two from drifting again. The rule is back in force: `src/landing/`
still has exactly one consumer, still must not be imported elsewhere, and new
primitives still belong in `design/`.

**Still open under `FE-4`.** `main.tsx` still loads two `anthire.css`
stylesheets (now 51 and 63 lines), and `landing/components/primitives.tsx` still
duplicates some names from `design/components/primitives.tsx`. Whether the load
order between the two stylesheets is still load-bearing after the landing
rewrite is **unverified**.

---

## Layout and flow blocks (round 3)

Added 2026-10-12. All live in `src/design/components/`; none changes the look of
a page that does not use it. `GlassCard`, `SectionCard` and `StatCard` are
untouched (their class output is pinned by `__tests__/DesignWrappers.test.tsx`).

**Frame** (`page.tsx`)
- `Page` — `width="form|default|workspace|reading"` = 880 / 1200 / full / 720px.
  Left-anchored (only `reading` centres), `flex-col gap-6`, adds no padding.
- `PageBody` — main column plus an optional 320px `aside` at `lg+`; single
  column without one. Both columns are on the 24px rhythm.
- `PageHeader` — gains `meta`. Inside a `Page` its `mb-6` is cancelled; outside
  one it is unchanged.
- `Stepper` gains `compactLabel`; `EmptyState` gains `size="row"` and
  `bordered` (defaults render exactly as before).

**Containers**
- `card.tsx` `Card` — `emphasis` plain (default) / raised / feature, `density`
  compact / default / roomy, slots `eyebrow title description icon actions
  footer`, `flush`, `divided`. With `to` and no `actions` the whole card is one
  link; with both, the title is a stretched link and actions sit above it.
- `rows.tsx` `Section` (titled group, hairline, no box), `Well` (inset fill, no
  border), `RowList` / `Row` (divided rows, at least 56px, optional link).
- `stat-group.tsx` `StatGroup` / `Stat` — one surface of divided cells, two-up on
  phones. The group renders nothing when every `value` is null or undefined; a
  single missing value shows "—" with an `aria-label`.

**Flow**
- `next-step.tsx` `NextStep` — the one next step for a page, one action slot, no
  button when `action` is absent. Also `ActionRow`, `TurnBadge` (icon + required
  `label`), `StepContext`, `WhatNext`. Every visible word is a prop.
- `journey-rail.tsx` `JourneyRail` — ordered stages whose state is a server fact;
  rail + panel at `lg+`, accordion below. `stateLabels` is required.
- `approval-track.tsx` `ApprovalTrack` — approval steps with actor and date, the
  current actor named, a returned step's note linked by `aria-describedby`.

**Disclosure** (`collapsible.tsx`, round 4)
- `Collapsible` — one disclosure for every activity / history / timeline
  section: `<button aria-expanded aria-controls>` in a heading, a labelled
  region, content not rendered while closed. Closed, the header shows the count
  and the newest entry (`12 events · latest: …`). `persistKey` remembers the
  choice in `localStorage` (every access in try/catch; build the key with
  `hooks/useScopedPersistKey` so it is per person). Height animates with a
  grid-rows transition and is instant under `prefers-reduced-motion` or without
  `matchMedia`. `variant="card"` is its own surface, `"bare"` sits in a card.
- `ProgressiveList` — the first five items, then "Show all n". Its
  `renderItem` returns the `<li>`.
- Secondary records (activity, history) are collapsed by default; a section the
  page exists for is not.

**Rules** (from the round 3 spec)
- `AppShell`'s `<main>` owns the only gutters. An in-shell page renders
  `<Page>` and never sets `px-*`, `py-*`, `mx-auto` or `max-w-*` on its wrapper.
- Region order: `PageHeader`, at most one `NextStep`, then `PageBody`.
- A restructured page has one `raised` or `feature` region; everything else is
  `plain`. No card inside a card: use `Well`, `Section` or `RowList`.
- A state is never shown by colour alone: an icon and a word, both from props.
- Layout gaps are 8 / 12 / 16 / 24 / 40px (`gap-2/3/4/6/10`).
- `lib/queryKeys.ts` is the one place the opening's query keys are spelled.

## Dropdowns (2026-10-13)

One closed-state design covers every dropdown trigger: 40px (36px compact), 10px radius,
`--input` border strengthening on hover, card fill, 14px text, 16px chevron 12px from the edge,
accent focus ring, disabled and `aria-invalid` states.

- **Native `<select>`**: styled for EVERY select by the base layer in `index.css` (no class needed).
  The rules are `select:where(...)`, so any Tailwind class a call site passes still wins; only the
  end padding that clears the chevron is forced. Where the browser supports
  `appearance: base-select` (Chromium 135+, Edge) the OPEN list is styled like our menus; Firefox and
  Safari keep the refined closed state and the OS list (intended fallback). `select[multiple]` and
  `select[size]` are excluded. The `@supports` block must stay OUTSIDE `@layer`: Tailwind hoists an
  `@layer` out of an `@supports` and drops the condition (guarded by `SelectControl.test.tsx`).
- **New code**: `import { SelectControl } from '@/components/ui/select-control'` (props `size`, `invalid`,
  `leadingIcon`, full ref/prop pass-through), or `SelectField` from the design primitives for a
  labelled field with hint/error wiring.
- **Menus and popovers**: `ui/dropdown-menu.tsx` and `ui/popover.tsx` share one surface
  (`ui/menu-styles.ts`): 12px radius, hairline, elevated shadow, 4px padding, 36px items. Use them
  for anything that opens a list; do not hand-roll a button plus an absolutely positioned div (no
  Escape, no arrow keys, no focus return).
