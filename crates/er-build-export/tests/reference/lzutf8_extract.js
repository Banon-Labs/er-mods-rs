// The planner's decode path, backed by the REFERENCE LZ-UTF8 implementation.
//
// The acceptance test wants a module exposing the site's `Bc` (payload -> the base64 text that
// `atob` then turns into JSON). The site bundles rotemdan/lzutf8.js; this shim calls that same
// library, so the check stays an INDEPENDENT one -- a bug shared by our encoder and our decoder
// still fails here.
//
// The library is not vendored. Install it beside this file (or anywhere on NODE_PATH):
//
//   npm --prefix crates/er-build-export/tests/reference install lzutf8@0.6.3

let LZ;
try {
  LZ = require('lzutf8');
} catch (err) {
  throw new Error('lzutf8 is not installed: ' + err.message +
    ' -- npm --prefix crates/er-build-export/tests/reference install lzutf8@0.6.3');
}

// `Bc`: unswap the legacy prefix, base64url -> base64, decompress, drop the NUL padding.
exports.Bc = (payload) => LZ.decompress(
  String(payload)
    .replace(/^uwu/, 'eyI')
    .replace(/^UWU/, 'eyJ')
    .replace(/-/g, '+')
    .replace(/_/g, '/'),
  { inputEncoding: 'Base64' },
).split('\u0000').join('');

// `zc`: the inverse, for a caller that wants to build a payload with the reference encoder.
exports.zc = (base64) => LZ.compress(String(base64), { outputEncoding: 'Base64' })
  .replace(/^eyI/, 'uwu')
  .replace(/^eyJ/, 'UWU')
  .replace(/\+/g, '-')
  .replace(/\//g, '_');

exports.LZ = LZ;
