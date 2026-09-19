// Which vtable slot is which, measured rather than taken from the Steamworks header.
//
// `drop-map-filter.js` hooked slot 12 as `GetLobbyByIndex` on the header's ordering and got
// `index: 1113200, lobby: 0x10fc70` back -- neither an index nor a `CSteamID`, so the slot is not
// that function on this build. Slots 4 and 5 clearly are `RequestLobbyList` and
// `AddRequestLobbyListStringFilter`: they fire exactly when a search starts and carry the right
// strings. So the ordering is right at the front and wrong by slot 12, which is what a header from
// a different interface revision looks like.
//
// `steam_api64.dll`'s flat exports are thunks into `lsteamclient.dll`, and each one ends in a jump
// or call to the interface method it wraps. Rather than decode them, this prints both sides and
// lets the addresses be compared directly: every vtable slot with its module offset, and every
// flat matchmaking export with its own. A slot whose address appears inside one export's first
// bytes is that export's method.
//
// Read-only: no hooks, no writes.
'use strict';

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const SLOTS = 40;

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  send({ tag: 'fatal', reason: STEAM + ' not loaded' });
} else {
  const accessor = steam.findExportByName(ACCESSOR);
  const iface = new NativeFunction(accessor, 'pointer', [])();
  const vtable = iface.readPointer();

  const slots = [];
  for (let i = 0; i < SLOTS; i += 1) {
    let target;
    try {
      target = vtable.add(i * Process.pointerSize).readPointer();
    } catch (error) {
      break;
    }
    const home = Process.findModuleByAddress(target);
    slots.push({
      slot: i,
      address: target.toString(),
      where: home === null ? null : home.name + '+0x' + target.sub(home.base).toString(16),
    });
  }
  send({ tag: 'slots', slots: slots });

  // The flat exports, and the first pointer-sized jump target inside each, so a slot address can
  // be matched against the method a named export wraps.
  const named = [];
  for (const e of steam.enumerateExports()) {
    if (e.name.indexOf('SteamAPI_ISteamMatchmaking_') !== 0) continue;
    let head = null;
    try {
      head = Array.from(e.address.readByteArray(16) === null ? [] : new Uint8Array(e.address.readByteArray(16)))
        .map(function (b) {
          return ('0' + b.toString(16)).slice(-2);
        })
        .join(' ');
    } catch (error) {
      head = 'unreadable';
    }
    named.push({ name: e.name.replace('SteamAPI_ISteamMatchmaking_', ''), address: e.address.toString(), head: head });
  }
  send({ tag: 'exports', count: named.length, exports: named });
}
