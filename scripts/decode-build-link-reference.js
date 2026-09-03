// Decode a `?i=` share payload with the REFERENCE LZ-UTF8 implementation, the way the planner
// does (`Bc` in its bundle): unswap the prefix, base64url -> base64, LZ-UTF8 decompress, strip
// NULs, atob, JSON.parse.
//
// This exists because our own decoder shares its author with our encoder, so agreement between
// the two proves nothing about whether the SITE can read what we write. `rotemdan/lzutf8.js` is
// the library the site bundles.
//
//   npm --prefix <dir> install lzutf8@0.6.3
//   node scripts/decode-build-link-reference.js <payload-file> [--json out.json]
//
// Resolve the library from NODE_PATH or a local install; there is no package.json in this repo.
const LZ = require('lzutf8');
const fs = require('fs');
const payload = fs.readFileSync(process.argv[2], 'utf8').trim().replace(/^https?:[^?]*\?i=/, '');
const swapped = payload.replace(/^uwu/, 'eyI').replace(/^UWU/, 'eyJ').replace(/-/g, '+').replace(/_/g, '/');
let text;
try { text = LZ.decompress(swapped, { inputEncoding: 'Base64' }); }
catch (err) { console.log('LZUTF8 DECOMPRESS THREW:', err.message); process.exit(2); }
const stripped = text.split('\u0000').join('');
const json = Buffer.from(stripped, 'base64').toString('latin1');
let doc;
try { doc = JSON.parse(json); }
catch (err) {
  console.log('JSON.parse FAILED:', err.message);
  console.log('json bytes', json.length, 'lz output bytes', text.length);
  const at = Number((err.message.match(/position (\d+)/) || [])[1] || 0);
  console.log('around:', JSON.stringify(json.slice(Math.max(0, at - 140), at + 140)));
  process.exit(4);
}
const out = process.argv.indexOf('--json');
if (out > 0 && process.argv[out + 1]) fs.writeFileSync(process.argv[out + 1], JSON.stringify(doc, null, 1));
console.log('OK json bytes', json.length,
  'armaments', doc.inventory && doc.inventory.slots.length,
  'talismans', doc.talismans && doc.talismans.slots.length,
  'faceData', typeof doc.faceData);
