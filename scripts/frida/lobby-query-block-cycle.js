// After a `Nearby only` ring is exhausted, does the search keep asking Steam, and does the block
// it asks for CHANGE?
//
// The user reports the banner stops reciting places once the ring runs out: "When I exhausted
// nearby, the banner didn't come up again saying it was going through 1-N locations again for a
// new player. It should keep doing this on repeat until I cancel."
//
// `search_banner::pump` pops from a `VecDeque` and never refills it, so the recital certainly
// dies. What that does not say is whether the SEARCH died with it, and the two want different
// fixes:
//
//   * queries keep going out and the block id cycles -> the search is fine, the banner alone is
//     broken, and the fix is to make the queue repeat.
//   * queries keep going out with the SAME block id -> the rotation died too, and refilling the
//     banner queue would show a recital that lies about what is being asked.
//   * queries stop -> the search itself ended, and the repeat has to restart it, not just the text.
//
// That queue is a Rust `VecDeque` inside our own DLL with no game-side signature, so Frida cannot
// read it. The Steam call it drives is reachable, and it is the part that decides between the three.
//
// Read-only: exports are hooked, arguments are read, nothing is written.

'use strict';

const STEAM_MODULE = 'steam_api64.dll';
const OUR_KEY = 'er_invasion_warp_map';

function steamModule() {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === STEAM_MODULE) return m;
  }
  return null;
}

const steam = steamModule();
if (steam === null) {
  send({ tag: 'fatal', why: STEAM_MODULE + ' is not loaded in this process' });
} else {
  // Report which of the matchmaking exports actually exist on this build, so a silent run is
  // distinguishable from a run where the name was wrong.
  const wanted = /RequestLobbyList|AddRequestLobbyListStringFilter|AddRequestLobbyListNumericalFilter/;
  const found = [];
  for (const e of steam.enumerateExports()) {
    if (e.type === 'function' && wanted.test(e.name)) found.push({ name: e.name, address: e.address.toString() });
  }
  send({ tag: 'exports', module: steam.name, base: steam.base.toString(), found });

  const readCStr = (p) => {
    try {
      return p.isNull() ? null : p.readCString(256);
    } catch (e) {
      return null;
    }
  };

  let filters = 0;
  let requests = 0;
  // The last value seen for our own key, so a repeat of the SAME block is visible as such rather
  // than as another indistinguishable line.
  let lastOurs = null;
  let sameInARow = 0;

  for (const e of found) {
    const addr = ptr(e.address);
    if (/AddRequestLobbyList\w*Filter/.test(e.name)) {
      Interceptor.attach(addr, {
        onEnter(args) {
          filters += 1;
          // Flat API: (key, value, comparison). Vtable-relative builds shift by one; both are
          // reported so the shape is visible rather than assumed.
          const a0 = readCStr(args[0]);
          const a1 = readCStr(args[1]);
          const a2 = readCStr(args[2]);
          const key = a0 === OUR_KEY ? a0 : a1 === OUR_KEY ? a1 : null;
          if (key === null) {
            if (filters <= 6) send({ tag: 'filter-other', fn: e.name, a0, a1, a2, filters });
            return;
          }
          const value = a0 === OUR_KEY ? a1 : a2;
          if (value === lastOurs) {
            sameInARow += 1;
            // Only the first few and then powers of two, so a stuck block reports as a count
            // rather than a flood.
            if (sameInARow <= 4 || (sameInARow & (sameInARow - 1)) === 0) {
              send({ tag: 'block-repeat', value, sameInARow, filters });
            }
            return;
          }
          sameInARow = 0;
          lastOurs = value;
          send({ tag: 'block-changed', value, filters });
        },
      });
    } else if (/RequestLobbyList/.test(e.name)) {
      Interceptor.attach(addr, {
        onEnter() {
          requests += 1;
          if (requests <= 8 || (requests & (requests - 1)) === 0) {
            send({ tag: 'request', requests, filters, lastOurs });
          }
        },
      });
    }
  }

  send({
    tag: 'armed',
    hooked: found.length,
    note: 'block-changed = the ring is rotating; block-repeat = it is stuck; request with no filter = unfiltered',
  });
}
