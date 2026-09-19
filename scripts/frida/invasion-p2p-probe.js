// Does the invader send this host a single packet during the fifteen seconds Seamless spends at
// session state 0x12?
//
// This is the probe that has to be armed BEFORE the host opens her world, because her hosting is
// the scarce resource. `scripts/frida/invasion-connect-probe.js` already established what it is
// not: across several full search cycles, `ISteamMatchmaking::JoinLobby` and `GetLobbyData` saw
// zero calls, and the player never appeared in her Steam recent-players list -- so no Steam session
// was ever formed. What remains is whether Seamless attempts a peer connection at all.
//
// # Hooking every slot rather than the ones a header names
//
// The matchmaking probe worked because `lobby_publish.rs` had already pinned four v009 slot numbers
// independently. Nothing in this repo has pinned a slot of `ISteamNetworking`,
// `ISteamNetworkingSockets` or `ISteamNetworkingMessages`, and guessing cost real time once
// already: slot 15 of the matchmaking vtable resolved to `0xfff02560`, which belongs to no module,
// and `Interceptor.attach` took it rather than refusing.
//
// So this walks each interface's vtable and hooks every entry that lands inside `lsteamclient.dll`,
// reporting by slot index. A call during the connect window names its own slot; which function that
// slot is can be read off the header afterwards, from a number that was measured rather than
// assumed. The module test is the safety rail: an entry outside that module is reported and left
// alone.
//
// Read-only: entry-only interceptors. Nothing is replaced and no memory is written.

'use strict';

// Accessors, in the order the answer is most likely to be in. Seamless is old enough to be on the
// legacy `ISteamNetworking` packet API; the two newer interfaces are here so a negative on the
// legacy one is not mistaken for a negative on all of them.
const ACCESSOR_PATTERNS = [
  /^SteamAPI_SteamNetworking_v\d+$/,
  /^SteamAPI_SteamNetworkingSockets_SteamAPI_v\d+$/,
  /^SteamAPI_SteamNetworkingMessages_SteamAPI_v\d+$/,
];

// A vtable has no length, so the walk needs a stop. Every interface here is well under this, and a
// bad read stops the walk anyway.
const MAX_SLOTS = 64;

// Per-slot reporting cap. A packet send during an active session is hot enough to flood the
// transcript, and the count carries the same information.
const SAMPLE = 25;

function moduleNamed(want) {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === want) return m;
  }
  return null;
}

const steam = moduleNamed('steam_api64.dll');

if (steam === null) {
  send({ tag: 'fatal', reason: 'steam_api64.dll not loaded' });
} else {
  const exports = steam.enumerateExports();

  for (const pattern of ACCESSOR_PATTERNS) {
    const found = exports.filter((e) => pattern.test(e.name));
    if (found.length === 0) {
      send({ tag: 'accessor-missing', pattern: String(pattern) });
      continue;
    }
    for (const entry of found) {
      hookInterface(entry.name, entry.address);
    }
  }
}

function hookInterface(name, accessor) {
  let iface;
  try {
    iface = new NativeFunction(accessor, 'pointer', [])();
  } catch (error) {
    send({ tag: 'accessor-threw', accessor: name, error: String(error) });
    return;
  }
  if (iface.isNull()) {
    // Ordinary before the interface is initialised, and worth saying rather than looking like a
    // silent negative later.
    send({ tag: 'accessor-null', accessor: name });
    return;
  }
  const vtable = iface.readPointer();
  const owner = Process.findModuleByAddress(vtable);
  send({
    tag: 'iface',
    accessor: name,
    iface: iface.toString(),
    vtable: vtable.toString(),
    vtableModule: owner === null ? null : owner.name,
  });

  const hooked = [];
  const refused = [];
  const named = [];
  for (let slot = 0; slot < MAX_SLOTS; slot += 1) {
    let target;
    try {
      target = vtable.add(slot * Process.pointerSize).readPointer();
    } catch (error) {
      break;
    }
    const home = Process.findModuleByAddress(target);
    if (home === null || home.name.toLowerCase() !== 'lsteamclient.dll') {
      refused.push({ slot, target: target.toString(), module: home === null ? null : home.name });
      continue;
    }
    let seen = 0;
    let nonzero = 0;
    try {
      Interceptor.attach(target, {
        onEnter() {
          seen += 1;
        },
        // The count alone cannot tell a receive poll that found nothing from one that read a
        // packet: both are one call. A slot firing at ~21/s on an idle host is a per-frame poll,
        // and its RETURN is the only thing that says whether anything arrived. Reported the first
        // time it is ever non-zero, and then sampled, so an incoming invasion cannot hide inside a
        // call count that was already in the thousands.
        onLeave(retval) {
          const value = retval.toInt32();
          if (value !== 0) {
            nonzero += 1;
            if (nonzero === 1 || nonzero % SAMPLE === 0) {
              send({ tag: 'nonzero', accessor: name, slot, seen, nonzero, value });
              return;
            }
          }
          if (seen !== 1 && seen % SAMPLE !== 0) return;
          send({ tag: 'call', accessor: name, slot, seen, nonzero });
        },
      });
      hooked.push(slot);
      // Names the slot instead of leaving it a number. `lsteamclient.dll` exports the flat
      // wrappers, so a vtable entry that coincides with one resolves to a real name and the two
      // hot slots stop being anonymous.
      const symbol = DebugSymbol.fromAddress(target);
      if (symbol !== null && symbol.name !== null) {
        named.push({ slot, symbol: symbol.name });
      }
    } catch (error) {
      refused.push({ slot, target: target.toString(), error: String(error) });
    }
  }
  send({ tag: 'hooks', accessor: name, hooked, refused, named });
}
