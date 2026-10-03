// Which menu windows are open right now, named by their RTTI class.
//
// `MenuWindowJob::Run` is pumped once per frame for every live menu window job, and `job+0x130` is
// the window it owns. Collecting the owners seen over one frame and naming each by the RTTI beside
// its vtable gives the set of open windows without guessing at any bitmap index. A frame ends when a
// job already seen in it is pumped again, so the boundary comes from the pump itself. The set is
// sent whenever it changes, so a menu opening or closing is one message. When the last window
// closes nothing is pumped any more, so that final change is not reported.
//
// The rva is the 1.17 one: 1.16.2 `0x7ad1c0` maps to `0x7ae040` (`IDENTICAL-WHOLE` in
// `docs/recon/rva-map-1162-to-1170.needed-verified.tsv`), and it sits below the `0xafefe9` boundary,
// so the installed 1.17.1 has it at the same address.
//
// Read-only: one Interceptor, reads only.
'use strict';

const MENU_WINDOW_JOB_RUN_RVA = 0x7ae040;
const MENU_WINDOW_JOB_WINDOW_OFFSET = 0x130;

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;

// About 28 percent of function entries on this build open with an Arxan healing stub, and a hook on
// the stub never fires.
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
  } catch (e) {
    name = null;
  }
  if (name === null) name = 'vtable+0x' + vtable.sub(lo).toString(16);
  names.set(key, name);
  return name;
}

let seen = new Map();
let jobsThisFrame = new Set();
let last = '';
let calls = 0;

// Runs when a job repeats, so `seen` holds exactly one frame of pumps.
function endFrame () {
  const open = [...seen.keys()].sort();
  const key = open.join('|');
  if (key !== last) {
    last = key;
    send({ tag: 'open-windows', open, pumpsLastFrame: calls });
  }
  seen = new Map();
  jobsThisFrame = new Set();
  calls = 0;
}

const target = follow(lo.add(MENU_WINDOW_JOB_RUN_RVA));
Interceptor.attach(target, {
  onEnter (args) {
    const job = args[0].toString();
    if (jobsThisFrame.has(job)) endFrame();
    jobsThisFrame.add(job);
    calls += 1;
    try {
      const window = args[0].add(MENU_WINDOW_JOB_WINDOW_OFFSET).readPointer();
      if (window.isNull()) return;
      const vt = window.readPointer();
      if (!inImage(vt)) return;
      const name = rttiName(vt);
      seen.set(name, (seen.get(name) || 0) + 1);
    } catch (e) {}
  },
});

send({ tag: 'armed', at: target.toString() });
