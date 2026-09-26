// No filesystem/network operations: bytes in on stdin, validated TTF out on stdout.
import { eotToTtf, parseEotMetadata } from './_vendor/mtx_decompressor/index.mjs';
const limit = 16 * 1024 * 1024;
try {
  const chunks = []; let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > limit) throw Error('EOT input exceeds limit');
    chunks.push(chunk);
  }
  const source = Buffer.concat(chunks);
  const meta = parseEotMetadata(source);
  if (meta.permissions !== 0 || meta.rootString || (meta.flags & (0x20 | 0x10000000)))
    throw Error('Restricted EOT is not supported');
  const result = eotToTtf(source);
  if (!result || result.length < 12 || result.length > limit ||
      Buffer.from(result.subarray(0,4)).toString('hex') !== '00010000')
    throw Error('Invalid or oversized TTF result');
  process.stdout.write(Buffer.from(result));
} catch (_) {
  // Never echo untrusted font names, paths or decoder internals to the caller.
  process.stderr.write('Embedded font decoding failed\n');
  process.exitCode = 1;
}
