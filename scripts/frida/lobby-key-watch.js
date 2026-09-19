// When does this client's `lobby_key` change, and who changed it?
//
// # The question
//
// `lobby_key` is compared with `k_ELobbyComparisonEqual`, so two clients whose values differ are
// invisible to each other in both directions. It was assumed to be fixed per install -- a
// fingerprint of the loaded param tables, computed once. Measured 2026-09-18 on one machine in one
// evening, that is false: three distinct values, and two of the transitions happened with NO
// relaunch in between.
//
//   34154670c4db...   matched the host; the invasion landed        (br-20260918-212347-ad47)
//   f89c2a507f99...   same install, same ersc_settings.ini         (br-20260918-210243-0cb1)
//   8718c077e32c...   same RUNNING process as 34154670 above
//
// The last transition is the one that matters: the process did not restart, so something inside it
// recomputed the field mid-session. Until that is named, no amount of band or location work can
// make two friends meet reliably, because the pool can move under them between one search and the
// next.
//
// # Why this observes rather than detours
//
// The value is computed inside `ersc.dll` at `ersc+0xad6e0`. Detouring it works exactly once: run
// `br-20260917-222254-be6f` armed that hook and the game died 33 seconds later. Seamless
// byte-checks its own prologues, and a trampoline in one of them disarms it.
//
// So this never touches the producer. It watches the two places the finished value surfaces --
// the outgoing search filter, and `SetLobbyData` when this client publishes an advertisement --
// and takes a backtrace only on the frames where the value DIFFERS from the last one seen. A
// backtrace on every filter call would be one per search per field and would tell you nothing;
// one taken exactly at the change names the call that carried the new value.
//
// `Thread.backtrace` is FUZZY on purpose. Themida leaves most frames unattributable and an exact
// backtracer returns nothing at all on this target; fuzzy reaches real frames, and the ones inside
// `ersc.dll` are the only ones worth reading.
//
// Read-only: `Interceptor.attach` observers and getters. Nothing is written, no lobby is joined,
// and Frida reverts its own trampolines on detach -- including an abrupt one -- so a killed
// watcher cannot leave this game wedged the way a vtable pointer swap can.
'use strict';

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;
const REQUEST_SLOT = 4;
const SET_LOBBY_DATA_SLOT = 20;
const POOL_KEY = 'lobby_key';

const counts = { searches: 0, seen: 0, changes: 0, publishes: 0 };
let lastSent = null;
let lastPublished = null;

function str (pointer) {
  try {
    return pointer.isNull() ? null : pointer.readUtf8String();
  } catch (error) {
    return null;
  }
}

function where (address) {
  const module = Process.findModuleByAddress(address);
  if (module === null) return String(address);
  return `${module.name}+0x${address.sub(module.base).toString(16)}`;
}

// Only the frames inside a real module are worth printing; the rest are Themida noise.
function frames (context) {
  try {
    return Thread.backtrace(context, Backtracer.FUZZY)
      .map(where)
      .filter((name) => name.indexOf('+0x') !== -1)
      .slice(0, 12);
  } catch (error) {
    return [`<backtrace failed: ${error.message}>`];
  }
}

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  send({ tag: 'fatal', reason: `${STEAM} not loaded` });
} else {
  const accessor = steam.findExportByName(ACCESSOR);
  if (accessor === null) {
    send({ tag: 'fatal', reason: `${ACCESSOR} not exported` });
  } else {
    const iface = new NativeFunction(accessor, 'pointer', [])();
    if (iface.isNull()) {
      send({ tag: 'fatal', reason: `${ACCESSOR} returned null` });
    } else {
      const vtable = iface.readPointer();
      const slot = (n) => vtable.add(n * Process.pointerSize).readPointer();

      // The search side: what this client ASKS for.
      Interceptor.attach(slot(STRING_FILTER_SLOT), {
        onEnter (args) {
          if (str(args[1]) !== POOL_KEY) return;
          const value = str(args[2]);
          if (value === null) return;
          counts.seen += 1;
          if (value === lastSent) return;
          counts.changes += 1;
          const was = lastSent;
          lastSent = value;
          send({
            tag: was === null ? 'first-key' : 'key-changed',
            n: counts.changes,
            search: counts.searches,
            was: was,
            now: value,
            stack: frames(this.context),
          });
        },
      });

      // The publish side: what this client ADVERTISES, when it hosts. Seamless writes its
      // advertisement once at CreateLobby, so a second value here is a genuine recompute rather
      // than a repeat.
      Interceptor.attach(slot(SET_LOBBY_DATA_SLOT), {
        onEnter (args) {
          if (str(args[2]) !== POOL_KEY) return;
          const value = str(args[3]);
          if (value === null) return;
          counts.publishes += 1;
          if (value === lastPublished) return;
          const was = lastPublished;
          lastPublished = value;
          send({
            tag: 'published',
            n: counts.publishes,
            was: was,
            now: value,
            stack: frames(this.context),
          });
        },
      });

      Interceptor.attach(slot(REQUEST_SLOT), {
        onLeave () {
          counts.searches += 1;
        },
      });

      send({ tag: 'armed', watching: POOL_KEY, slots: [STRING_FILTER_SLOT, SET_LOBBY_DATA_SLOT] });
    }
  }
}

rpc.exports = {
  totals () {
    return { ...counts, lastSent, lastPublished };
  },
};
