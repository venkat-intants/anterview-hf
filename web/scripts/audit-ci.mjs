#!/usr/bin/env node
/**
 * npm-audit gate with an explicit accepted-risk allowlist.
 *
 * The npm analogue of the repo's .trivyignore. `npm audit` has no native
 * ignore file, and the blunt alternatives are both wrong:
 *   --audit-level=critical  silently accepts every future high, not just the
 *                           ones we have reviewed
 *   || true                 accepts everything and makes the step theatre
 *
 * So: fail on any advisory affecting a PRODUCTION dependency unless its GHSA
 * id is listed below with a reason. Dev-only advisories (vite/vitest/esbuild
 * and friends) are excluded via --omit=dev — they never reach a user, since
 * the frontend ships as a static build.
 *
 * Adding an entry here is a deliberate risk acceptance. Each needs: why it is
 * not exploitable HERE, and what unblocks the upgrade.
 */

import { execFileSync } from 'node:child_process';

/** @type {Record<string, {reason: string, unblock: string}>} */
const ALLOWLIST = {
  'GHSA-qwww-vcr4-c8h2': {
    reason:
      'react-router "RSC Mode CSRF Bypass". Requires React Router RSC mode. ' +
      'This app is a pure client-side SPA — src/main.tsx mounts with ' +
      'createRoot(); there is no RSC, no SSR, no hydrateRoot, no ' +
      'StaticRouter/createStaticHandler anywhere in src/. The vulnerable code ' +
      'path is unreachable. No fixed version exists: the advisory range is ' +
      '>=7.12.0 <8.3.0 and no 8.x is published (latest is 7.18.2), while ' +
      'downgrading to 7.11.0 would REINTRODUCE the open-redirect advisories ' +
      '(GHSA-2j2x-hqr9-3h42, GHSA-wrjc-x8rr-h8h6) that 7.18.x fixes.',
    unblock: 'react-router-dom >= 8.3.0 once published',
  },
};

function audit() {
  try {
    // npm audit exits non-zero when it finds anything; capture either way.
    return execFileSync('npm', ['audit', '--omit=dev', '--json'], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
      shell: process.platform === 'win32',
    });
  } catch (err) {
    if (typeof err.stdout === 'string' && err.stdout.length > 0) return err.stdout;
    throw err;
  }
}

const report = JSON.parse(audit());
const vulns = report.vulnerabilities ?? {};

/** Collect every distinct advisory, with the package it came in through. */
const advisories = new Map();
for (const [pkg, node] of Object.entries(vulns)) {
  for (const via of node.via ?? []) {
    if (typeof via !== 'object' || !via.url) continue;
    const id = via.url.split('/').pop();
    if (!advisories.has(id)) {
      advisories.set(id, { id, pkg, severity: via.severity, title: via.title, url: via.url });
    }
  }
}

const blocking = [];
const accepted = [];
for (const adv of advisories.values()) {
  (ALLOWLIST[adv.id] ? accepted : blocking).push(adv);
}

for (const adv of accepted) {
  console.log(`accepted  ${adv.id.padEnd(24)} ${adv.severity.padEnd(9)} ${adv.pkg}`);
  console.log(`          reason: ${ALLOWLIST[adv.id].reason}`);
  console.log(`          unblock: ${ALLOWLIST[adv.id].unblock}`);
}

// An allowlist entry that no longer matches anything is stale — it hides the
// fact that the risk was accepted for a dependency we have since removed or
// upgraded. Report it, but do not fail: a transient resolution difference
// should not break the build.
for (const id of Object.keys(ALLOWLIST)) {
  if (!advisories.has(id)) console.log(`stale     ${id} is allowlisted but no longer reported — prune it`);
}

if (blocking.length === 0) {
  console.log(`\nOK — ${accepted.length} accepted, 0 unreviewed production advisories.`);
  process.exit(0);
}

console.error(`\n${blocking.length} unreviewed production advisor${blocking.length === 1 ? 'y' : 'ies'}:\n`);
for (const adv of blocking) {
  console.error(`  ${adv.severity.toUpperCase().padEnd(9)} ${adv.pkg} — ${adv.title}`);
  console.error(`            ${adv.url}`);
}
console.error(
  '\nFix it (npm audit fix), or — if it is genuinely not exploitable here —\n' +
    'add the GHSA id to ALLOWLIST in web/scripts/audit-ci.mjs with a reason\n' +
    'and an unblock condition.',
);
process.exit(1);
