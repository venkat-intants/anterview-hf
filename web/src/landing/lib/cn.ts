// Re-export only. The implementation lives in `src/lib/utils.ts` (FE-4).
//
// See the note in `src/design/lib/cn.ts`: this was the third byte-identical
// copy of `twMerge(clsx(...))`. The module path is kept so the importers under
// `src/landing/` keep resolving; only the definition moved.
//
// New code should import `cn` from `@/lib/utils` directly — see
// `src/design/README.md` for which design system is canonical.
export { cn } from '@/lib/utils';
