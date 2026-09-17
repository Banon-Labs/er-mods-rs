// Does Seamless attempt a Steam lobby join during the fifteen seconds it sits at session state
// 0x12, and what does it do with the answer?
//
// Run br-20260917-205031-edd3, live, with a friend hosting in Highroad Cross: our sweep finds the
// host, Seamless matches (0x0f, ~170ms), enters 0x12, holds it for 15000ms +/- 50ms on thirty
// consecutive cycles, and falls back to searching. Our own DLL cancels nothing on that path
// (`local[cancelled=0]`), so the connect is failing inside Seamless and the 15s is its own timeout.
//
// # Where the calls actually are
//
// The first cut of this script hooked the flat `SteamAPI_ISteamMatchmaking_*` exports of
// `steam_api64.dll` and caught nothing at all -- not even `RequestLobbyList`, which our own DLL's
// log proves fires on every search cycle. Those flat wrappers are the C binding; Seamless takes
// the interface from `SteamAPI_SteamMatchmaking_v009()` and calls through its vtable, which is
// what `lobby_publish.rs` already detours and says so in as many words: "in `steamclient64.dll`'s
// interface vtable, not in `ersc.dll`".
//
// Slots are v009's, and four of the five are pinned independently by `lobby_publish.rs`
// (`RequestLobbyList` 4, `AddRequestLobbyListStringFilter` 5, `GetLobbyData` 19, `SetLobbyData` 20,
// `GetLobbyOwner` 35). `JoinLobby` 14 and `LeaveLobby` 15 sit in the same table; the resolved
// addresses are reported so they can be checked against a disassembly rather than taken on trust.
//
// Slots 4 and 5 are deliberately left alone: our own DLL holds MinHook detours on both, and a
// second interceptor over a live trampoline is how an attach turns into a crash.
//
// Read-only: entry-only interceptors on two vtable slots. Nothing is replaced and no game memory
// is written.

'use strict';

const MATCHMAKING_ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';

// Slot 14 only. Slot 15 was in this list as `LeaveLobby` for one reload and resolved to
// `0xfff02560`, which belongs to no module while its neighbours sit in `lsteamclient.dll` at
// `0x6ffffad3xxxx` -- so the v009 slot table stops describing this build somewhere at or before it,
// and `Interceptor.attach` took the address anyway rather than refusing. Hooking an address that is
// not a function entry writes a trampoline into memory nobody has identified, in a live session the
// player is in. Slot 14 is corroborated: it lands in the same module and within a kilobyte of the
// `AddRequestLobbyListStringFilter` our own DLL resolved independently at `0x6ffffad3ac80`.
//
// Slot 19 is the control. A silent `JoinLobby` proves nothing on its own -- it reads the same
// whether Seamless never joins a lobby or the slot table stopped describing this build at 14.
//
// `GetLobbyOwner` (slot 35) was the control for one reload and was silent too, which is why it is
// not the control any more: it is inconclusive rather than informative here, because our own DLL
// reads it only on the publish path and this run publishes nothing (`advert[published=0]`). Slot 19
// is `GetLobbyData`, which a search reads once per key per candidate, so it is hot whenever a query
// returns anything at all. Sampled rather than reported per call for the same reason.
//
// Both slots are pinned by `lobby_publish.rs`, and neither is detoured by our own DLL, so there is
// no trampoline to attach over.
const SLOTS = [
  { slot: 12, name: 'GetLobbyByIndex' },
  { slot: 14, name: 'JoinLobby' },
  { slot: 17, name: 'GetNumLobbyMembers' },
  { slot: 19, name: 'GetLobbyData', sample: 50 },
];

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
  // `Module.findExportByName` was removed in Frida 17 and throws `TypeError: not a function`. The
  // export list is what that call read anyway, and enumerating it is already proven on this target
  // by the first cut of this script.
  let accessor = null;
  for (const e of steam.enumerateExports()) {
    if (e.name === MATCHMAKING_ACCESSOR) {
      accessor = e.address;
      break;
    }
  }
  if (accessor === null) {
    send({ tag: 'fatal', reason: MATCHMAKING_ACCESSOR + ' not exported' });
  } else {
    const getMatchmaking = new NativeFunction(accessor, 'pointer', []);
    const iface = getMatchmaking();
    if (iface.isNull()) {
      send({ tag: 'fatal', reason: MATCHMAKING_ACCESSOR + ' returned null' });
    } else {
      const vtable = iface.readPointer();
      const owner = Process.findModuleByAddress(vtable);
      send({
        tag: 'iface',
        iface: iface.toString(),
        vtable: vtable.toString(),
        vtableModule: owner === null ? null : owner.name,
      });

      const installed = [];
      for (const entry of SLOTS) {
        const target = vtable.add(entry.slot * Process.pointerSize).readPointer();
        const home = Process.findModuleByAddress(target);
        // Refuse an address that belongs to no module. `Interceptor.attach` does not: slot 15
        // resolved to `0xfff02560` and it took it, which would have written a trampoline into
        // unidentified memory in a session the player is in.
        if (home === null || home.name.toLowerCase() !== 'lsteamclient.dll') {
          send({
            tag: 'slot-refused',
            name: entry.name,
            slot: entry.slot,
            target: target.toString(),
            module: home === null ? null : home.name,
          });
          continue;
        }
        try {
          let seen = 0;
          Interceptor.attach(target, {
            onEnter(args) {
              seen += 1;
              // A hot getter would flood the transcript one line per call and tell us nothing the
              // count does not. `sample` reports the first call and then every nth.
              if (entry.sample !== undefined && seen !== 1 && seen % entry.sample !== 0) return;
              // `this` is args[0]; the lobby id is the 64-bit `CSteamID` in args[1].
              send({ tag: 'call', name: entry.name, seen, lobby: args[1].toString() });
            },
          });
          installed.push({
            name: entry.name,
            slot: entry.slot,
            target: target.toString(),
            module: home === null ? null : home.name,
          });
        } catch (error) {
          send({ tag: 'hook-failed', name: entry.name, target: target.toString(), error: String(error) });
        }
      }
      send({ tag: 'hooks', installed });
    }
  }
}
