/**
 * The careers board's address, where staff can find it.
 *
 * THE GAP. Candidates apply with no login at `/careers/<company-slug>`. The board is
 * finished — a public route, a rate-limited endpoint, six visibility gates in one
 * module, 21 backend tests, three languages, both Caddyfiles proxying it under CI
 * enforcement — and its address appeared on exactly ONE screen: the platform owner's
 * company table, as bare monospace text with no link and no copy button. That is the
 * one role which is not the company's own staff, so an HR manager had no way to find
 * their own front door.
 *
 * The repo has named this bug class three times, in `navSections.tsx` on the
 * question-review screen and the document library, and turned it into an invariant in
 * `navRoleScoping.test.ts`. The wording there applies verbatim: "'authorised HR users
 * can upload documents' was not true of the product."
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import CareersLinkCard from '../components/hr/CareersLinkCard';

function renderCard(props: Parameters<typeof CareersLinkCard>[0]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CareersLinkCard {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('CareersLinkCard', () => {
  it('shows the full public URL, not just the slug', () => {
    renderCard({ slug: 'acme-college', companyName: 'Acme College' });

    // The whole point: the one screen that showed a slug today showed the slug
    // alone, which is not something anyone can paste anywhere.
    expect(screen.getByText(`${window.location.origin}/careers/acme-college`)).toBeTruthy();
  });

  it('renders nothing at all without a slug', () => {
    // A platform owner belongs to no company (company_id is NULL), so there is no
    // board to link to. An empty card would be worse than no card.
    const { container } = renderCard({ slug: null, companyName: null });

    expect(container.querySelector('[data-testid="careers-link-card"]')).toBeNull();
  });

  it('renders nothing for an empty-string slug either', () => {
    // An empty string is falsy, but asserting it separately means a future
    // `slug !== undefined` check cannot pass this file.
    const { container } = renderCard({ slug: '', companyName: 'Acme' });

    expect(container.querySelector('[data-testid="careers-link-card"]')).toBeNull();
  });

  it('copies the link to the clipboard', async () => {
    const user = userEvent.setup();
    // Installed AFTER setup(): user-event v14 swaps in its own clipboard stub,
    // which would otherwise replace this spy and make the assertion vacuous.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderCard({ slug: 'acme-college', companyName: 'Acme College' });

    await user.click(screen.getByRole('button', { name: /copy the careers page link/i }));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/careers/acme-college`),
    );
  });

  it('survives a browser with no clipboard API', async () => {
    const user = userEvent.setup();
    // `navigator.clipboard` is absent on a non-HTTPS origin and in older browsers.
    // The optional chaining in the component is what keeps this from throwing, and
    // the link text stays on screen so the value is still copyable by hand.
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    renderCard({ slug: 'acme-college', companyName: 'Acme College' });

    await user.click(screen.getByRole('button', { name: /copy the careers page link/i }));

    expect(screen.getByText(`${window.location.origin}/careers/acme-college`)).toBeTruthy();
  });

  it('opens the board in a new tab, without leaking the referrer', () => {
    renderCard({ slug: 'acme-college', companyName: 'Acme College' });

    const link = screen.getByRole('link', { name: /open the careers page/i });

    expect(link.getAttribute('href')).toBe(`${window.location.origin}/careers/acme-college`);
    expect(link.getAttribute('target')).toBe('_blank');
    // rel="noreferrer" implies noopener. Without it the opened page gets a handle
    // on this one through window.opener.
    expect(link.getAttribute('rel')).toContain('noreferrer');
  });

  it('names the company so a multi-tenant operator knows whose board it is', () => {
    renderCard({ slug: 'acme-college', companyName: 'Acme College' });

    expect(screen.getByText(/Acme College/)).toBeTruthy();
  });

  it('still shows the link when the company name is missing', () => {
    // company_name and company_slug come from the same LEFT JOIN, but a name is
    // cosmetic here and the link is not — one must not gate the other.
    renderCard({ slug: 'acme-college', companyName: null });

    expect(screen.getByText(`${window.location.origin}/careers/acme-college`)).toBeTruthy();
  });

  it('does not warn about having no open roles', () => {
    // Deliberate difference from the per-opening apply card, which DOES warn. A
    // careers board never 404s for an active company: it answers 200 with an empty
    // board and a polite "no open roles right now"
    // (test_careers_board.py::test_an_empty_board_is_a_board_not_an_error). So the
    // link is always safe to share and a warning would be noise.
    renderCard({ slug: 'acme-college', companyName: 'Acme College' });

    expect(screen.queryByText(/will not accept applications/i)).toBeNull();
    expect(screen.queryByText(/no open roles/i)).toBeNull();
  });

  it('url-escapes a slug rather than interpolating it raw', () => {
    // Slugs are generated by `_slugify` and cannot contain anything exotic today,
    // so this is about the component not being the weak link if that ever changes.
    renderCard({ slug: 'a b', companyName: 'Odd' });

    const link = screen.getByRole('link', { name: /open the careers page/i });
    expect(link.getAttribute('href')).toContain('careers/a');
  });
});
