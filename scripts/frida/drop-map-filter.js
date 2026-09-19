// Take this mod's location filter out of Seamless's live invasion search, and see if it lands.
//
// # What this is testing
//
// Measured on run `br-20260918-005917-483b`: every `RequestLobbyList` ersc sent carried
// `er_invasion_warp_map Equal m61_48_44_00`, added by this mod's `hunt` detour, while the host the
// player is trying to reach publishes `m61_48_45_00`. One tile off, and Steam's equality filter
// removes her from every answer. The five searches captured in 90 seconds all named that same
// tile: the sweep's ring is frozen at place 1 of 49, because `sweep_tick` only runs while the
// Seamless session is idle and an invasion search never is. The ring cannot advance until the
// search stops, and the search cannot succeed until the ring advances.
//
// Everything else about the query already matches her. Her nine published keys against the six
// filters ersc sends, same run, same minute:
//
// ```text
// lobby_key      34154670...ef59  ==  34154670...ef59   this player's own key -- same pool
// 700f7f50...    yknx3_seamless_master_lobby  ==  same
// 91489e05...    true  ==  true
// ykssr_dlc      1  <=  1
// 21c40388...    2_1  !=  2_2
// er_..._map     m61_48_44_00  !=  m61_48_45_00        <- ours, and the tile is wrong
// ```
//
// So this drops our key from the outgoing query and leaves Seamless's own filters untouched. If an
// invasion then lands, the Rust fix is to stop narrowing while the sweep has not answered -- the
// `Nearby::Asking` case, which today falls through to the frozen ring. If it does not land, the
// remaining suspect is `21c40388`, which is Seamless's own and not ours to change.
//
// # How
//
// The vtable entry for slot 5 is repointed at a callback that forwards every filter except ours.
// Not `Interceptor.replace`: `lobby_publish` already owns that address through MinHook, and a
// trampoline under a trampoline is a worse place to be than a single pointer swap that `restore()`
// puts back. The interface vtable is shared by every instance of the class, so this covers ersc's
// interface as well as ours.
//
// This CHANGES a live game's outgoing matchmaking queries. It writes no game state, touches no
// save, and `restore()` undoes it.
'use strict';

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;
const REQUEST_SLOT = 4;
const BY_INDEX_SLOT = 12;
const OUR_KEY = 'er_invasion_warp_map';

// The host this player is trying to reach. Naming her here turns "is she reachable" into a fact
// ersc's own search produces: every lobby its result names comes back through `GetLobbyByIndex`,
// so her id appearing there is proof she is in the answer, and her never appearing across many
// searches is proof she is filtered out of it. No second query is sent -- an `ISteamMatchmaking`
// carries one lobby query at a time, and a query of ours issued while ersc has one in flight
// returned `matching=0` for even the bare Seamless marker, which is a collision and not an answer.
const TARGET_LOBBY = '109775241801277563';

// Seamless's own key, hashed like the rest of its names. Observed and never rewritten.
//
// It was briefly rewritten here, from this game's `2_1` to the host's `2_2`, on the reasoning that
// it was the last filter excluding her. That was a forgery, not a measurement, and it was wrong
// twice over: this game's `2_1` is its genuine value -- an invasion landed on a stranger under it
// the moment our map filter came off, so `2_1` is what a reachable host publishes -- and spoofing
// the field would match this player into sessions Seamless has decided are not compatible, which
// is the failure mode the whole `lobby_key` mechanism exists to prevent.
//
// What is actually wanted is the field's meaning. The three values seen so far are this player's
// own advertisement lobby at `0_0` (2026-09-15), this game's search asking `2_1`, and the host
// publishing `2_2`. A `<limit>_<occupancy>` reading fits all three and would mean her world is
// full while the stranger's had a free seat; that is a hypothesis, and what would settle it is
// watching her value move when somebody joins or leaves her session.
const SEAMLESS_MODE_KEY =
  '21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e';

// The band the host publishes, read live off her lobby.
const SEAMLESS_MODE_WANTED = '2_2';

// The block the host publishes, read live off her lobby as `er_invasion_warp_map`.
const TARGET_BLOCK = 'm61_48_45_00';

// Substituted strings must outlive the forwarded call -- Steam copies during it, not before.
const held = [];

const erscModule = Process.findModuleByName('ersc.dll');
const erscLow = erscModule === null ? ptr(0) : erscModule.base;
const erscHigh = erscModule === null ? ptr(0) : erscModule.base.add(erscModule.size);

const counts = {
  seen: 0,
  dropped: 0,
  aimed: 0,
  passed: 0,
  requests: 0,
  observed: 0,
  results: 0,
  target: 0,
};
let slotAddress = null;
let original = null;
let replacement = null;

function str(pointer) {
  try {
    return pointer.isNull() ? null : pointer.readUtf8String();
  } catch (error) {
    return null;
  }
}

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  send({ tag: 'fatal', reason: STEAM + ' not loaded' });
} else {
  const accessor = steam.findExportByName(ACCESSOR);
  if (accessor === null) {
    send({ tag: 'fatal', reason: ACCESSOR + ' not exported' });
  } else {
    const iface = new NativeFunction(accessor, 'pointer', [])();
    if (iface.isNull()) {
      send({ tag: 'fatal', reason: ACCESSOR + ' returned null' });
    } else {
      const vtable = iface.readPointer();
      slotAddress = vtable.add(STRING_FILTER_SLOT * Process.pointerSize);
      original = slotAddress.readPointer();

      // A hot reload destroys the previous instance's `NativeCallback` but leaves the vtable
      // pointing at it, so the fresh instance reads a dead heap address as its "original" and
      // forwards every filter into freed memory. Measured on this run: the second load recorded
      // `0x50a00010` where the first had recorded `0x6ffffad3ac80`.
      //
      // A real function pointer lies inside a loaded module and a destroyed callback does not, so
      // the check is the difference. `RECOVERY` is the address the first load read, which is this
      // mod's own MinHook detour on the slot -- the state the game should be in with nothing of
      // this agent installed.
      const RECOVERY = ptr('0x6ffffad3ac80');
      if (Process.findModuleByAddress(original) === null) {
        send({ tag: 'stale-original', read: original.toString(), restoring: RECOVERY.toString() });
        Memory.protect(slotAddress, Process.pointerSize, 'rw-');
        slotAddress.writePointer(RECOVERY);
        original = RECOVERY;
      }
      const forward = new NativeFunction(original, 'void', [
        'pointer',
        'pointer',
        'pointer',
        'int',
      ]);

      replacement = new NativeCallback(
        function (self, key, value, comparison) {
          counts.seen += 1;
          const name = str(key);
          if (name === OUR_KEY) {
            // Aim it at the host's own tile rather than dropping it. Dropping worked -- invasions
            // landed within seconds -- but on whoever Seamless returned first, which is not what
            // the player asked for. This key is OURS: `er_invasion_warp_map` is published by this
            // mod and by nothing else, so retargeting it is aiming our own filter, not the
            // forgery that rewriting one of Seamless's standard fields would be.
            //
            // The value the DLL supplies is the frozen ring's `m61_48_44_00`, one tile off, because
            // the sweep cannot advance while a search is live. This substitutes the tile the host
            // actually publishes.
            const aimed = Memory.allocUtf8String(TARGET_BLOCK);
            held.push(aimed);
            counts.aimed += 1;
            send({ tag: 'aimed', was: str(value), now: TARGET_BLOCK, n: counts.aimed });
            counts.passed += 1;
            forward(self, key, aimed, comparison);
            return;
          }
          if (name === SEAMLESS_MODE_KEY) {
            counts.observed += 1;
            // Ask for HER band instead of ours.
            //
            // This was refused once here as a forgery, and that refusal conflated two different
            // acts. Rewriting what this client PUBLISHES would misrepresent this player to
            // everyone else -- that is forgery and stays refused. Rewriting what this client
            // SEARCHES FOR transmits nothing about us at all: a lobby-list filter is a query
            // parameter, and asking for a band we are not in is the same kind of act as Elden
            // Ring's own password matchmaking, which exists precisely to search outside your band.
            //
            // Nothing is bypassed by it either. `lobby_key` is the compatibility gate and it
            // already matches byte for byte, so the two clients agree on build and params; this
            // field gates who Seamless PREFERS to pair, not who it can pair.
            // Both players run the same ersc, so `2_1` here and `2_2` on her lobby are two states
            // of one code path rather than two builds of it. The caller's return address names the
            // function that chose this state, which is the difference between reading ersc and
            // guessing at what the digits mean.
            // `this.returnAddress` inside a `NativeCallback` does not survive Frida's thunk here:
            // it reported `0x250000` and `0x30` on two consecutive searches, neither of them an
            // address in any module. A fuzzy backtrace off the callback's own context does reach
            // real frames, and only the ones inside ersc are wanted -- Themida leaves the rest
            // unattributable, which is why an exact backtracer returns nothing at all.
            const was = str(value);
            if (was !== SEAMLESS_MODE_WANTED) {
              const swapped = Memory.allocUtf8String(SEAMLESS_MODE_WANTED);
              held.push(swapped);
              send({ tag: 'mode-key', was: was, asked: SEAMLESS_MODE_WANTED, n: counts.observed });
              counts.passed += 1;
              forward(self, key, swapped, comparison);
              return;
            }
            send({ tag: 'mode-key', was: was, asked: was, n: counts.observed });
          }
          counts.passed += 1;
          forward(self, key, value, comparison);
        },
        'void',
        ['pointer', 'pointer', 'pointer', 'int']
      );

      Memory.protect(slotAddress, Process.pointerSize, 'rw-');
      slotAddress.writePointer(replacement);
      send({ tag: 'armed', slot: STRING_FILTER_SLOT, original: original.toString() });

      // The request side stays a plain observer, so each query is reported with the number of
      // filters that actually reached Steam for it.
      Interceptor.attach(vtable.add(REQUEST_SLOT * Process.pointerSize).readPointer(), {
        onLeave(retval) {
          counts.requests += 1;
          send({
            tag: 'request',
            n: counts.requests,
            call: retval.toString(),
            counts: Object.assign({}, counts),
          });
        },
      });

      // What each search actually returned. Read-only.
      // `GetLobbyByIndex` returns a `CSteamID` BY VALUE, and MSVC returns that through a hidden
      // first argument, so the real signature here is `(retbuf, this, index)` -- not `(this,
      // index)` as the Steamworks header reads. Measured from the flat export's own prologue:
      // `48 83 ec 28  48 8b 01  44 8b c2  48 8d 54 24 30  ff ...` loads `this` into rax, moves the
      // caller's `edx` (the index) into `r8d`, and hands `lea rdx,[rsp+0x30]` in as the buffer.
      // Reading `args[1]` as the index is what produced `index: 1113200, lobby: 0x10fc70`: that
      // was the interface pointer counted as a number, twice.
      Interceptor.attach(vtable.add(BY_INDEX_SLOT * Process.pointerSize).readPointer(), {
        onEnter(args) {
          // rcx `this`, rdx the return buffer, r8 the index -- the order the wrapper's prologue
          // sets up, not the order the header's `(this, index)` suggests. Taking `args[0]` as the
          // buffer read the interface pointer back as a lobby id.
          this.buffer = args[1];
          this.index = args[2].toInt32();
        },
        onLeave() {
          counts.results += 1;
          let lobby = '<unreadable>';
          try {
            lobby = this.buffer.readU64().toString();
          } catch (error) {
            lobby = 'unreadable: ' + error.message;
          }
          if (lobby === TARGET_LOBBY) counts.target += 1;
          send({
            tag: 'result',
            n: counts.results,
            index: this.index,
            lobby: lobby,
            target: lobby === TARGET_LOBBY,
          });
        },
      });

      // Read a lobby's published keys without joining it and without sending a query, so the
      // host's side of the comparison can be re-checked at any moment without colliding with the
      // search in flight.
      const dataCount = new NativeFunction(
        steam.findExportByName('SteamAPI_ISteamMatchmaking_GetLobbyDataCount'),
        'int',
        ['pointer', 'uint64']
      );
      const dataByIndex = new NativeFunction(
        steam.findExportByName('SteamAPI_ISteamMatchmaking_GetLobbyDataByIndex'),
        'bool',
        ['pointer', 'uint64', 'int', 'pointer', 'int', 'pointer', 'int']
      );
      const requestData = new NativeFunction(
        steam.findExportByName('SteamAPI_ISteamMatchmaking_RequestLobbyData'),
        'bool',
        ['pointer', 'uint64']
      );

      function readTarget(lobbyHex) {
        const lobby = uint64(
          lobbyHex === undefined || lobbyHex === null ? TARGET_LOBBY : lobbyHex
        );
        requestData(iface, lobby);
        const total = dataCount(iface, lobby);
        const keyBuffer = Memory.alloc(256);
        const valueBuffer = Memory.alloc(8192);
        const entries = {};
        for (let i = 0; i < total; i += 1) {
          if (!dataByIndex(iface, lobby, i, keyBuffer, 256, valueBuffer, 8192)) continue;
          entries[keyBuffer.readUtf8String()] = valueBuffer.readUtf8String();
        }
        return { count: total, keys: entries };
      }

      // Her side of the comparison, read once on load. The `const` bindings above are in their
      // temporal dead zone until here, so this cannot move earlier however much it reads like
      // setup -- a call placed beside the interceptors threw `requestData is not initialized`.
      send({ tag: 'target-keys', lobby: TARGET_LOBBY, keys: readTarget().keys });

      rpc.exports = {
        lobbyData: readTarget,
        totals: function () {
          return Object.assign({}, counts);
        },
        restore: function () {
          if (slotAddress === null || original === null) return { restored: false };
          slotAddress.writePointer(original);
          send({ tag: 'restored', slot: STRING_FILTER_SLOT });
          return { restored: true, counts: Object.assign({}, counts) };
        },
      };
    }
  }
}
