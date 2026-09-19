// Where a D-pad Right press stops, between the pad poll and the right-hand armament switch.
//
// The symptom, reported 2026-09-19: "After invading, I can't press right on my dpad to switch
// weapons on my right side". Every hook this repo's DLLs had armed on that run was cleared by
// reading the run log and the 1.17 decompile -- the deathblight penalty never armed, the
// quick-slot detour never answered, the DirectInput suppression blanks only the mouse's left
// button and refuses joystick states by size, and `er-focus-input`'s `DLUID+0x88d` can only let
// the pad poll continue. So the question is no longer "which of ours ate it" but "does the press
// arrive at all", and those are answered at different addresses.
//
// Three places, in the order the press travels:
//
//   1. `DLUID::PadDevice::Poll` stores the raw `XINPUT_GAMEPAD.wButtons` at `device + 0x890`.
//      That is the press as the driver saw it, before any game logic. If `0x0008` never appears
//      here, nothing in this process ate it -- the pad, the driver or the container did.
//   2. The same store, watched for the bit rising and falling, gives the press as an edge. A bit
//      that arrives and stays stuck reads identically to a bit that never arrives if only the
//      level is sampled, and the two want opposite fixes.
//   3. Whether anything downstream runs on that edge. Silence downstream while the bit is moving
//      upstream localises the loss to game logic rather than to input.
//
// Read-only. One `Interceptor.onLeave` that reads two integers out of the object the function was
// already called with. Nothing is written, no page protection changes, and no watchpoint is armed
// -- `MemoryAccessMonitor` on a live game object killed this target once already.
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/dpad-right-where-it-dies.js

'use strict';

// `DLUID::PadDevice::Poll`, the XInput-reading half of the pad device.
//
// Read off the 1.17.0 Ghidra dump at `0x141f6d8d0` and carried to the installed 1.17.1 by
// `scripts/map-rvas-1170-to-1171.py`: the rva is at or above the `0xafefe9` boundary, so the
// function moved `+0x70`. Its shape in the decompile is the anchor -- it calls `XInputGetState`
// and stores `wButtons` to `param_1 + 0x890`.
const POLL_RVA = 0x1f6d940;

// `XINPUT_GAMEPAD.wButtons` as the poll stored it, on the pad device object.
const DEVICE_BUTTONS_OFFSET = 0x890;

// The stick/trigger block the poll fills beside the buttons, read only to tell a device that is
// being polled but reporting nothing from a device that is not being polled at all.
const DEVICE_LEFT_STICK_X_OFFSET = 0x89c;

// `XINPUT_GAMEPAD_DPAD_RIGHT`. The bit the player is pressing.
const DPAD_RIGHT = 0x0008;

// The rest of the D-pad, so a report can say whether the whole pad is dead or only this direction.
const DPAD_UP = 0x0001;
const DPAD_DOWN = 0x0002;
const DPAD_LEFT = 0x0004;

// `CSMenuMan`, on 1.17. The 1.16.2 notes in this repo call it "inputmgr" because that is what its
// two input arrays are used for, but the data map names it: `0x3d6b7b0 -> 0x3d6f820`, 846/846
// references agreeing, which is as firm as a global gets here.
//
// Read to answer one question the pad poll cannot: whether a press that arrives at the driver is
// being consumed as a MENU event while the player is in gameplay. That would mean something is
// holding menu input open over the world -- an overlay is the obvious candidate -- and it is a
// different fault from the game refusing an armament switch on its own state.
const CS_MENU_MAN_RVA = 0x3d6f820;

// Logical input-event array: one i32 per event id. The game's node update writes
// `CSMenuMan + 0xdc + eventId * 4`.
const MENU_EVENT_ARRAY_OFFSET = 0xdc;

// How many event ids to scan. The array is indexed by menu event id and the ids this repo has
// named all sit well inside this; a bounded scan beats a guess at the exact length.
const MENU_EVENT_SCAN_COUNT = 128;

// Keystate bitmap, one byte per event id, the other half of the same pair.
const MENU_KEYSTATE_OFFSET = 0x90;

// `GameDataMan`, 1.16.2 `0x3d5df38` -> 1.17 `0x3d61f98`, 642/642 references agreeing.
const GAME_DATA_MAN_RVA = 0x3d61f98;

// `GameDataMan + 0x8` -> `PlayerGameData`, and `PlayerGameData + 0x2b0` -> `EquipGameData`, which
// is embedded rather than pointed to.
const PLAYER_GAME_DATA_OFFSET = 0x8;
const EQUIP_GAME_DATA_OFFSET = 0x2b0;

// `CS::EquipGameData::GetParamIdInSlot(egd, ChrAsmSlot) -> int`, the game's own read-back oracle
// for what is in a slot. 1.16.2 `0x1402470e0`, and the rva is below the `0xafefe9` boundary, so
// 1.17.0 and the installed 1.17.1 are both the same address -- confirmed unique by
// `map-rvas-1162-to-1170.py` on a 40-byte signature.
const GET_PARAM_ID_IN_SLOT_RVA = 0x2470e0;

// `ChrAsm` inside `EquipGameData`. See `armamentState` for how this is derived.
const CHR_ASM_OFFSET = 0x6c;

// The cycle positions themselves, inside `ChrAsm`.
//
// `ChrAsm` opens with two unnamed ints, then `equipment: ChrAsmEquipment`, which is `arm_style`
// followed by six `u32` slot indices. Ghidra proves the indexing independently: the 1.16.2 dump
// names `getSelectedWeaponSlotIndex(armStyle*, n)` at `0x1404c4b50`, and its whole body is
// `if (5 < n) DLPanic("..\\Source\\Game\\Chr\\CSChrArmStyle.cpp", ...); return armStyle[n + 1];`
// -- six indices, based at `arm_style`, which is exactly the upstream layout.
//
//   ChrAsm + 0x00  unnamed
//   ChrAsm + 0x04  unnamed
//   ChrAsm + 0x08  arm_style          <- `getSelectedWeaponSlotIndex`'s base
//   ChrAsm + 0x0c  left_weapon_slot   <- n = 0
//   ChrAsm + 0x10  right_weapon_slot  <- n = 1, the number D-pad Right increments
//   ChrAsm + 0x14  left_arrow_slot, and so on to 0x20
//
// The first version of this read `+0x00` and `+0x04`, which are the two unnamed ints and not the
// cycle at all -- a reading that would have reported "frozen" for a cycle moving perfectly.
const ARM_STYLE_OFFSET = CHR_ASM_OFFSET + 0x08;
const LEFT_WEAPON_SLOT_OFFSET = CHR_ASM_OFFSET + 0x0c;
const RIGHT_WEAPON_SLOT_OFFSET = CHR_ASM_OFFSET + 0x10;

// The `ChrAsmSlot` values that answer this question.
//
// Negative slots are SELECTORS, not indices: the game resolves the player's current cycle position
// into a concrete index, `-1` giving `sel * 2 + 1`. The weapon block interleaves the hands
// (0 = Left 1, 1 = Right 1, 2 = Left 2, 3 = Right 2, 4 = Left 3, 5 = Right 3), so odd is the right
// hand and `-1` is "whatever the right hand currently has cycled in".
//
// That distinction is the whole measurement. Asking `-1` says whether the CYCLE moved; asking 1, 3
// and 5 says whether there was anywhere for it to move TO. A press that changes neither, with only
// one of the three right-hand slots occupied, is the game declining to cycle rather than the press
// being eaten -- and those want completely different fixes.
const SLOTS = [
  { slot: -1, name: 'right-active (cycle position)' },
  { slot: 1, name: 'Right 1' },
  { slot: 3, name: 'Right 2' },
  { slot: 5, name: 'Right 3' },
  { slot: -2, name: 'left-active (cycle position)' },
];

const base = Process.getModuleByName('eldenring.exe').base;

const getParamIdInSlot = new NativeFunction(
  base.add(GET_PARAM_ID_IN_SLOT_RVA),
  'int',
  ['pointer', 'int'],
  'win64',
);

// The right-hand armament state as the game itself reports it, or `null` before a character
// exists. A pure read-back through the engine's own getter -- nothing is written.
function armamentState () {
  let equip;
  try {
    const manager = base.add(GAME_DATA_MAN_RVA).readPointer();
    if (manager.isNull()) return null;
    const pgd = manager.add(PLAYER_GAME_DATA_OFFSET).readPointer();
    if (pgd.isNull()) return null;
    equip = pgd.add(EQUIP_GAME_DATA_OFFSET);
  } catch (error) {
    return null;
  }
  const state = {};
  for (const entry of SLOTS) {
    try {
      state[entry.name] = getParamIdInSlot(equip, entry.slot);
    } catch (error) {
      state[entry.name] = 'unreadable';
    }
  }
  // The two integers `ChrAsm` opens with, which are what a D-pad cycle moves. Reading them turns
  // one unanswered question into two answerable ones: an index that moves while the slot contents
  // do not is the cycle working and the equipment failing to follow, and an index that does not
  // move is the press never reaching the cycle at all. Those want opposite fixes, and the slot
  // read alone cannot tell them apart.
  //
  // Offsets derived from `../fromsoftware-rs`'s `#[repr(C)] EquipGameData` rather than guessed:
  // vftable 0x00, `equipment_item_idx_list: [u32; 22]` 0x08..0x60, `unk60: usize` 0x60,
  // `unk68: u32` 0x68, so `chr_asm` lands at 0x6c.
  try {
    state.armStyle = equip.add(ARM_STYLE_OFFSET).readU32();
    state.leftWeaponSlot = equip.add(LEFT_WEAPON_SLOT_OFFSET).readU32();
    state.rightWeaponSlot = equip.add(RIGHT_WEAPON_SLOT_OFFSET).readU32();
  } catch (error) {
    state.armStyle = 'unreadable';
    state.leftWeaponSlot = 'unreadable';
    state.rightWeaponSlot = 'unreadable';
  }
  return state;
}

// The last snapshot taken while no D-pad direction was held, and the diff against it.
//
// A list of everything that is live tells you almost nothing -- the table carries ~25 entries at
// rest, which is why the first version of this printed a wall of ids that meant nothing. What
// answers the question is the DELTA: the id whose value changes between an idle frame and the
// frame the button is down IS this press's logical event. If no id changes at all, the press never
// became a logical event, and it died in the mapping between the pad device and the event table
// rather than anywhere downstream.
let idleSnapshot = null;

function snapshotMap () {
  const live = liveMenuEvents();
  if (live === null) return null;
  const map = {};
  for (const entry of live) {
    const key = entry.id + (entry.keystate !== undefined ? ':key' : ':val');
    map[key] = entry.keystate !== undefined ? entry.keystate : entry.value;
  }
  return map;
}

function diffAgainstIdle (now) {
  if (idleSnapshot === null || now === null) return null;
  const changed = [];
  const keys = new Set(Object.keys(idleSnapshot).concat(Object.keys(now)));
  for (const key of keys) {
    const was = idleSnapshot[key];
    const is = now[key];
    if (was !== is) changed.push({ id: key, idle: was === undefined ? 'absent' : was, pressed: is === undefined ? 'absent' : is });
  }
  return changed;
}

// Read the menu event ids that are live right now, or `null` if the manager is not up.
//
// Bounded and fault-closed: a null singleton before the menu system exists is the ordinary case,
// not an error, and a failed read returns nothing rather than throwing inside a per-frame hook.
function liveMenuEvents () {
  let manager;
  try {
    manager = base.add(CS_MENU_MAN_RVA).readPointer();
  } catch (error) {
    return null;
  }
  if (manager.isNull()) return null;
  const live = [];
  try {
    for (let id = 0; id < MENU_EVENT_SCAN_COUNT; id++) {
      const value = manager.add(MENU_EVENT_ARRAY_OFFSET + id * 4).readS32();
      if (value !== 0) live.push({ id: '0x' + id.toString(16), value: value });
      const key = manager.add(MENU_KEYSTATE_OFFSET + id).readU8();
      if (key !== 0) live.push({ id: '0x' + id.toString(16), keystate: '0x' + key.toString(16) });
    }
  } catch (error) {
    return null;
  }
  return live;
}

const counts = {
  polls: 0,
  everNonZero: 0,
  dpadRightFrames: 0,
  dpadRightEdges: 0,
  dpadUpEdges: 0,
  dpadDownEdges: 0,
  dpadLeftEdges: 0,
};

// Per-device, because the poll runs for every connected pad and slot 0 is not guaranteed to be the
// player's. A device that never reports a button is a device nobody is holding, and merging them
// would hide that.
const devices = new Map();

// A press whose armament reading is still owed a follow-up, and how many polls to let pass first.
//
// The engine does not cycle the weapon on the same frame the bit rises, so the "after" sample has
// to be taken later or every working switch would read as a dead one. Counted in polls rather than
// milliseconds because polls are the thing that actually advances the input pipeline -- a wall
// clock would sample early on a stutter and late on a fast frame.
let pendingCompare = null;
const COMPARE_AFTER_POLLS = 30;

function deviceState (device) {
  const key = device.toString();
  let state = devices.get(key);
  if (state === undefined) {
    state = { previous: 0, polls: 0, everNonZero: false };
    devices.set(key, state);
  }
  return state;
}

function names (buttons) {
  const held = [];
  if ((buttons & DPAD_UP) !== 0) held.push('up');
  if ((buttons & DPAD_DOWN) !== 0) held.push('down');
  if ((buttons & DPAD_LEFT) !== 0) held.push('left');
  if ((buttons & DPAD_RIGHT) !== 0) held.push('right');
  return held.join('+') || 'none';
}

const poll = base.add(POLL_RVA);

Interceptor.attach(poll, {
  onEnter: function (args) {
    this.device = args[0];
  },
  onLeave: function () {
    const device = this.device;
    if (device === undefined || device.isNull()) return;
    let buttons;
    let stickX;
    try {
      buttons = device.add(DEVICE_BUTTONS_OFFSET).readU16();
      stickX = device.add(DEVICE_LEFT_STICK_X_OFFSET).readFloat();
    } catch (error) {
      return;
    }
    counts.polls++;
    // Armed from here rather than at load, because `GameDataMan` is null until a character exists
    // and an agent that reloads mid-session would otherwise never get its watchpoint.
    watchRightWeaponSlot();
    const state = deviceState(device);
    state.polls++;
    // The owed follow-up. This is the line that answers the user's question -- whether the right
    // hand actually changed weapon -- and it is the only one of these that is a direct measurement
    // of the thing they are looking at rather than of the input in front of it.
    if (pendingCompare !== null && counts.polls - pendingCompare.at >= COMPARE_AFTER_POLLS) {
      const owed = pendingCompare;
      pendingCompare = null;
      const after = armamentState();
      const changed = [];
      if (owed.before !== null && after !== null) {
        for (const key of Object.keys(after)) {
          if (after[key] !== owed.before[key]) {
            changed.push({ slot: key, from: owed.before[key], to: after[key] });
          }
        }
      }
      send({
        tag: 'armament-after',
        n: owed.n,
        polls: counts.polls - owed.at,
        before: owed.before,
        after: after,
        changed: changed,
        verdict: changed.length === 0
          ? 'nothing moved -- the engine did not cycle the right hand on this press'
          : 'the right hand cycled, so the switch itself works',
      });
    }
    if (buttons !== 0 && !state.everNonZero) {
      state.everNonZero = true;
      counts.everNonZero++;
      send({
        tag: 'device-is-live',
        device: device.toString(),
        buttons: '0x' + buttons.toString(16),
        note: 'This pad device has reported a held button at least once, so the poll is reaching it.',
      });
    }
    if ((buttons & DPAD_RIGHT) !== 0) counts.dpadRightFrames++;
    // Refresh the idle baseline only while nothing is held, so the comparison is always against a
    // genuinely quiet frame rather than against the tail of the previous press.
    if (buttons === 0) {
      const quiet = snapshotMap();
      if (quiet !== null) idleSnapshot = quiet;
    }
    const rose = buttons & ~state.previous;
    state.previous = buttons;
    if (rose === 0) return;
    if ((rose & DPAD_UP) !== 0) counts.dpadUpEdges++;
    if ((rose & DPAD_DOWN) !== 0) counts.dpadDownEdges++;
    if ((rose & DPAD_LEFT) !== 0) counts.dpadLeftEdges++;
    if ((rose & DPAD_RIGHT) === 0) return;
    counts.dpadRightEdges++;
    // The armament state at the instant of the press, and again shortly after. A cycle that is
    // going to happen has not happened yet on the rising edge -- the press is still travelling --
    // so a single sample here would report "unchanged" for a switch that works perfectly.
    // `pendingCompare` is picked up by a later poll and reports the delta.
    const before = armamentState();
    pendingCompare = { at: counts.polls, before: before, n: counts.dpadRightEdges };
    send({
      tag: 'dpad-right',
      n: counts.dpadRightEdges,
      device: device.toString(),
      buttons: '0x' + buttons.toString(16),
      held: names(buttons),
      stickX: stickX,
      eventDelta: diffAgainstIdle(snapshotMap()),
      armamentBefore: before,
      note: 'D-pad Right rose. `armamentBefore` is what the engine says is in each right-hand slot at that instant, read through its own GetParamIdInSlot. The follow-up `armament-after` line says whether any of it moved.',
    });
  },
});

// A hardware watchpoint on `right_weapon_slot` itself, which is the only instrument that answers
// "what writes this" rather than "what does it hold now".
//
// Four bytes, one address, no protection change -- deliberately NOT `MemoryAccessMonitor`, which
// revokes a whole 4 KB page and turns every access by every thread into a fault. On this target
// that killed the game once already: a guard page on Seamless's session produced `0xc0000005` at
// `ersc+0x89e23` while the player used an item.
//
// What each outcome means, and they are opposites:
//
//   a write arrives on a D-pad Right press  -> the local cycle path runs and something later
//                                              overwrites or ignores it
//   no write at all while the press repeats -> the press never reaches the cycle, and the address
//                                              of the last writer is irrelevant because there is none
const WATCH_SLOT = 0;
let watching = null;

// Whether the watchpoint attempt is worth repeating.
//
// It is retried while it fails for a reason that can change -- no character yet, a thread list
// caught mid-load. It is NOT retried once the API itself is missing, which cannot change inside a
// session: the first version retried unconditionally and emitted the same failure line on every
// poll, 225 of them, which buries the run's real output under a defect in the instrument.
let watchAttemptsLeft = 240;
let watchApiReported = false;

function watchRightWeaponSlot () {
  if (watching !== null) return watching;
  if (watchAttemptsLeft <= 0) return null;
  watchAttemptsLeft--;
  // Say what this build of Frida actually offers, once, instead of guessing spellings. `Thread`
  // is the static namespace this repo's notes name; a thread object from `enumerateThreads` is
  // where Frida 16.2+ actually puts the per-thread hardware breakpoint and watchpoint methods.
  if (!watchApiReported) {
    watchApiReported = true;
    const sample = Process.enumerateThreads()[0];
    send({
      tag: 'watchpoint-api',
      frida: Frida.version,
      threadStatics: Object.getOwnPropertyNames(Thread),
      threadObject: sample === undefined
        ? 'no threads to sample'
        : Object.getOwnPropertyNames(sample).concat(
          Object.getOwnPropertyNames(Object.getPrototypeOf(sample) || {}),
        ),
      note: 'What this Frida exposes for hardware watchpoints. Whichever name appears here is the one to call; anything else is a guess.',
    });
  }
  let equip;
  try {
    const manager = base.add(GAME_DATA_MAN_RVA).readPointer();
    if (manager.isNull()) return null;
    const pgd = manager.add(PLAYER_GAME_DATA_OFFSET).readPointer();
    if (pgd.isNull()) return null;
    equip = pgd.add(EQUIP_GAME_DATA_OFFSET);
  } catch (error) {
    return null;
  }
  const address = equip.add(RIGHT_WEAPON_SLOT_OFFSET);
  // The game thread is the one that would cycle it, and a watchpoint is per-thread. Arming every
  // thread would spend the four available slots on threads that never touch equipment; the poll's
  // own thread and the main thread are where a player-driven write can come from.
  const armed = [];
  // Why the failures are collected rather than swallowed: the first version of this counted only
  // successes and reported `threads: 0`, which reads as "nothing writes it" when it actually means
  // "nothing is watching". A silent instrument that reports an absence is worse than no instrument,
  // because the absence looks like a finding.
  const refused = [];
  const threads = Process.enumerateThreads();
  for (const thread of threads) {
    try {
      // A method on the thread object, NOT the static `Thread.setHardwareWatchpoint` that this
      // repo's notes name -- that spelling is `TypeError: not a function` on Frida 17.17.0, and
      // because the first version counted only successes it reported zero armed threads as though
      // that were a measurement. The slot id is the first argument: four per thread, 0..3.
      thread.setHardwareWatchpoint(WATCH_SLOT, address, 4, 'w');
      armed.push(thread.id);
    } catch (error) {
      if (refused.length < 3) refused.push(String(error));
    }
  }
  watching = armed.length === 0 ? null : { address: address.toString(), threads: armed };
  // A missing method is not a condition that improves by trying again next frame.
  if (armed.length === 0 && refused.some(function (why) { return why.indexOf('not a function') !== -1; })) {
    watchAttemptsLeft = 0;
  }
  if (armed.length === 0 && watchAttemptsLeft > 0) {
    // Quiet retry: no line until it either works or gives up, so a transient failure during a load
    // screen does not fill the log with itself.
    return null;
  }
  send({
    tag: 'watchpoint',
    address: address.toString(),
    threadsSeen: threads.length,
    threadsArmed: armed.length,
    refusedBecause: refused,
    usable: armed.length > 0,
    note: armed.length > 0
      ? 'Write watchpoint on ChrAsm.right_weapon_slot. A press that produces no hit never reached the cycle.'
      : 'NOT watching -- every thread refused the watchpoint, so silence here proves nothing. The reasons are above.',
  });
  // Leave `watching` null on total failure so the next poll tries again: threads come and go, and
  // a watchpoint that could not be placed during a load screen may place fine a second later.
  return watching;
}

// Give every armed thread its debug register back.
//
// This is not tidiness, it is the difference between a probe and a crash. A hardware watchpoint
// lives in the thread's debug registers, not in the agent, so it outlives the agent: once this
// script is gone there is no exception handler left, and the next write to the watched address
// raises a debug exception nobody services. The game dies, with nothing in the crash log, because
// an unhandled hardware-debug exception is not a fault the crash handler can catch.
//
// Measured 2026-09-19, on this repo's own game: the watcher holding 116 armed threads was killed
// by its outer `timeout` at 18:38:50 and the game's last log write is 18:38:49 -- the same second,
// mid-sweep, with the heartbeat healthy at tick 67200 one line earlier. `bd
// never-vtable-swap-from-frida-a-killed-watcher-freezes-the-game-2026-09-18` is the same lesson
// through a different instrument.
function releaseWatchpoints () {
  if (watching === null) return;
  const held = watching;
  watching = null;
  watchAttemptsLeft = 0;
  for (const id of held.threads) {
    for (const thread of Process.enumerateThreads()) {
      if (thread.id !== id) continue;
      try {
        thread.unsetHardwareWatchpoint(WATCH_SLOT);
      } catch (error) {
        // A thread that has exited cannot hold a watchpoint, so there is nothing to give back.
      }
    }
  }
}

// Frida calls this when the script is unloaded, which covers a clean detach and the watcher's own
// `SIGTERM` path. It does NOT cover a `SIGKILL`, which is why the runner must never hard-kill a
// watcher that owns debug state -- there is no in-process fix for being killed outright.
Process.setExceptionHandler(function (details) {
  if (details.type !== 'breakpoint' && details.type !== 'single-step' && details.type !== 'access-violation') {
    return false;
  }
  if (watching === null) return false;
  send({
    tag: 'slot-written',
    by: details.address.toString(),
    symbol: DebugSymbol.fromAddress(details.address).toString(),
    type: details.type,
    note: 'Something wrote ChrAsm.right_weapon_slot. This address is the writer.',
  });
  // Resume: the write is the game's own and must complete. Returning true tells Frida the
  // exception is handled and execution continues from where it stopped.
  return true;
});

send({
  tag: 'armed',
  poll: poll.toString(),
  rva: '0x' + POLL_RVA.toString(16),
  note: 'DLUID::PadDevice::Poll, 1.17.0 dump 0x141f6d8d0 carried +0x70 to the installed 1.17.1. Read-only: two fields off the object the poll was already called with.',
});

// Pulled by the driver rather than pushed on a clock. A press is an edge and the edges are sent as
// they happen; this is for the question the edges cannot answer on their own -- whether the poll is
// running at all while nothing is being pressed.
rpc.exports = {
  totals: function () {
    const perDevice = [];
    for (const [device, state] of devices) {
      perDevice.push({ device: device, polls: state.polls, everNonZero: state.everNonZero });
    }
    return { counts: counts, devices: perDevice };
  },
  // Frida calls `dispose` when the script is unloaded, which covers a clean detach, a reload in
  // place, and the watcher's own `SIGTERM` path. Giving the debug registers back there is what
  // stops this agent from outliving itself as an unhandled exception in the player's game.
  //
  // It cannot cover `SIGKILL`: nothing in-process can. That half of the fix belongs to whoever
  // starts the watcher -- do not wrap one that owns debug state in a hard kill.
  dispose: function () {
    releaseWatchpoints();
  },
};
