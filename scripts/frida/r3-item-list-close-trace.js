// Which `GaitemSelectDialog` methods run when B closes the item list, and how long `Run` keeps
// pumping the window after that.
//
// er-r3-view hides its board once the item list's `MenuWindowJob::Run` (1.17.1 rva 0x7ae040) has
// gone quiet, and the player sees it go later than the game's own panels. This hooks every
// virtual method of the item list's window and reports calls on that window that are rare (the
// first `RARE` calls of each slot), each with the time since the last `Run` and the `Run` count,
// so the method that starts the close and the length of the close animation both show up. When
// `Run` stops, the next `Run` of any other window sends the item list's last pump time.
'use strict';

const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const TARGET = '.?AVGaitemSelectDialog@CS@@';
const MAX_SLOTS = 120;
const RARE = 4;

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;
const follow = (a) => a.readU8() === 0xe9 ? a.add(5).add(a.add(1).readS32()) : a;

const names = new Map();
function rtti (obj) {
  let vt;
  try { vt = obj.readPointer(); } catch (e) { return null; }
  if (!inImage(vt)) return null;
  const k = vt.toString();
  if (names.has(k)) return names.get(k);
  let n = null;
  try {
    const col = vt.sub(8).readPointer();
    if (inImage(col) && col.readU32() === 1) {
      const base = col.sub(col.add(0x14).readU32());
      n = base.add(col.add(0x0c).readU32()).add(0x10).readCString();
    }
  } catch (e) {}
  names.set(k, n);
  return n;
}

let window = null;
let runs = 0;
let lastRun = 0;
let quietSent = true;
const counts = {};
const hooked = new Set();

function hookSlots (w) {
  const vt = w.readPointer();
  let n = 0;
  for (let i = 0; i < MAX_SLOTS; i++) {
    let fn;
    try { fn = vt.add(i * 8).readPointer(); } catch (e) { break; }
    if (!inImage(fn)) break;
    const key = fn.toString();
    if (hooked.has(key)) continue;
    hooked.add(key);
    const slot = i;
    try {
      Interceptor.attach(fn, {
        onEnter (args) {
          if (window === null || !args[0].equals(window)) return;
          counts[slot] = (counts[slot] || 0) + 1;
          if (counts[slot] <= RARE) {
            send({ tag: 'call', slot, rva: fn.sub(lo).toString(), nth: counts[slot],
              ms_since_run: Date.now() - lastRun, runs });
          }
        },
      });
      n++;
    } catch (e) {}
  }
  send({ tag: 'hooked', window: w.toString(), vtable: vt.sub(lo).toString(), slots: n });
}

Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    let w;
    try { w = args[0].add(JOB_WINDOW).readPointer(); } catch (e) { return; }
    if (w.isNull()) return;
    if (rtti(w) === TARGET) {
      if (window === null || !w.equals(window)) {
        window = w;
        if (hooked.size === 0) hookSlots(w);
      }
      runs++;
      lastRun = Date.now();
      quietSent = false;
    } else if (!quietSent && lastRun !== 0 && Date.now() - lastRun > 50) {
      quietSent = true;
      send({ tag: 'item-list-quiet', last_run_ms_ago: Date.now() - lastRun, runs });
    }
  },
});

send({ tag: 'armed' });
