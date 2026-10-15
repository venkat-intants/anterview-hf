import { describe, it, expect } from 'vitest';
import { csvCell, csvRow } from '../lib/csv';

describe('csvCell', () => {
  it.each(['=1+1', '+SUM(A1)', '-1+2', '@SUM(A1)', '\t=1', '\r=1', '\n=1', '-5'])(
    'neutralises a leading formula trigger in %j (including numeric-looking "-5")',
    (raw) => {
      const cell = csvCell(raw);
      expect(cell.startsWith(`"'`)).toBe(true);
      expect(cell).toBe(`"'${raw}"`);
    },
  );

  it('leaves ordinary text alone, quoted', () => {
    expect(csvCell('Asha Rao')).toBe('"Asha Rao"');
    expect(csvCell('a=b')).toBe('"a=b"');
    expect(csvCell("O'Brien")).toBe(`"O'Brien"`);
  });

  it('doubles embedded quotes and survives commas and newlines', () => {
    expect(csvCell('say "hi", ok')).toBe('"say ""hi"", ok"');
    expect(csvCell('line1\nline2')).toBe('"line1\nline2"');
  });

  it('quotes AFTER neutralising, so an embedded quote in a payload cannot escape', () => {
    expect(csvCell('=HYPERLINK("http://x","y")')).toBe(`"'=HYPERLINK(""http://x"",""y"")"`);
  });

  it('writes real numbers as-is (they cannot carry a payload)', () => {
    expect(csvCell(-5)).toBe('-5');
    expect(csvCell(0)).toBe('0');
    expect(csvCell(Number.NaN)).toBe('');
  });

  it('maps null/undefined to an empty cell and booleans to words', () => {
    expect(csvCell(null)).toBe('');
    expect(csvCell(undefined)).toBe('');
    expect(csvCell(true)).toBe('true');
  });

  it('does not neutralise an empty string', () => {
    expect(csvCell('')).toBe('""');
  });
});

describe('csvRow', () => {
  it('joins cells with commas', () => {
    expect(csvRow(['a', 1, '=x', null])).toBe(`"a",1,"'=x",`);
  });
});
