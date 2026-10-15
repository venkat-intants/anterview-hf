// Class strings shared by the account screens (see AuthLayout.tsx).

/** Link styled as the ink pill, for the one primary action on a status page. */
export const PRIMARY_LINK_CLASS =
  'inline-flex w-full items-center justify-center gap-2 rounded-full bg-primary px-5 py-3 text-[14px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:w-auto';

/** Quiet text link for the secondary route out of a screen. */
export const TEXT_LINK_CLASS =
  'inline-flex min-h-10 items-center gap-1.5 rounded text-[14px] font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
