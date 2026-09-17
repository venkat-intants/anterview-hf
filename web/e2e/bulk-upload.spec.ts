// A stack of CVs, dropped into an opening (E5).
//
// This is how a hiring team with a shared inbox actually starts: not one
// candidate applying, but forty resumes to file at once. The work happens in the
// background, so what matters on screen is that HR can see it happening, and
// that a file the parser cannot read is NAMED rather than silently dropped —
// a resume that vanishes is a person who never hears back.

import {
  Api,
  createOpening,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import { makeCvPdf } from './support/pdf';

test.describe('bulk upload', () => {
  test('files a batch of CVs into one opening, and names the one it could not read', async ({
    page,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Bulk Upload');
    const stamp = Date.now().toString(36);

    await signIn(page, tenant.accounts.hr_manager);
    await page.goto('/hr/applicants');

    await page.getByLabel('Opening').selectOption({ label: `${opening.title} · mid` });
    await page.getByLabel('Resume PDFs').setInputFiles([
      ...['Anaya', 'Bilal', 'Charu'].map((name) => ({
        name: `${name.toLowerCase()}-${stamp}.pdf`,
        mimeType: 'application/pdf',
        buffer: makeCvPdf([
          `${name} ${stamp}`,
          `${name.toLowerCase()}.${stamp}@e2e-anthire.com`,
          'Python FastAPI PostgreSQL pytest',
          'E2E-SCORE: 71',
        ]),
      })),
      // A PDF only by its name — a scan, a renamed Word file, a truncated
      // download. The batch must carry on and say which one failed.
      {
        name: `not-really-a-pdf-${stamp}.pdf`,
        mimeType: 'application/pdf',
        buffer: Buffer.from('This is not a PDF at all.', 'utf8'),
      },
    ]);

    await expect(page.getByText('4 resumes selected')).toBeVisible();
    await page.getByRole('button', { name: 'Upload 4 resumes' }).click();

    // ── The panel follows the batch, naming the opening it is filing into ────
    const panel = page.getByText(
      new RegExp(`(Processing upload|Upload finished) — ${opening.title}`),
    );
    await expect(panel).toBeVisible();

    // Reading and scoring happen in the reconciler; run it rather than wait.
    await runBackgroundPasses(request);
    await expect(page.getByText(`Upload finished — ${opening.title}`)).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText('4 of 4 files read')).toBeVisible();

    // ── Three filed, one reported — by name, with the reason ────────────────
    await expect(page.getByText(/3 added/)).toBeVisible();
    await expect(page.getByText(/1 failed/)).toBeVisible();
    await expect(
      page.getByText(new RegExp(`not-really-a-pdf-${stamp}\\.pdf`)),
      'the file that could not be read is named on screen',
    ).toBeVisible();

    // ── And the three are really in the opening ─────────────────────────────
    const enrolled = await api.get<{ full_name: string }[]>(
      `/hr/requisitions/${opening.id}/enrolments`,
    );
    expect(enrolled.map((e) => e.full_name).join(' ')).toContain(stamp);
    expect(enrolled, 'the unreadable file created nobody').toHaveLength(3);
  });
});
