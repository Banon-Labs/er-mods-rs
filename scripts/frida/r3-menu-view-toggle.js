// Which field of the open item-select menu does an R3 press change?
//
// The player reports R3 cycling the item list between three views. This takes a snapshot of every
// open menu window's first `SNAPSHOT_BYTES` when R3 rises at the pad and another `SETTLE_POLLS`
// polls later, and reports each dword that changed. A field that steps through three values across
// three presses is the view mode; cursor and animation noise changes on presses that are not R3 too.
//
// Hooks, both read-only:
//   `DLUID::PadDevice::Poll` (1.17.1 rva `0x1f6d940`, see `dpad-right-where-it-dies.js`) stores
//   `XINPUT_GAMEPAD.wButtons` at `device+0x890`; R3 is `XINPUT_GAMEPAD_RIGHT_THUMB` `0x0080`.
//   `MenuWindowJob::Run` (1.17 rva `0x7ae040`, see `open-menu-windows.js`), whose `job+0x130` is the
//   window, gives the live window objects.
'use strict';

const POLL_RVA = 0x1f6d940;
const DEVICE_BUTTONS_OFFSET = 0x890;
const RIGHT_THUMB = 0x0080;
const MENU_WINDOW_JOB_RUN_RVA = 0x7ae040;
const MENU_WINDOW_JOB_WINDOW_OFFSET = 0x130;
const SNAPSHOT_BYTES = 0x1000;
const SETTLE_POLLS = 20;
const WATCHED = new Set(['.?AVGaitemSelectDialog@CS@@', '.?AVEquipDialog@CS@@', '.?AVGaitemSelectDialogBase@CS@@']);

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;

function follow (address) {
  return address.readU8() === 0xe9 ? address.add(5).add(address.add(1).readS32()) : address;
}

const names = new Map();
function rttiName (vtable) {
  const key = vtable.toString();
  if (names.has(key)) return names.get(key);
  let name = null;
  try {
    const col = vtable.sub(8).readPointer();
    if (col.readU32() === 1) {
      const imageBase = col.sub(col.add(0x14).readU32());
      name = imageBase.add(col.add(0x0c).readU32()).add(0x10).readCString();
    }
  } catch (e) {}
  if (name === null) name = 'vtable+0x' + vtable.sub(lo).toString(16);
  names.set(key, name);
  return name;
}

// window pointer -> class name for the last complete frame. A frame ends when a job already seen in
// it is pumped again; the windows collected in it then replace the previous set, so a window that
// closed and stopped being pumped drops out and a snapshot never reads a freed object.
let windows = new Map();
let building = new Map();
let jobsThisFrame = new Set();
Interceptor.attach(follow(lo.add(MENU_WINDOW_JOB_RUN_RVA)), {
  onEnter (args) {
    const job = args[0].toString();
    if (jobsThisFrame.has(job)) {
      windows = building;
      building = new Map();
      jobsThisFrame = new Set();
    }
    jobsThisFrame.add(job);
    try {
      const window = args[0].add(MENU_WINDOW_JOB_WINDOW_OFFSET).readPointer();
      if (window.isNull()) return;
      const vt = window.readPointer();
      if (!inImage(vt)) return;
      const name = rttiName(vt);
      if (WATCHED.has(name)) building.set(window.toString(), name);
    } catch (e) {}
  },
});

// The windows themselves did not change on 19 presses, so the view mode lives in an object they
// point to, or in a global the menu reads (the mode survives closing the menu). Snapshot one level
// of heap children of each window, plus `GameDataMan` and its children.
const GAME_DATA_MAN_RVA = 0x3d61f98;
const CHILD_BYTES = 0x800;

function addRegion (out, p, name, size) {
  if (out.has(p)) return;
  try { out.set(p, { name, bytes: new Uint32Array(ptr(p).readByteArray(size)) }); } catch (e) {}
}

function addChildren (out, root, name, size) {
  let words;
  try { words = new BigUint64Array(ptr(root).readByteArray(size)); } catch (e) { return; }
  for (let i = 0; i < words.length; i++) {
    const v = words[i];
    if (v < 0x10000n || v > 0x7fffffffffffn || (v & 7n) !== 0n) continue;
    const child = ptr('0x' + v.toString(16));
    if (!inImage(child)) addRegion(out, child.toString(), name + '+0x' + (i * 8).toString(16), CHILD_BYTES);
  }
}

function snapshot () {
  const out = new Map();
  for (const [p, name] of windows) {
    addRegion(out, p, name, SNAPSHOT_BYTES);
    addChildren(out, p, name, SNAPSHOT_BYTES);
  }
  try {
    const gdm = lo.add(GAME_DATA_MAN_RVA).readPointer();
    if (!gdm.isNull()) {
      addRegion(out, gdm.toString(), 'GameDataMan', 0x200);
      addChildren(out, gdm.toString(), 'GameDataMan', 0x200);
    }
  } catch (e) {}
  return out;
}

// Field -> one entry per press that changed it. A view mode changes on every press, holds between
// presses (each press starts from where the last one ended), and repeats every third press.
const history = new Map();
const CYCLE_WINDOW = 4;

function cycling (press) {
  const out = [];
  for (const [key, h] of history) {
    if (h.length < CYCLE_WINDOW) continue;
    const tail = h.slice(-CYCLE_WINDOW);
    if (tail[tail.length - 1].press !== press) continue;
    let ok = true;
    for (let n = 1; n < tail.length && ok; n++) {
      if (tail[n].press !== tail[n - 1].press + 1 || tail[n].from !== tail[n - 1].to) ok = false;
      if (n >= 3 && tail[n].to !== tail[n - 3].to) ok = false;
    }
    if (ok && new Set(tail.map(e => e.to)).size === 3) out.push({ key, values: h.slice(-6).map(e => e.to) });
    if (out.length >= 40) break;
  }
  return out;
}

// The fields found cycling 0, 1, 2 on presses 24-33, and the objects that hold them.
const FOUND = [[0xcc0, 0x7c0], [0xf98, 0x7f0], [0x2e0, 0x7f8]];
function owners () {
  const out = [];
  for (const [p, name] of windows) {
    if (name !== '.?AVGaitemSelectDialog@CS@@') continue;
    for (const [child, off] of FOUND) {
      try {
        const obj = ptr(p).add(child).readPointer();
        const vt = obj.readPointer();
        out.push({
          child: '0x' + child.toString(16),
          obj: obj.toString(),
          cls: inImage(vt) ? rttiName(vt) : 'no vtable',
          value: obj.add(off).readU32(),
        });
      } catch (e) { out.push({ child: '0x' + child.toString(16), err: String(e) }); }
    }
  }
  return out;
}

let presses = 0;
let polls = 0;
let lastButtons = new Map();
let pending = null;

Interceptor.attach(lo.add(POLL_RVA), {
  onEnter (args) { this.device = args[0]; },
  onLeave () {
    polls += 1;
    let buttons;
    try { buttons = this.device.add(DEVICE_BUTTONS_OFFSET).readU16(); } catch (e) { return; }
    const key = this.device.toString();
    const was = lastButtons.get(key) || 0;
    lastButtons.set(key, buttons);
    if (pending !== null && polls - pending.at >= SETTLE_POLLS) {
      const after = snapshot();
      for (const [p, before] of pending.before) {
        const now = after.get(p);
        if (!now) continue;
        for (let i = 0; i < before.bytes.length; i++) {
          if (before.bytes[i] === now.bytes[i] || before.bytes[i] > 0xff || now.bytes[i] > 0xff) continue;
          const key = before.name + ' @0x' + (i * 4).toString(16);
          let h = history.get(key);
          if (!h) { h = []; history.set(key, h); }
          h.push({ press: pending.press, from: before.bytes[i], to: now.bytes[i] });
        }
      }
      send({ tag: 'r3-cycle', press: pending.press, regions: pending.before.size, candidates: cycling(pending.press), owners: owners() });
      pending = null;
    }
    if ((buttons & RIGHT_THUMB) && !(was & RIGHT_THUMB) && pending === null) {
      presses += 1;
      pending = { press: presses, at: polls, before: snapshot() };
      send({ tag: 'r3-press', press: presses, windows: windows.size });
    }
  },
});

send({ tag: 'armed' });
