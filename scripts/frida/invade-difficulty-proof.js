// Does the invade difficulty actually change the band that goes out on the wire?
//
// # What is being proven
//
// The setting's whole effect is one string. Seamless publishes `<level band>_<weapon band>` and
// filters it with `k_ELobbyComparisonEqual`, so the difficulty is proven when, and only when, a
// query leaves this process carrying a band above the character's own -- and disproven by a run
// where every query still carries the character's own band. A log line saying the setting changed
// is not the proof; it is the thing this checks against the wire.
//
// Measured bracket tables (bd `seamless-band-tables-measured-level-8-weapon-3-2026-09-18`):
// levels `[20, 40, 70, 100, 125, 150, 200, 300]`, weapons `[3, 12, 20]`. The launcher's character
// is `RL9 +2`, which falls in band `0` on both axes, so this run's own band is `0_0` and `Harder`
// is `2_2`.
//
// # Why this drives three exports rather than pressing three buttons
//
// Each replaces a control that has been measured undriveable, and each is the crate's own export
// rather than a hook:
//
//   set_invade_difficulty   the difficulty's only front end is the F4 panel, which needs a mouse
//                           click on a row; there is no key for it
//   force_search_range      the bounds popup answers row 0 under every agent-driven input tried,
//                           three times, so `Both near and far` cannot be reached by pressing
//   use_item                the finger is what sets the reach at all; `request_invade` alone arms
//                           a search with no row behind it, and a search with no row takes no
//                           widening rung and never reaches the far half
//
// Read-only otherwise: one `Interceptor` on `steam_api64.dll`'s string-filter slot, nothing on
// `ersc.dll`.
'use strict';

const DRIVER = 'er_invasion_warp.dll';

// `InvadeDifficulty::ALL` index 2. One bracket is a small step on a `RL9` character -- band 1 is
// `RL21-40` -- and two is unambiguous on both axes at once.
const DIFFICULTY_HARDER = 2;
const EXPECTED_OWN_BAND = '0_0';
const EXPECTED_FAR_BAND = '2_2';

// `FORCED_RANGE_NEAR_AND_FAR`.
const RANGE_NEAR_AND_FAR = 2;

// `with_category(102)` -- the Bloody Finger, `0x40000000 | 102`.
const BLOODY_FINGER = 0x40000066;

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;

const bands = {};
let uses = 0;

function report (payload) {
  send(payload);
}

function str (pointer) {
  try {
    return pointer.isNull() ? null : pointer.readUtf8String();
  } catch (error) {
    return null;
  }
}

function driver (name, returnType, argTypes, args) {
  const owner = Process.findModuleByName(DRIVER);
  if (owner === null) {
    report({ tag: 'error', why: DRIVER + ' is not in this process' });
    return null;
  }
  const entry = owner.findExportByName(name);
  if (entry === null) {
    report({ tag: 'error', why: DRIVER + ' does not export ' + name });
    return null;
  }
  try {
    return new NativeFunction(entry, returnType, argTypes).apply(null, args);
  } catch (error) {
    report({ tag: 'error', why: name + ' threw: ' + error.message });
    return null;
  }
}

// Every band that leaves this process, counted by value.
//
// Counted rather than streamed: a search sends one query every fifteen seconds and each carries
// the same band, so a line per call would bury the one transition that matters under fifty copies
// of the line before it.
function watchTheWire () {
  const accessor = Module.findGlobalExportByName(ACCESSOR)
    || Module.findExportByName(STEAM, ACCESSOR);
  if (accessor === null) {
    report({ tag: 'error', why: ACCESSOR + ' is not exported here' });
    return;
  }
  const iface = new NativeFunction(accessor, 'pointer', [])();
  if (iface.isNull()) {
    report({ tag: 'error', why: 'the matchmaking interface is null' });
    return;
  }
  const slot = iface.readPointer().add(STRING_FILTER_SLOT * Process.pointerSize).readPointer();
  Interceptor.attach(slot, {
    onEnter: function (args) {
      const value = str(args[2]);
      if (value === null || !/^\d+_\d+$/.test(value)) {
        return;
      }
      const first = bands[value] === undefined;
      bands[value] = (bands[value] || 0) + 1;
      if (!first) {
        return;
      }
      report({
        tag: 'band',
        band: value,
        // Named on the frame it first appears, so the verdict is not something read off a tally
        // afterwards. `own` is the near half working; `far` is the setting working; anything else
        // is neither and says so.
        verdict: value === EXPECTED_FAR_BAND
          ? 'far -- the difficulty reached the wire'
          : (value === EXPECTED_OWN_BAND ? 'own -- the near half, as expected' : 'unexpected'),
        seen: bands
      });
    }
  });
  report({ tag: 'wire', at: slot.toString() });
}

function start () {
  const set = driver('er_invasion_warp_set_invade_difficulty', 'int', ['uint32'], [DIFFICULTY_HARDER]);
  report({ tag: 'difficulty', asked: DIFFICULTY_HARDER, in_force: set, expect: EXPECTED_FAR_BAND });
  driver('er_invasion_warp_force_search_range', 'int', ['uint32'], [RANGE_NEAR_AND_FAR]);
  report({ tag: 'range', forced: RANGE_NEAR_AND_FAR });
  useTheFinger();
}

// Ask for the finger. The request is resolved to an inventory index on the next game tick, so it
// does not have to land on a particular frame; the log says plainly when the item is not held.
function useTheFinger () {
  uses += 1;
  const asked = driver('er_invasion_warp_use_item', 'int', ['uint32'], [BLOODY_FINGER]);
  report({ tag: 'finger', attempt: uses, recorded: asked, seen_so_far: bands });
}

watchTheWire();
start();
