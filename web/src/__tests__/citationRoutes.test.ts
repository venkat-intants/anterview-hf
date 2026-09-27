// Every citation route is a route this app actually mounts.
//
// WHY THIS TEST EXISTS. `CITATION_ROUTES` (api/agent.ts) is the one table that
// turns an agent's evidence into a link, and its Python twin
// (shared/agents/schema.py) is pinned to it by a parity test. That parity test
// compares two TABLES — it never asks whether either side names a route the
// frontend mounts. So four kinds shipped pointing at paths `App.tsx` had no
// `<Route>` for (`applicant`/`scorecard` → /hr/applicants/{id}, `interview` →
// /hr/interviews/{id}, `exam_attempt` → /hr/exams/attempts/{id}, `document` →
// /hr/library/{id}): the chip was a live link, and the page it opened was the
// 404 catch-all. A dead source chip is worse than an unlinked one — it says
// "here is the record" and then denies the record exists.
//
// HOW IT CHECKS. `App.tsx` builds its routes in JSX, not as a route table, so
// there is no object to import. The paths are read out of the source (the same
// approach CandidatePhone.test.tsx takes to its pages) and handed to
// react-router's own `matchRoutes`, so the matching semantics under test are
// the router's, not a regex of our own. The `*` catch-all is excluded
// deliberately: it matches everything, and matching ONLY it is exactly the
// defect — that is the NotFound page.
//
// This test does NOT check authorisation. Whether the matched route sits behind
// the right guard for the role that may receive that citation kind
// (CITATION_MIN_ROLES) is a separate question, pinned in RoleRoute.test.tsx and
// enforced server-side.

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { matchRoutes, type RouteObject } from 'react-router-dom';

import { CITATION_ROUTES } from '../api/agent';

/** Every `path="…"` declared in App.tsx, minus the 404 catch-all. */
function mountedRoutes(): RouteObject[] {
  const source = readFileSync('src/App.tsx', 'utf-8');
  const paths = [...source.matchAll(/path="([^"]+)"/g)].map((m) => m[1]).filter((p) => p !== '*');
  // Flat rather than nested: every path in App.tsx is absolute (the guard and
  // shell wrappers are pathless layout routes), so matching them flat is the
  // same question with less machinery.
  return paths.map((path) => ({ path }));
}

const SAMPLE_ID = '11111111-1111-4111-8111-111111111111';

/** A citation template ('/hr/library/{id}') as a concrete URL path. */
function urlFor(template: string): string {
  // `{id}` can appear twice — /hr/enrolments/{id}/evidence?decision={id} — so
  // the replacement is global. (Not `replaceAll`: this project's TS lib target
  // does not declare it.)
  const filled = template.replace(/\{id\}/g, SAMPLE_ID);
  // matchRoutes parses a string location itself, but stripping the query and
  // hash here keeps the failure message about the path that was matched.
  const [pathname] = filled.split(/[?#]/);
  return pathname;
}

describe('CITATION_ROUTES resolve to mounted routes', () => {
  const routes = mountedRoutes();

  it('reads a plausible route table out of App.tsx', () => {
    // A regex that silently stopped matching would make every assertion below
    // vacuous, so the extraction itself is pinned first.
    expect(routes.length).toBeGreaterThan(40);
    expect(routes.map((r) => r.path)).toContain('/hr/applicants');
  });

  const linked = Object.entries(CITATION_ROUTES).filter(
    (entry): entry is [string, string] => entry[1] !== null,
  );

  it.each(linked)('%s → %s opens a real screen', (kind, template) => {
    const url = urlFor(template);
    const matched = matchRoutes(routes, url);
    expect(
      matched,
      `CITATION_ROUTES.${kind} points at ${template}, and App.tsx mounts no route ` +
        `that matches ${url} — the chip is a live link to the NotFound page. Mount a ` +
        `route that opens that record rather than repointing the citation at a list.`,
    ).not.toBeNull();
  });

  it('leaves the aggregate kinds unlinked rather than pointing them at a list', () => {
    // The complement: an aggregate has no single record to open, and `null` is
    // the honest answer. Turning one into a link would make this suite pass and
    // the product worse, so the shape is pinned too.
    for (const kind of ['role_profile', 'analytics', 'audit']) {
      expect(CITATION_ROUTES[kind as keyof typeof CITATION_ROUTES]).toBeNull();
    }
  });
});
