// The report-only CSP allows exactly one inline script: the pre-paint theme
// script in index.html, by its sha256. Edit that script without updating the
// hash in web/vercel.json AND space/Caddyfile and the policy would start
// reporting a violation on every page load (and, the day it is enforced, the
// theme would flash). This test recomputes the hash so that drift fails CI.
//
// Browsers hash the script text AFTER newline normalisation (CRLF -> LF), so
// the source is normalised the same way: a Windows CRLF checkout and a Linux
// LF build must agree.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const WEB = path.resolve(__dirname, '../..');
const REPO = path.resolve(WEB, '..');

function inlineScripts(html: string): string[] {
  const normalised = html.replace(/\r\n?/g, '\n');
  // Only attribute-less <script> blocks are inline; module/src scripts are not.
  return [...normalised.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
}

function sha256Source(script: string): string {
  return `'sha256-${createHash('sha256').update(script, 'utf8').digest('base64')}'`;
}

const indexHtml = readFileSync(path.join(WEB, 'index.html'), 'utf8');
const vercel = readFileSync(path.join(WEB, 'vercel.json'), 'utf8');
const caddy = readFileSync(path.join(REPO, 'space', 'Caddyfile'), 'utf8');

function reportOnly(text: string, quoted: boolean): string {
  const re = quoted
    ? /"key": "Content-Security-Policy-Report-Only", "value": "([^"]+)"/
    : /Content-Security-Policy-Report-Only "([^"]+)"/;
  const m = re.exec(text);
  if (!m) throw new Error('Content-Security-Policy-Report-Only header not found');
  return m[1];
}

describe('report-only CSP script hash', () => {
  const scripts = inlineScripts(indexHtml);

  it('index.html has exactly one inline script (the theme script)', () => {
    expect(scripts).toHaveLength(1);
  });

  it('web/vercel.json allows that script by hash', () => {
    expect(reportOnly(vercel, true)).toContain(sha256Source(scripts[0]));
  });

  it('space/Caddyfile allows that script by hash', () => {
    expect(reportOnly(caddy, false)).toContain(sha256Source(scripts[0]));
  });

  it('both origins carry the same report-only policy', () => {
    expect(reportOnly(vercel, true)).toBe(reportOnly(caddy, false));
  });

  it('the report-only policy is never the enforced header', () => {
    // Guard against someone renaming the header to enforce it blind.
    expect(vercel).toContain('"key": "Content-Security-Policy", "value": "frame-ancestors');
    expect(caddy).toMatch(/Content-Security-Policy "frame-ancestors /);
  });

  it('the built dist/index.html (when present) serves the same script', () => {
    const built = path.join(WEB, 'dist', 'index.html');
    if (!existsSync(built)) return; // dist is a build artefact; absent in a fresh checkout
    const served = inlineScripts(readFileSync(built, 'utf8'));
    expect(served).toEqual(scripts);
  });
});
