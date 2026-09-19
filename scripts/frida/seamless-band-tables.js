// What are the two ceilings of Seamless's `<level band>_<weapon band>` field?
//
// # The question, and why it has one answer rather than an estimate
//
// Static RE of `ersc-2.0.1` pinned the producer at `ersc+0xa97b0` (bd
// `seamless-band-is-two-threshold-tables-at-ersc-0xa97b0-2026-09-18`). Both halves of the value are
// a threshold-table lookup over a `std::vector` held by the object passed in `rcx`:
//
//   level  band  =  index of the first `[rcx+0x70]` u32 >= the character level, else the count
//   weapon band  =  index of the first `[rcx+0x88]` u8  >= the weapon upgrade, else the count
//
// So the MAXIMUM band on each axis is exactly that vector's length, and pinning it needs only
// `end - begin`. The tables themselves come out of the same read and are worth more than the two
// numbers: they are every bracket edge, which nothing in this repo has ever measured.
//
// # Why this is a hook and not a memory scan
//
// Both vectors live on the heap, the object has no static home, and the function has no direct
// callers -- it is a lambda body reached through a `std::function` thunk. A scan of the process's
// writable ranges for a plausible pointer pair is gigabytes of qwords walked in JavaScript and
// would still need this value to confirm which candidate it found.
//
// # The prologue risk, and what is done about it
//
// Seamless byte-checks its own prologues. A MinHook detour at `ersc+0xad6e0` killed the game 33
// seconds in (run `br-20260917-222254-be6f`), and the `ersc_observers` MinHook pair kills it at
// about 25. A Frida `Interceptor` has been survivable at one ersc site for a whole session of
// invading, which is evidence that the two patchers are not equivalent here -- it is not a promise.
//
// So this detaches ITSELF the moment it has read the tables once. The value is a constant for the
// life of the process, one call answers the question completely, and the trampoline is reverted
// before a periodic check has much of a window to find it. Nothing is written, and the arguments
// are read, never altered.
//
// # What makes the call happen
//
// Measured 2026-09-18, run `br-20260918-235543-826e`: the hook armed cleanly on a character
// standing in a world -- the game survived it, 125 threads, heartbeat unbroken -- and the function
// was not called once in 110 seconds. Standing in a world is not enough; the band is built when
// Seamless assembles the lobby data for a search, which is the same place its `lobby_key` was
// caught being rebuilt mid-session (bd `seamless-lobby-key-is-recomputed-mid-session-at-ersc-0xac4d1`).
//
// So this starts one. `er_invasion_warp_request_invade` is the crate's own export and arms a search
// on the game task that owns the session's mutex, with no Interceptor anywhere near ersc's invade
// action -- see `scripts/frida/er-request-invade.js` for why calling `ersc+0x25850` from an RPC
// thread parks forever instead. That is a real invasion search: it can land this character in a
// stranger's world, which is ordinary play for an invader and is why the run is agent-owned.
//
// `AddRequestLobbyListStringFilter` is watched alongside it, read-only, so a run where the
// producer never fires still says which band went out on the wire. Those two together tell the two
// failure modes apart: a search that never started, and a search that started with a band string
// Seamless had cached from before the hook was armed.
'use strict';

const MODULE = 'ersc.dll';

// The crate's own export, and the only thing here that drives anything.
const DRIVER_MODULE = 'er_invasion_warp.dll';
const DRIVER_EXPORT = 'er_invasion_warp_request_invade';

// Steam's matchmaking interface, for the read-only witness on the outgoing filter.
const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;

// The drive is a single call at load, and that is deliberate rather than optimistic. The watcher
// only attaches to a process that is already in a world -- `scripts/er-frida-up.py` refuses
// otherwise -- and `er_invasion_warp_request_invade` records a request that the next game tick
// finding the session idle acts on, so it does not need to land on a particular frame. Measured
// 2026-09-18: the tables arrived 20ms after the request. A timer retry would be waiting on a
// readiness the DLL already waits on, which is the pattern `scripts/check-no-timeouts.py` exists
// to refuse; reload the agent file to ask again, which is the workflow `er-frida-watch.py` is for.

// Measured against `vendor-archive/seamless/ersc-2.0.1.dll`. A different Seamless build moves this
// and the read below would be pointed at whatever code now lives there, so the banner prints the
// module size as the build's fingerprint and the caller checks it before believing the numbers.
const BAND_RVA = 0xa97b0;

// The object's two vectors, as byte offsets from `rcx`.
const LEVEL_BEGIN = 0x70;
const LEVEL_END = 0x78;
const WEAPON_BEGIN = 0x88;
const WEAPON_END = 0x90;

// A table longer than this is not a bracket table, it is a wild pointer pair. Bailing out loudly
// beats printing four thousand numbers as if they were bracket edges.
const SANE_MAX_ENTRIES = 64;

let listener = null;
let answered = false;

function readVector (object, beginOffset, endOffset, elementSize) {
  const begin = object.add(beginOffset).readPointer();
  const end = object.add(endOffset).readPointer();
  if (begin.isNull() || end.isNull()) {
    return { error: 'null vector' };
  }
  const span = end.sub(begin).toInt32();
  if (span < 0 || span % elementSize !== 0) {
    return { error: 'span ' + span + ' is not a whole number of ' + elementSize + '-byte entries' };
  }
  const count = span / elementSize;
  if (count > SANE_MAX_ENTRIES) {
    return { error: 'count ' + count + ' is past anything a bracket table would hold' };
  }
  const entries = [];
  for (let i = 0; i < count; i += 1) {
    const at = begin.add(i * elementSize);
    entries.push(elementSize === 4 ? at.readU32() : at.readU8());
  }
  return { count: count, entries: entries };
}

function report (payload) {
  send(payload);
}

const module = Process.findModuleByName(MODULE);
if (module === null) {
  report({ tag: 'error', why: MODULE + ' is not loaded in this process' });
} else {
  const base = module.base;
  report({
    tag: 'armed',
    module: MODULE,
    base: base.toString(),
    // The build's fingerprint. `ersc-2.0.1` is 13832192 bytes once loaded; a different number
    // means the `BAND_RVA` above was measured against code that is no longer there, and every
    // number this agent prints afterwards is whatever happens to live at that offset now.
    size: module.size,
    at: base.add(BAND_RVA).toString(),
    note: 'detaches after the first call; the tables are constant for the life of the process'
  });

  listener = Interceptor.attach(base.add(BAND_RVA), {
    onEnter: function (args) {
      if (answered) {
        return;
      }
      answered = true;
      // `rcx` is the owning object, `r8d` the character level, `r9b` the weapon upgrade level.
      // Arguments 2 and 3 are read only to print them beside the tables, so the lookup can be
      // reproduced by hand from this one line rather than taken on trust.
      const object = args[0];
      const level = args[2].toUInt32() >>> 0;
      const weapon = args[3].toUInt32() & 0xff;
      let levels;
      let weapons;
      try {
        levels = readVector(object, LEVEL_BEGIN, LEVEL_END, 4);
        weapons = readVector(object, WEAPON_BEGIN, WEAPON_END, 1);
      } catch (error) {
        report({ tag: 'error', why: 'reading the vectors faulted: ' + error.message });
        return;
      }
      report({
        tag: 'tables',
        object: object.toString(),
        level_argument: level,
        weapon_argument: weapon,
        level_table: levels,
        weapon_table: weapons,
        // The whole point of the run. A value above every threshold lands on the count, so the
        // count IS the top band on that axis.
        max_level_band: levels.count,
        max_weapon_band: weapons.count
      });
      // Off the moment the answer is in hand, so Seamless's prologue check has as little window as
      // this can give it.
      if (listener !== null) {
        listener.detach();
        listener = null;
        report({ tag: 'detached' });
      }
    }
  });

  watchTheWire();
  driveASearch();
}

// Read the band that actually goes out on a query, whether or not the producer above fires.
//
// This is the control. With it, a run that reports no tables still distinguishes "no search ever
// started" (no filter line at all) from "the search used a string Seamless had already built"
// (a filter line carrying a band, and no producer call behind it) -- and those want opposite next
// steps. It touches `steam_api64.dll`, never ersc, so it costs nothing this agent is worried about.
function watchTheWire () {
  const accessor = Module.findGlobalExportByName(ACCESSOR)
    || Module.findExportByName(STEAM, ACCESSOR);
  if (accessor === null) {
    report({ tag: 'wire', armed: false, why: ACCESSOR + ' is not exported here' });
    return;
  }
  let iface;
  try {
    iface = new NativeFunction(accessor, 'pointer', [])();
  } catch (error) {
    report({ tag: 'wire', armed: false, why: 'the accessor threw: ' + error.message });
    return;
  }
  if (iface.isNull()) {
    report({ tag: 'wire', armed: false, why: 'the matchmaking interface is null' });
    return;
  }
  const slot = iface.readPointer().add(STRING_FILTER_SLOT * Process.pointerSize).readPointer();
  Interceptor.attach(slot, {
    onEnter: function (args) {
      const key = str(args[1]);
      const value = str(args[2]);
      // `<digits>_<digits>` is the band's shape, matched on the value because 2.0.x hashes its key
      // names per build. Every other filter on the query is left unreported; one line per band is
      // the point, several per search is noise.
      if (value === null || !/^\d+_\d+$/.test(value)) {
        return;
      }
      if (value === lastBandOnTheWire) {
        return;
      }
      lastBandOnTheWire = value;
      report({ tag: 'wire', armed: true, key: key, band: value });
    }
  });
  report({ tag: 'wire', armed: true, at: slot.toString() });
}

let lastBandOnTheWire = null;

function str (pointer) {
  try {
    return pointer.isNull() ? null : pointer.readUtf8String();
  } catch (error) {
    return null;
  }
}

// Ask the crate to start a search, which is what makes Seamless build the value this measures.
//
// One call, not a retry loop: `arm_invade_request` records a request that the next game tick
// finding the session idle acts on, so it outlives the frame it was made on and there is nothing
// for a retry to wait for.
function driveASearch () {
  const owner = Process.findModuleByName(DRIVER_MODULE);
  if (owner === null) {
    report({ tag: 'drive', armed: false, why: DRIVER_MODULE + ' is not in this process' });
    return;
  }
  const entry = owner.findExportByName(DRIVER_EXPORT);
  if (entry === null) {
    report({ tag: 'drive', armed: false, why: DRIVER_MODULE + ' does not export ' + DRIVER_EXPORT });
    return;
  }
  let armed;
  try {
    armed = new NativeFunction(entry, 'int', [])();
  } catch (error) {
    report({ tag: 'drive', armed: false, why: 'the export threw: ' + error.message });
    return;
  }
  report({ tag: 'drive', armed: armed, note: 'a real invasion search; this run owns it' });
}
