// localDatetime.ts — converting between a <input type="datetime-local">
// value (a naive wall-clock string with no zone) and an ISO instant WITH an
// offset, which is what every PH4-A2 scheduling endpoint requires.
//
// THE BUG THIS EXISTS TO PREVENT: `new Date(iso).toISOString().slice(0, 16)`
// looks like it produces a datetime-local value, but toISOString() always
// returns UTC wall time — so pre-filling an input with it shows a UTC time as
// though it were the reader's own local time, silently off by their UTC
// offset. That is exactly the defect fixed in HRInterviews' reschedule
// dialog; every new PH4-A2 form uses these two functions instead so the bug
// cannot recur here.

/**
 * The wall-clock string a datetime-local input needs to DISPLAY a moment in
 * the reader's own (browser) timezone: "YYYY-MM-DDTHH:mm".
 *
 * Uses the Date object's local getters — never toISOString(), which is UTC.
 */
export function toLocalInputValue(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number): string => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/**
 * A datetime-local input's value, read as the wall time it is — the browser's
 * own timezone — and converted to an ISO instant with an explicit UTC offset
 * ("Z"). `new Date('2026-09-21T10:30')` (no zone suffix) is parsed by every
 * browser and by Node as LOCAL time, so this is the correct and only
 * conversion needed; never hand a naive string straight to the server.
 */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString();
}
