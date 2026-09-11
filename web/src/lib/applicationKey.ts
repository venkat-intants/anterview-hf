// The identity of one APPLICATION on a person-and-opening screen (B5).
//
// The pipeline board and the interview picker show a person once per opening,
// so the person's id no longer identifies a row: two rows would share it, and
// React would reuse the wrong one. The application (enrolment) id does, and a
// person filed under no opening falls back to their own id, prefixed so it can
// never collide with an enrolment's.
//
// Kept out of the api/ modules on purpose: tests mock those wholesale, and a
// display helper living there would have to be re-provided by every mock.

export function applicationKey(row: {
  enrolment_id?: string | null;
  applicant_id?: string;
  id?: string;
}): string {
  return row.enrolment_id ?? `applicant:${row.applicant_id ?? row.id ?? ''}`;
}
