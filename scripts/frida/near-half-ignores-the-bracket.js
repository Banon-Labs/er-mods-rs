// Does a picked bracket reach the wire during the NEAR half of a search?
//
// # The report this is measuring
//
// 2026-09-18: "it was my feature not working" -- the player had `RL71-100` selected in the panel
// and invaded `RL20` characters anyway. Run `br-20260919-004438-566d` timed it: they cancelled
// search #1 (line 609), picked `RL71-100` (631, 636), started search #2 (644) and landed a match
// (663), with no `the near half is over` line between the last two. So the second search never
// reached its far half.
//
// `invade_difficulty::band_for` opens with `if !in_far_half() { return None; }`, which would make
// every query of that search carry Seamless's own band while the panel went on showing the pick.
// That is a reading of the source, not a measurement, and the two have disagreed before -- a gate
// can be bypassed by a caller nobody remembered.
//
// # What it does
//
// Picks a bracket through the crate's own export, starts a search, and reports the band on every
// outgoing `AddRequestLobbyListStringFilter` -- the same slot Seamless puts the field through.
// The verdict is one field: whether the band that left this process is the one that was picked.
//
//   picked RL71-100 / +4 to +12  =  bands 3 and 1  =  `3_1`
//
// A run that reports `own` is the defect reproduced: the pick was live, the panel would have shown
// it, and the query asked for the character's own bracket regardless. A run that reports `picked`
// says the gate is not where the source says it is, and the fix would be somewhere else entirely.
//
// Read-only apart from the two drive exports, and neither touches `ersc.dll`.
'use strict';

const DRIVER = 'er_invasion_warp.dll';

// `er_invasion_warp_pick_brackets` takes band-plus-one per axis, so 0 means "leave it at mine".
// Band 3 is `RL71-100`, band 1 is `+4 to +12` -- the pair from the report.
const PICK_LEVEL_BAND = 3;
const PICK_WEAPON_BAND = 1;
const EXPECTED_PICK = PICK_LEVEL_BAND + '_' + PICK_WEAPON_BAND;

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;

const bands = {};

function report (payload) {
  send(payload);
}

function driver (name, argTypes, args) {
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
    return new NativeFunction(entry, 'int', argTypes).apply(null, args);
  } catch (error) {
    report({ tag: 'error', why: name + ' threw: ' + error.message });
    return null;
  }
}

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
      let value;
      try {
        value = args[2].isNull() ? null : args[2].readUtf8String();
      } catch (error) {
        return;
      }
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
        on_the_wire: value,
        picked: EXPECTED_PICK,
        // One field, named on the frame the value first appears rather than tallied afterwards.
        verdict: value === EXPECTED_PICK
          ? 'picked -- the bracket reached the wire'
          : 'own -- the pick did not bind to this query',
        seen: bands
      });
    }
  });
  report({ tag: 'wire', at: slot.toString() });
}

watchTheWire();
report({
  tag: 'pick',
  level_band: PICK_LEVEL_BAND,
  weapon_band: PICK_WEAPON_BAND,
  accepted: driver('er_invasion_warp_pick_brackets', ['uint32', 'uint32'],
    [PICK_LEVEL_BAND + 1, PICK_WEAPON_BAND + 1])
});
// The near half is what is under test, so this starts a plain search and does NOT force the
// finger's `Both near and far` row. A search with no row behind it takes no widening rung at all,
// which is precisely the case the report came from.
report({
  tag: 'search',
  armed: driver('er_invasion_warp_request_invade', [], []),
  note: 'a real invasion search; this run owns it'
});
