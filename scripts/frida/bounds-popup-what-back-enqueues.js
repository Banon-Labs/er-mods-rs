// What the player's `Back` press actually enqueues, on the object the bounds popup queries.
//
// # The one value this run is for
//
// The invasion-bounds popup ("Attempting to invade another world.") cannot be closed by the
// player. Everything either side of the press is measured; the press itself is not.
//
// The close is `movl $0x5, 0x10(%rcx)` at `0x1407c2c64`, guarded by `test dl,dl`, and that `dl`
// is now read correctly out of the 1.17 image rather than guessed:
//
// ```text
//   FUN_1407c2ae0(ctrl, param_2)             ; the ctrl tick, dispatching on ctrl+0x10
//     step 2 -> dl = FUN_1407c3210(ctrl, param_2)
//                  = param_2
//                  | shown(kind 0x02) | shown(kind 0x1c) | (shown(0x1f) && shown(0x20))
//            -> FUN_1407c2c50(ctrl, dl, menuData)      ; switch arm 0x15 / 0x16
// ```
//
// The three `shown(...)` terms read `CSMenuManImp+0x90`, which is the shown-menu-window flag
// array -- `byte[70]`, indexed by a window index, tested `& 3`. They mean "another menu came up,
// abandon this dialog", and they are vanilla's real escape hatch. The press is `param_2`, and
// `param_2` is `A || B` from `FUN_140660f70`:
//
// ```text
//   A = FUN_1403f4a90(owner) = *(u8 *)(owner + 0x1c5) >> 1 & 1
//   B = FUN_1403f4e10(owner) = FUN_1404fa370(*(owner + 0x178), 0xf)
// ```
//
// So the press has to arrive as either a flag bit on the owner or a pending menu event with id
// `0xf`. Both have measured zero across a dozen presses. What no run has done is look at what the
// press DOES enqueue, and that is the whole of this agent.
//
// # Why the queue can be walked rather than sampled
//
// `FUN_1404fa370(source, id)` is decoded, not assumed:
//
// ```text
//   if (*(u64 *)(source + 0x28) & (0xf << (4 * ((id % 15) + (id != 0))))) {
//     for (node = *(source + 0x8); node; node = *(node + 0x30))
//       if ((*(u32 *)(node + 0x60) & 0x800c0003) == 0)
//         if (*(u16 *)(*node + 0x156) == id) return true;
//   }
//   return false;
// ```
//
// `+0x28` is a coarse sixteen-nibble index over pending ids and `+0x8` is the list that carries
// the real ones. So every id pending on a source can be enumerated, not just probed one at a
// time -- which is what turns "0xf never fires" into "here is what fires instead".
//
// # Why predicate B is the hook and not the query
//
// `0x1404fa370` is asked for many ids on many widgets every frame; its `rcx` is a different
// object each call and caching one gets a widget nobody is looking at. `0x1403f4e10` is the only
// caller whose answer reaches this dialog's `dl`, so its `rcx` is the owner and nothing else is.
//
// # Cost
//
// Predicate B runs for several menu owners every frame. `onEnter` here does one pointer store per
// call and nothing else; the queue walk happens only while the bounds popup's own step-2 handler
// is running, and every line is emitted on change. A per-frame `send` would be tens of thousands
// of identical messages with the press buried in them.
//
// Installed build 1.17.1. Every rva below is under `0xafefe9`, so the 1.17.0 deobf image, the
// Ghidra dump on :8767 and the live process all agree with no translation.
const RVA = {
  // The step-2 handler: called once a frame while the bounds popup is on screen. The gate for
  // every walk below, so nothing is read while the dialog is not up.
  startInvasion: 0x7c2c50,
  // Predicate B. Its `rcx` is the owner whose `+0x178` this dialog's `dl` is computed from.
  backPredicate: 0x3f4e10,
  // Predicate A, hooked for its return value alone: if the flag bit ever rises on a press, the
  // product fix is a flag read and not a queue walk.
  flagPredicate: 0x3f4a90,
};

// The id the popup's back-out is gated on. Named rather than filtered: an id that appears on the
// press while `0xf` stays absent is the finding.
const BACK_EVENT_ID = 0xf;

// The owner fields the two predicates read.
const OWNER_EVENT_SOURCE = 0x178;
const OWNER_FLAG_BYTE = 0x1c5;
// `owner + 0x6a0` is the `CSPlayerMenuCtrl` an owner belongs to, which is how the owner driving
// this dialog is told apart from the four others polled every frame.
const OWNER_CTRL = 0x6a0;

// The event source's own shape, from the decode above.
const SOURCE_NIBBLES = 0x28;
const SOURCE_LIST_HEAD = 0x8;
const NODE_NEXT = 0x30;
const NODE_FLAGS = 0x60;
const NODE_FLAGS_MASK = 0x800c0003;
const EVENT_ID_OFFSET = 0x156;
// A list that loops or is being torn down mid-walk must end the walk, not the game.
const MAX_NODES = 256;

const base = Process.getModuleByName('eldenring.exe').base;

// Arxan leaves about 28 percent of entries on this build as a rel32 stub with the real bytes
// intact behind it. A hook on the stub is overwritten rather than installed, and reports zero
// hits -- which reads as "the function never ran".
function follow (address) {
  try {
    return address.readU8() === 0xe9
      ? address.add(5).add(address.add(1).readS32())
      : address;
  } catch (e) {
    return address;
  }
}

// Fault-closed throughout: a pointer chain through a menu object being torn down faults, and an
// oracle that takes the game with it destroys the evidence it was installed for.
function readPtr (address) {
  try {
    const value = address.readPointer();
    return value.isNull() ? null : value;
  } catch (e) {
    return null;
  }
}

function readU8 (address) {
  try {
    return address.readU8();
  } catch (e) {
    return null;
  }
}

function readU16 (address) {
  try {
    return address.readU16();
  } catch (e) {
    return null;
  }
}

function readU32 (address) {
  try {
    return address.readU32();
  } catch (e) {
    return null;
  }
}

function readU64 (address) {
  try {
    return address.readU64().toString(16);
  } catch (e) {
    return null;
  }
}

// Every event id pending on one source, with the enabled/disabled verdict the query itself uses.
function pendingEventIds (source) {
  const head = readPtr(source.add(SOURCE_LIST_HEAD));
  if (head === null) return [];
  const ids = [];
  let node = head;
  for (let seen = 0; node !== null && seen < MAX_NODES; seen++) {
    const flags = readU32(node.add(NODE_FLAGS));
    const child = readPtr(node);
    if (child !== null && flags !== null) {
      const id = readU16(child.add(EVENT_ID_OFFSET));
      if (id !== null) {
        // The query only answers for a node whose flags are clear, so a masked-out id is
        // reported with its reason rather than dropped: "the press enqueued 0xf and the query
        // refused it for its flags" is a different bug from "the press enqueued something else".
        const blocked = (flags & NODE_FLAGS_MASK) !== 0;
        ids.push('0x' + id.toString(16) + (blocked ? '(blocked:0x' + (flags & NODE_FLAGS_MASK).toString(16) + ')' : ''));
      }
    }
    node = readPtr(node.add(NODE_NEXT));
  }
  return ids;
}

// Owners seen by predicate B, newest value per pointer. Storing the pointer is all `onEnter`
// does; everything expensive happens on the popup's own frame.
const owners = new Map();
const info = {};
const hits = { startInvasion: 0, backPredicate: 0, flagPredicate: 0 };
let flagEverSet = false;
let backIdEverPending = false;
let last = null;

function arm (name, onEnter, onLeave) {
  const entry = base.add(RVA[name]);
  const body = follow(entry);
  info[name] = {
    entry: entry.toString(),
    body: body.toString(),
    stubbed: !body.equals(entry),
  };
  Interceptor.attach(body, onLeave === undefined ? { onEnter } : { onEnter, onLeave });
}

arm('backPredicate', function (args) {
  hits.backPredicate++;
  owners.set(args[0].toString(), args[0]);
});

arm('flagPredicate', function (args) {
  hits.flagPredicate++;
  owners.set(args[0].toString(), args[0]);
}, function (retval) {
  if (!retval.isNull() && !flagEverSet) {
    flagEverSet = true;
    send({ tag: 'flag-predicate-answered-yes', note: 'owner+0x1c5 bit 1 rose -- predicate A is live and the product fix is a flag read' });
  }
});

// The ctrl the bounds popup is sitting in, learned from its own handler. The close needs it, and
// it must come from the handler rather than from a scan: `CSPlayerMenuCtrl` is shared by the whole
// player menu and writing a step into the wrong one moves a dialog nobody is looking at.
let boundsCtrl = null;
// Set by the input census when the player presses something and consumed a few lines later in the
// same call, because the census now runs inside the popup's own handler. The write therefore
// happens on the game's menu thread, which is the thread that owns this object.
let pendingClose = null;
let closes = 0;

arm('startInvasion', function (args) {
  hits.startInvasion++;
  const ctrl = args[0];
  const dl = args[1].toInt32() & 0xff;
  boundsCtrl = ctrl;
  // The input census, on the thread that owns this ctrl. It sets `pendingClose` when the player
  // presses something, and the consumption directly below picks it up in the same call -- so the
  // close is written on the menu thread by construction rather than by a handoff from a timer.
  census();
  if (pendingClose !== null) {
    const asked = pendingClose;
    pendingClose = null;
    try {
      // `ctrl + 0x10` is the step, and 5 is the identical value the game's own back-out arm
      // writes at `0x1407c2c64`. Step 5 is what the tick dispatches to the popup close, so this
      // is the game's teardown reached by the game's own constant -- not a window torn down
      // behind the menu system's back.
      ctrl.add(0x10).writeU32(5);
      closes++;
      send({
        tag: 'closed-on-player-press',
        ctrl: ctrl.toString(),
        keys: asked.keys,
        pad: asked.pad,
        closes,
        note: 'step 5 written to CSPlayerMenuCtrl+0x10 on the press listed here. This is the product rule being prototyped: vanilla cannot close this dialog because predicate B looks for menu entry id 0xf and no owner in the process carries one.',
      });
    } catch (e) {
      send({ tag: 'close-failed', error: e.message });
    }
  }
  const lines = [];
  for (const [key, owner] of owners) {
    const ctrlOfOwner = readPtr(owner.add(OWNER_CTRL));
    const mine = ctrlOfOwner !== null && ctrlOfOwner.equals(ctrl);
    const flag = readU8(owner.add(OWNER_FLAG_BYTE));
    const source = readPtr(owner.add(OWNER_EVENT_SOURCE));
    const ids = source === null ? [] : pendingEventIds(source);
    if (ids.some(function (id) { return id === '0x' + BACK_EVENT_ID.toString(16); })) {
      backIdEverPending = true;
    }
    // An owner with nothing pending and a resting flag says nothing, every frame, for every one
    // of the five-plus owners polled. Only the ones carrying something go in the line.
    if (ids.length === 0 && (flag === null || flag === 0) && !mine) continue;
    lines.push({
      owner: key,
      mine,
      flag: flag === null ? null : '0x' + flag.toString(16),
      nibbles: source === null ? null : readU64(source.add(SOURCE_NIBBLES)),
      ids,
    });
  }
  const now = JSON.stringify({ dl, lines });
  if (now === last) return;
  last = now;
  send({
    tag: 'bounds-popup-frame',
    dl,
    ctrl: ctrl.toString(),
    backIdEverPending,
    flagEverSet,
    owners: lines,
    note: 'dl is what closes the popup; it is `param_2 | three shown-window terms`. Every id listed is pending on an owner the dialog polls. Event id 0xf is the one the back-out asks for.',
  });
});

// ---------------------------------------------------------------------------------------------
// What the player is actually pressing, on the two stages that are not the menu system's.
//
// The first live reading with this agent settled the menu side and left exactly one gap. With the
// popup on screen for 1,993 sampled frames, the owner whose `+0x178` and `+0x1c5` decide `dl` --
// `0x32e8e080`, matched by `+0x6a0` to the ctrl -- never changed once: flag `0x8` (so predicate A
// bit 1 is clear), bucket counts `0x10002004000008` fixed, entry ids fixed at
// `0x131 x4, 0x143, 0x116, 0x93` and eight zeroes, with no `0xf` anywhere. Nothing the player does
// reaches it. So the press has to be read where the press really is.
//
// `GetAsyncKeyState` is the same call `local_invasion_filter::hotkeys` already uses in the product
// to read this player's mark and toggle keys, so naming the key here names something the DLL can
// read the same way tomorrow. Only bit 15 (down right now) is read: the low "pressed since the
// last call" bit is consumed by whoever reads it, and eating the game's own edge would turn this
// oracle into an input bug.
//
// The pad goes through `XInputGetState` on the module the game has already loaded -- resolved,
// never `LoadLibrary`'d, so this adds no module to the process.
const VK_MIN = 0x01;
const VK_MAX = 0xff;
const VK_DOWN_MASK = 0x8000;
// The census rides the popup's own per-frame handler and samples every third call of it. A
// 255-call sweep on every frame is thousands of native calls a second through the bridge, and a
// human press is tens of milliseconds wide, so every third frame catches it without that cost --
// roughly the 20 Hz a wall-clock timer used to give, except it is the popup's frames that drive it.
// A timer would be a poll: it ticks while no popup is on screen, it ticks on a thread that does
// not own the ctrl the close has to be written through, and it keeps ticking after the popup is
// gone. Counting the handler's own calls has none of those and needs no clock.
const CENSUS_EVERY_NTH_FRAME = 3;

function resolve (moduleNames, symbol) {
  for (const name of moduleNames) {
    let found = null;
    try {
      found = Process.findModuleByName(name);
    } catch (e) {
      found = null;
    }
    if (found === null) continue;
    const address = found.findExportByName(symbol);
    if (address !== null) return { module: name, address };
  }
  return null;
}

const getAsyncKeyState = (function () {
  const found = resolve(['user32.dll', 'USER32.dll'], 'GetAsyncKeyState');
  return found === null ? null : new NativeFunction(found.address, 'int16', ['int']);
})();

// The game loads exactly one of these; which one is itself worth reporting, because a pad read
// against a module the game is not using is a silent zero rather than an error.
const xInputGetState = (function () {
  const found = resolve(
    ['xinput1_4.dll', 'XINPUT1_4.dll', 'xinput1_3.dll', 'xinput9_1_0.dll'],
    'XInputGetState',
  );
  return found === null
    ? null
    : { name: found.module, call: new NativeFunction(found.address, 'uint32', ['uint32', 'pointer']) };
})();

// `XINPUT_STATE` is `DWORD dwPacketNumber` then `XINPUT_GAMEPAD`, whose first field is
// `WORD wButtons` -- so the buttons are at `+4`.
const XINPUT_STATE_SIZE = 16;
const XINPUT_BUTTONS_OFFSET = 4;
const padState = xInputGetState === null ? null : Memory.alloc(XINPUT_STATE_SIZE);

let lastKeys = null;
let lastButtons = null;

function keysDown () {
  if (getAsyncKeyState === null) return null;
  const down = [];
  for (let vk = VK_MIN; vk <= VK_MAX; vk++) {
    if ((getAsyncKeyState(vk) & VK_DOWN_MASK) !== 0) down.push('0x' + vk.toString(16));
  }
  return down;
}

function padButtons () {
  if (xInputGetState === null || padState === null) return null;
  // Slot 0 only. A second pad would answer on another slot, and a run where the player's pad is
  // not slot 0 should say "no buttons" rather than quietly read somebody else's.
  if (xInputGetState.call(0, padState) !== 0) return null;
  return padState.add(XINPUT_BUTTONS_OFFSET).readU16();
}

// Called from the popup's own handler, so it runs only while the dialog is up. The census is for
// one question and a log full of the player walking around answers a different one.
function census () {
  if (hits.startInvasion % CENSUS_EVERY_NTH_FRAME !== 0) return;
  const keys = keysDown();
  const buttons = padButtons();
  const keyLine = keys === null ? 'unavailable' : keys.join(',') || 'none';
  const padLine = buttons === null ? 'unavailable' : '0x' + buttons.toString(16);
  if (keyLine === lastKeys && padLine === lastButtons) return;
  const wasIdle = (lastKeys === 'none' || lastKeys === null) && (lastButtons === '0x0' || lastButtons === null);
  lastKeys = keyLine;
  lastButtons = padLine;
  // An edge from nothing-held to something-held, while an inescapable modal is on screen, is the
  // player trying to dismiss it. Which button it turns out to be is the value this run exists to
  // name -- the shipped rule gets the measured one, not "anything".
  if (wasIdle && (keyLine !== 'none' || padLine !== '0x0') && boundsCtrl !== null) {
    pendingClose = { keys: keyLine, pad: padLine };
  }
  send({
    tag: 'player-input',
    keys: keyLine,
    pad: padLine,
    padModule: xInputGetState === null ? null : xInputGetState.name,
    note: 'Virtual keys held (GetAsyncKeyState bit 15) and the slot-0 pad button mask, sampled on every third frame of the bounds popup handler. XINPUT_GAMEPAD_B is 0x2000.',
  });
}

send({
  tag: 'armed',
  info,
  keyboard: getAsyncKeyState === null ? 'GetAsyncKeyState unavailable' : 'GetAsyncKeyState ready',
  pad: xInputGetState === null ? 'no XInput module loaded' : xInputGetState.name,
  note: 'Every line is emitted on change only.',
});

// Pulled by the driver rather than pushed on a timer. A heartbeat exists so a stall reads as a
// number that stopped rather than as silence, and the driver is what decides when it wants to know
// -- a clock inside the agent only guesses at that, and keeps guessing after nobody is listening.
rpc.exports = {
  totals: function () {
    return { hits, backIdEverPending, flagEverSet, owners: owners.size, closes };
  },
};
