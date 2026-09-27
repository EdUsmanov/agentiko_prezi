import { canLegallyEdit, eotToTtf, parseEotMetadata } from './vendor/mtx-decompressor/index.mjs';

try {
  const chunks = [];
  let inputLength = 0;
  for await (const chunk of process.stdin) {
    inputLength += chunk.length;
    if (inputLength > 24 * 1024 * 1024) throw new Error('Embedded font exceeds the decoder limit');
    chunks.push(chunk);
  }
  const source = Buffer.from(Buffer.concat(chunks).toString('utf8'), 'base64');
  const metadata = parseEotMetadata(source);
  const permitted = canLegallyEdit(metadata);
  const font = permitted ? eotToTtf(source) : null;
  if (font && (font.length < 12 || Buffer.from(font.subarray(0, 4)).toString('hex') !== '00010000')) {
    throw new Error('Embedded EOT did not decode to a TrueType font');
  }
  process.stdout.write(JSON.stringify({
    family: metadata.familyName,
    style: metadata.styleName,
    permissions: metadata.permissions,
    embeddable: permitted,
    data: font ? Buffer.from(font).toString('base64') : null,
  }));
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}
