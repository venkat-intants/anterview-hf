// Where an application came from (PH3-B1).
//
// A recruiter posts the same opening to several places and tags each link:
// `/apply/<id>?src=linkedin`. The channel is recorded against the application
// so the funnel can be counted later, and the page's only job is to carry the
// tag through — normalising it in the browser would put a second, disagreeing
// vocabulary in front of the one the database keeps.
//
// The two things a browser can actually prove here, and both matter: a tagged
// link still produces an application (a campaign tag must never cost a real
// applicant), and the channel is never shown to the person being tracked.
//
// What HR sees of it: nothing, deliberately and by design — the channel feeds
// PH5's funnel reporting, which is not built. So this spec reads the channel
// back from the same public endpoint the page reads, rather than pretending
// there is a screen.

import { API_URL } from './support/env';
import { expect, test } from './support/fixtures';
import { Api, createLiveMcqOpening } from './support/fixtures';
import { aCandidate, applyThroughPublicForm } from './support/journeys';

interface Posting {
  source: string;
  source_detail: string | null;
}

test.describe('where an application came from', () => {
  test('is taken from the link, kept off the candidate’s screen, and never costs an applicant', async ({
    page,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Apply Source');

    // ── The same posting, read the way each link arrives ────────────────────
    const posting = async (src?: string): Promise<Posting> => {
      const query = src === undefined ? '' : `?src=${encodeURIComponent(src)}`;
      const res = await request.get(`${API_URL}/apply/${opening.id}${query}`);
      expect(res.ok(), `the posting is public: ${res.status()}`).toBeTruthy();
      return (await res.json()) as Posting;
    };

    const untagged = await posting();
    expect(untagged.source, 'no tag is "direct" — they came to us').toBe('direct');
    expect(
      untagged.source,
      'and never "unknown", which is reserved for rows nobody was tracking',
    ).not.toBe('unknown');

    // A named board is a job board, and WHICH board is the detail — so the
    // channel stays a countable vocabulary instead of growing a new value
    // every time a recruiter posts somewhere new.
    const board = await posting('linkedin');
    expect(board.source, 'a named board is the job_board channel').toBe('job_board');
    expect(board.source_detail, 'and which board it was is kept').toBe('linkedin');

    // A recruiter invents a campaign tag nobody added to the vocabulary. It is
    // kept rather than refused: losing a real applicant to a typo in a URL
    // would be far worse than an uncounted campaign.
    const campaign = await posting('Autumn-Drive-2026');
    expect(campaign.source, 'an unrecognised tag is recorded as other').toBe('other');
    // Folded, not raw: hyphens, spaces and dots all become underscores and the
    // case is dropped, so one campaign written three ways in three adverts
    // still counts as one campaign.
    expect(campaign.source_detail, 'with the tag kept, so it can be counted later').toBe(
      'autumn_drive_2026',
    );
    expect(
      (await posting('autumn drive 2026')).source_detail,
      'the same campaign, spelled with spaces, is the same campaign',
    ).toBe('autumn_drive_2026');

    // A link cannot claim to be untracked: "unknown" is reserved for the rows
    // that pre-date tracking, and a tagged arrival must not hide among them.
    expect((await posting('unknown')).source, 'a link cannot claim to be untracked').toBe(
      'direct',
    );

    // ── A tagged link is still an ordinary application ──────────────────────
    const candidate = aCandidate('Ravi', 77);
    await applyThroughPublicForm(page, opening.id, candidate, { src: 'linkedin' });

    // ── And the candidate is never shown how they were counted ──────────────
    // They are being tracked, not addressed: a tracking label on the page they
    // read would be both odd and, on a shared screen, revealing.
    // The tag and the channel it became, not the English words: "source" is a
    // substring of "resources", and a test that fails on that is testing the
    // dictionary rather than the product.
    const shown = (await page.locator('body').innerText()).toLowerCase();
    for (const trace of ['linkedin', 'job_board', 'job board', 'utm_']) {
      expect(shown, `the candidate is never shown "${trace}"`).not.toContain(trace);
    }
    // The tag stays in the address bar, and that is fine: it is the link the
    // candidate themselves clicked, and it names the recruiter's campaign, not
    // the candidate. Only a credential has to be stripped from a URL.
  });
});
