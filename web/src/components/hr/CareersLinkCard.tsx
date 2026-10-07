/**
 * The company's public careers board address, where staff can actually find it.
 *
 * THE GAP THIS CLOSES. Candidates apply with no login at `/careers/<company-slug>`.
 * The board is finished: a public route, a rate-limited endpoint, six visibility
 * gates in one module, 21 backend tests, three languages, and both Caddyfiles
 * proxying it under CI enforcement. Its address appeared on exactly ONE screen —
 * the platform owner's company table, as bare monospace text with no link and no
 * copy button — i.e. for the one role that is not the company's own staff. An HR
 * manager had no way to discover their own front door.
 *
 * The repo has named this bug class three times and turned it into an invariant:
 * `navSections.tsx` on the question-review screen ("undiscoverable, which defeats a
 * review gate nobody can find"), on the document library ("'authorised HR users can
 * upload documents' was not true of the product"), and `navRoleScoping.test.ts`
 * which now fails the build for a routed, role-gated screen with no way in.
 *
 * WHY IN-PAGE AND NOT A NAV ENTRY. `navRoleScoping.test.ts` asserts every nav item's
 * path starts with its section's prefix, with one hard-coded exception. A
 * `/careers/...` entry would need adding to that exception list, weakening a
 * deliberate guard — and this is a per-company VALUE to copy, not a console route.
 *
 * ENGLISH LITERALS, NO LOCALE KEYS. Staff consoles are English-only by design
 * (CLAUDE.md constraint 5; 1 of 30 files under pages/hr/ uses `useTranslation`), and
 * adding EN-only keys would fail the EN/HI/TE parity test for hi and te.
 */
import { Copy, ExternalLink, Globe } from '@/design/components/icons';
import { GlassCard } from '@/design/components/primitives';
import { toast } from '@/lib/toast';

export default function CareersLinkCard({
  slug,
  companyName,
  className,
}: {
  /** The company's careers slug, from `GET /auth/me`. Nothing renders without it. */
  slug?: string | null;
  companyName?: string | null;
  className?: string;
}) {
  // A platform owner belongs to no company, so there is no board to link to.
  // Rendering an empty card would be worse than rendering nothing.
  if (!slug) return null;

  const link = `${window.location.origin}/careers/${slug}`;

  return (
    <GlassCard className={className ?? 'p-5'} data-testid="careers-link-card">
      <div className="flex items-center gap-2">
        <Globe className="h-4 w-4 text-[var(--ui-faint)]" aria-hidden="true" />
        <span className="text-[14px] font-medium text-foreground">Your careers page</span>
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
        Every opening you publish appears here. Candidates can browse and apply without
        an account — share it on your site, in a post, or on a printed notice.
      </p>

      <div className="mt-3 flex items-center gap-2 rounded-[10px] border border-border bg-black/25 px-3 py-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[var(--ui-soft)]">
          {link}
        </span>
        <button
          type="button"
          onClick={() => {
            // Optional chaining is not defensive clutter: navigator.clipboard is
            // absent on a non-HTTPS origin and in older browsers. The link stays
            // visible above either way, so a failed copy is still a usable screen.
            void navigator.clipboard?.writeText(link);
            toast.success('Link copied');
          }}
          className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"
          aria-label="Copy the careers page link"
        >
          <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <a
          href={link}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"
          aria-label="Open the careers page"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </a>
      </div>

      {/* No "you have no open roles" warning here, unlike the per-opening apply
          card. A careers board NEVER 404s for an active company — it answers 200
          with an empty board and a polite "no open roles right now"
          (test_careers_board.py::test_an_empty_board_is_a_board_not_an_error). So
          this link is always safe to share, and a warning would be noise. */}
      {companyName ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Public board for {companyName}.
        </p>
      ) : null}
    </GlassCard>
  );
}
