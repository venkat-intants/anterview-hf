// Pin: each role sees EXACTLY its own nav sections — no more, no less.
//
// Why this test exists. `CANDIDATE_NAV` used to render unconditionally for every
// authenticated user, so an HR manager, super admin and platform owner were all
// shown the candidate's own tools: "Resume" (upload your CV), "History" (your
// interview history) and "Jobs" (browse and apply). Nothing was insecure — those
// routes sit behind `ProtectedRoute`, i.e. authentication only, and the server
// enforces authorisation independently — but staff were handed links to pages
// that are empty for them by construction.
//
// The assertions below are COMPLEMENTS, not samples: for every role we assert
// both the sections it must see and that it sees no other. That is what makes
// this "exactly the right nav" rather than "at least the right nav" — a new
// section added without a visibleTo predicate fails here rather than quietly
// appearing for everyone, which is precisely how the original bug shipped.
//
// This is a PRESENTATION control. It is deliberately not a security boundary:
// see RoleRoute.test.tsx for the client-side route guards, and the service test
// suites for the server-side checks that actually enforce access.

import { describe, expect, it } from 'vitest';

import {
  homePathFor,
  NAV_SECTIONS,
  type NavSection,
  visibleNavSections,
} from '../components/layout/navSections';

/** Section ids in render order, for a given role set. */
const idsFor = (roles: string[]): string[] => visibleNavSections(roles).map((s: NavSection) => s.id);

const ALL_SECTION_IDS = NAV_SECTIONS.map((s: NavSection) => s.id);

describe('nav role scoping', () => {
  it('exposes every section through the table, so none can bypass scoping', () => {
    // If a section is rendered from JSX rather than NAV_SECTIONS it escapes both
    // visibleNavSections and this test — which is the failure mode being pinned.
    expect(ALL_SECTION_IDS).toEqual(['candidate', 'hr', 'admin', 'platform', 'company']);
    for (const section of NAV_SECTIONS) {
      expect(typeof section.visibleTo).toBe('function');
      expect(section.items.length).toBeGreaterThan(0);
    }
  });

  it('gives a plain candidate the candidate section and nothing else', () => {
    expect(idsFor(['candidate'])).toEqual(['candidate']);
  });

  it('treats a user with no roles as a candidate rather than showing nothing', () => {
    // A brand-new account mid-provisioning must still be able to navigate.
    expect(idsFor([])).toEqual(['candidate']);
  });

  it('gives an HR manager the hiring section and NOT the candidate section', () => {
    const ids = idsFor(['hr_manager']);
    expect(ids).toEqual(['hr']);
    expect(ids).not.toContain('candidate');
  });

  it('gives a super admin the company section only', () => {
    expect(idsFor(['super_admin'])).toEqual(['company']);
  });

  it('gives a platform owner the platform section only', () => {
    expect(idsFor(['platform_owner'])).toEqual(['platform']);
  });

  it('gives the analytics admin role the admin section only', () => {
    expect(idsFor(['admin'])).toEqual(['admin']);
  });

  it('gives a platform owner who also holds admin BOTH sections, in table order', () => {
    // CLAUDE.md: the platform owner also holds `admin` for analytics. Both are
    // legitimate, so both render — role scoping is additive, not exclusive.
    expect(idsFor(['platform_owner', 'admin'])).toEqual(['admin', 'platform']);
  });

  it('hides the candidate section from staff who ALSO carry the candidate role', () => {
    // The reason the predicate is isCandidateOnly and not roles.includes(
    // 'candidate'): a staff account that also carries `candidate` would
    // otherwise reproduce the original bug exactly.
    for (const staff of ['hr_manager', 'super_admin', 'platform_owner', 'admin']) {
      expect(idsFor(['candidate', staff])).not.toContain('candidate');
    }
  });

  it('never shows a staff section to a plain candidate', () => {
    const ids = idsFor(['candidate']);
    for (const staffSection of ['hr', 'admin', 'platform', 'company']) {
      expect(ids).not.toContain(staffSection);
    }
  });

  it('lands every role on a page its own nav actually links to', () => {
    // The defect this pins: `admin` is privileged, so scoping the nav removed
    // the candidate section from admin-only accounts — while login, the denied-
    // route redirect and the brand wordmark all still sent them to /dashboard.
    // Signed in, on a candidate page, with a sidebar linking only to /admin/*.
    const cases: Array<[string[], string]> = [
      [['candidate'], '/dashboard'],
      [[], '/dashboard'],
      [['hr_manager'], '/hr'],
      [['super_admin'], '/superadmin'],
      [['platform_owner'], '/platform'],
      [['admin'], '/admin/overview'],
    ];
    for (const [roles, expected] of cases) {
      expect(homePathFor(roles)).toBe(expected);
    }
  });

  it('resolves a multi-role user by HOME_BY_ROLE order, not by role label', () => {
    // Renamed from "…their most-privileged home", which was not what the third
    // case asserts and read as a contradiction of AppShell's ROLE_PRIORITY —
    // that list ranks `admin` above `hr_manager` for the display label.
    expect(homePathFor(['platform_owner', 'admin'])).toBe('/platform');
    expect(homePathFor(['candidate', 'hr_manager'])).toBe('/hr');

    // The deliberate divergence, pinned so it is a decision rather than drift:
    // this user is LABELLED "Platform Admin" and lands on /hr. `admin` is the
    // analytics role and sits outside the hierarchy (CLAUDE.md), so for someone
    // who also runs hiring, the hiring console is the useful place to open.
    // See the HOME_BY_ROLE comment in navSections.tsx.
    expect(homePathFor(['admin', 'hr_manager'])).toBe('/hr');
  });

  it('gives every staff role a home inside a section it can see', () => {
    // The property that makes the two fixes consistent: whatever homePathFor
    // returns must be reachable from that role's own nav, or we have simply
    // moved the stranding somewhere new.
    for (const role of ['hr_manager', 'super_admin', 'platform_owner', 'admin']) {
      const home = homePathFor([role]);
      const reachable = visibleNavSections([role]).flatMap((s: NavSection) =>
        s.items.map((i) => i.to),
      );
      expect(reachable.some((to) => home === to || home.startsWith(`${to}/`))).toBe(true);
    }
  });

  it('routes every nav item to a path its section owner can actually reach', () => {
    // Guards against the inverse defect: a link shown to a role whose route
    // guard would bounce it straight back to /dashboard.
    const prefixes: Record<string, string> = {
      hr: '/hr',
      admin: '/admin',
      platform: '/platform',
      company: '/superadmin',
    };
    for (const section of NAV_SECTIONS) {
      const prefix = prefixes[section.id];
      if (!prefix) continue;
      for (const item of section.items) {
        expect(item.to.startsWith(prefix)).toBe(true);
      }
    }
  });
});
