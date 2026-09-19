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

const base = Process.getModuleByName('eldenring.exe').base;

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
    const state = deviceState(device);
    state.polls++;
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
    const rose = buttons & ~state.previous;
    state.previous = buttons;
    if (rose === 0) return;
    if ((rose & DPAD_UP) !== 0) counts.dpadUpEdges++;
    if ((rose & DPAD_DOWN) !== 0) counts.dpadDownEdges++;
    if ((rose & DPAD_LEFT) !== 0) counts.dpadLeftEdges++;
    if ((rose & DPAD_RIGHT) === 0) return;
    counts.dpadRightEdges++;
    const menu = liveMenuEvents();
    send({
      tag: 'dpad-right',
      n: counts.dpadRightEdges,
      device: device.toString(),
      buttons: '0x' + buttons.toString(16),
      held: names(buttons),
      stickX: stickX,
      menuEvents: menu,
      note: 'The driver reported D-pad Right rising on this device. Anything that swallows the press from here on is inside the game, not in front of it. `menuEvents` is what CSMenuMan holds at that instant: entries here while the player is in the world mean the press is being read as menu input.',
    });
  },
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
};
