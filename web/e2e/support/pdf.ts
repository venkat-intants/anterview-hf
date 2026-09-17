// A small, real PDF with selectable text.
//
// The apply form only accepts a PDF it can read, so a spec needs a CV. Building
// one here rather than committing fixture files keeps the CV visible next to the
// test that relies on it: a spec can pin a candidate's resume score by including
// "E2E-SCORE: 85" in the lines (see services/data_gateway/app/fake_ai.py).

function escapeText(line: string): string {
  return line.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
}

/** One page of Helvetica text, uncompressed, with a correct cross-reference table. */
export function makeCvPdf(lines: string[]): Buffer {
  const body =
    `BT /F1 11 Tf 72 780 Td 16 TL\n` +
    lines.map((l) => `(${escapeText(l)}) Tj T*`).join('\n') +
    `\nET\n`;

  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] ' +
      '/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(body, 'latin1')} >>\nstream\n${body}endstream`,
  ];

  let pdf = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((obj, i) => {
    offsets.push(Buffer.byteLength(pdf, 'latin1'));
    pdf += `${i + 1} 0 obj\n${obj}\nendobj\n`;
  });

  const xrefAt = Buffer.byteLength(pdf, 'latin1');
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) pdf += `${String(offset).padStart(10, '0')} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`;

  return Buffer.from(pdf, 'latin1');
}
