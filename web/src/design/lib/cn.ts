// Re-export only. The implementation lives in `src/lib/utils.ts` (FE-4).
//
// This file used to hold its own `twMerge(clsx(...))` copy, as did
// `src/landing/lib/cn.ts` — three identical implementations that could drift
// apart silently, because a change to Tailwind class-merge behaviour in one
// design system would leave the other two unchanged and nothing would fail.
// The module path is kept so the ~4 importers under `src/design/` (and any
// external doc referencing it) keep resolving; only the definition moved.
//
// New code should import `cn` from `@/lib/utils` directly — see
// `src/design/README.md` for which design system is canonical.
export { cn } from '@/lib/utils';
