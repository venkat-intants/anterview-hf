#!/usr/bin/env node
/**
 * `npm run e2e` guard (FE-3).
 *
 * `web/e2e/` holds a README and no specs — deliberately, and documented there.
 * Playwright treats "zero matching spec files" as SUCCESS, so the script exited
 * 0 and read as "the E2E suite passes". That is the failure mode the README
 * itself warns about: an engineer told to run the E2E smoke before release gets
 * a green tick from a suite that asserts nothing.
 *
 * So: refuse to run when there are no specs, and say where the reasoning lives.
 * This is a guard, not a permanent block — the moment a real spec lands the
 * check passes and Playwright runs normally, with no second edit needed here.
 */

import { readdirSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const e2eDir = join(webRoot, 'e2e');

/** Playwright's default spec pattern: *.spec.* / *.test.* anywhere under testDir. */
const SPEC_RE = /\.(spec|test)\.[cm]?[jt]sx?$/;

function findSpecs(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return findSpecs(full);
    return SPEC_RE.test(entry.name) ? [full] : [];
  });
}

const specs = findSpecs(e2eDir);

if (specs.length === 0) {
  process.stderr.write(
    [
      '',
      'npm run e2e — REFUSING TO RUN: web/e2e/ contains no spec files.',
      '',
      'Playwright would exit 0 here, which reads as "E2E passed" when nothing',
      'was asserted. The suite is empty on purpose; the reasoning and the three',
      'things a rewrite needs first (stable data-testid hooks, fake media',
      'devices, and a per-run cost decision) are written up in:',
      '',
      '    web/e2e/README.md',
      '',
      'Add a spec there and this command runs Playwright normally.',
      '',
    ].join('\n'),
  );
  process.exit(1);
}

const result = spawnSync('npx', ['playwright', 'test', ...process.argv.slice(2)], {
  cwd: webRoot,
  stdio: 'inherit',
  shell: process.platform === 'win32',
});

process.exit(result.status ?? 1);
