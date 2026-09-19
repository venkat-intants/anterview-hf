// Reading the mail the app sends.
//
// Candidates receive their exam and interview links by email, so a spec that
// follows a real candidate has to read one. Mailpit catches everything the local
// stack sends; nothing leaves the machine.

import { expect } from '@playwright/test';
import { MAILPIT_URL } from './env';

export interface Mail {
  subject: string;
  text: string;
  created: string;
}

interface MailpitSummary {
  ID: string;
  Subject: string;
  Created: string;
}

/** Every message to this address, newest first. */
export async function mailFor(address: string): Promise<Mail[]> {
  const search = await fetch(
    `${MAILPIT_URL}/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
  );
  expect(search.ok, `Mailpit search failed (${search.status}) — is it running on ${MAILPIT_URL}?`)
    .toBeTruthy();
  const found = (await search.json()) as { messages?: MailpitSummary[] };
  const out: Mail[] = [];
  for (const m of found.messages ?? []) {
    const full = await (await fetch(`${MAILPIT_URL}/api/v1/message/${m.ID}`)).json();
    out.push({ subject: m.Subject, text: (full as { Text?: string }).Text ?? '', created: m.Created });
  }
  return out;
}

/**
 * Wait for an email to this address whose subject matches, and return it.
 * The outbox worker sends every few seconds, so this polls rather than assuming.
 */
export async function waitForMail(
  address: string,
  subjectMatch: RegExp,
  timeoutMs = 30_000,
): Promise<Mail> {
  const started = Date.now();
  let seen: string[] = [];
  while (Date.now() - started < timeoutMs) {
    const mails = await mailFor(address);
    seen = mails.map((m) => m.subject);
    const hit = mails.find((m) => subjectMatch.test(m.subject));
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 1_000));
  }
  throw new Error(
    `No mail to ${address} matching ${subjectMatch} within ${timeoutMs}ms. Subjects seen: ${
      seen.join(' | ') || '(none)'
    }`,
  );
}

/**
 * Wait for a matching email NEWER than one already read — for a second code of
 * the same kind, where waitForMail would return the first, already-used one.
 */
export async function waitForNewerMail(
  address: string,
  subjectMatch: RegExp,
  previous: Mail,
  timeoutMs = 30_000,
): Promise<Mail> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const hit = (await mailFor(address)).find(
      (m) => subjectMatch.test(m.subject) && m.created > previous.created,
    );
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 1_000));
  }
  throw new Error(`No mail to ${address} matching ${subjectMatch} newer than ${previous.created}`);
}

/** The single-use link out of an email body, e.g. /exam#<token>. */
export function linkIn(mail: Mail, pattern: RegExp): string {
  const match = mail.text.match(pattern);
  if (!match) throw new Error(`No link matching ${pattern} in "${mail.subject}"`);
  return match[1] ?? match[0];
}
