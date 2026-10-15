// csv — building a CSV cell that is safe to open in a spreadsheet.
//
// Quoting stops a cell breaking the CSV's structure; it does NOT stop Excel or
// LibreOffice evaluating a cell that begins with = + - @ (or a TAB / CR, which
// some versions strip before looking at the first character). Free text we
// export — an applicant's name from the unauthenticated apply form — can start
// with any of those (CWE-1236). The standard neutralisation, mirrored from the
// server's export (admin_ops analytics/interviews.py `_csv_safe`), is a leading
// apostrophe: the spreadsheet then treats the cell as text and hides the quote.
//
// Decision: a STRING is neutralised on its first character alone, so "-5"
// becomes "'-5" — a leading minus is a formula trigger ("-1+cmd|…"), and we
// cannot tell "-5" from "-1+cmd" without parsing it the way each spreadsheet
// does. A genuine `number` cannot carry a payload and is written as-is, so
// export numeric columns as numbers, not as strings.

const FORMULA_TRIGGERS = /^[=+\-@\t\r\n]/;

/** One CSV cell, neutralised against formula injection and quoted. */
export function csvCell(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  const safe = FORMULA_TRIGGERS.test(value) ? `'${value}` : value;
  return `"${safe.replace(/"/g, '""')}"`;
}

/** One CSV line (no trailing newline). */
export function csvRow(
  values: ReadonlyArray<string | number | boolean | null | undefined>,
): string {
  return values.map(csvCell).join(',');
}
