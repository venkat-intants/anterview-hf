# ADR-FE-001 — Which frontend design system to use for new work

**Status:** Accepted · **Date:** 2026-08-07 · **Closes the cheap half of** `FE-4`
(`docs/code-review-2026-08-07-domains.md`)

## Context

`web/src/` carries **three** coexisting design systems. That is a fact about the
tree, not a proposal:

| System | What it is | Consumers at HEAD |
|---|---|---|
| `src/components/ui/` | shadcn/ui — Radix-backed accessible primitives (Button, Input, Badge, Dialog, Select, Skeleton, Table, Tabs, …) | 19 files |
| `src/design/` | The "Anterview" app kit — `AppShell`, `GlassCard`, `StatCard`, `SegTabs`, `Pill`, `ToggleSwitch`, `StatusTag`, `ScoreRing`, `Reveal`, `AuroraField` | 46 files |
| `src/landing/` | A near-copy of the same kit, built for the marketing page | **1 file** — `src/pages/Landing.tsx` |

They had **three byte-identical copies of `cn()`**, `src/main.tsx` globally loads
**two** `anterview.css` stylesheets (`landing/styles/` then `design/styles/`),
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

- Merging the two `anterview.css` stylesheets that `main.tsx` loads globally.
  They are 164 (landing) and 53 (design) lines and the load order is
  load-bearing — `main.tsx` documents that `design/` must win on name overlap.
- Collapsing the six diverged primitive pairs into one implementation each.
  Their props differ, so this is a rewrite of `pages/Landing.tsx`, not a
  find-and-replace.
- Retiring `src/landing/` entirely. Freezing it (rule 4) bounds the damage
  without spending that budget now.
