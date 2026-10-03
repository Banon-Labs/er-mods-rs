// How often `MenuWindowJob::Run` (1.17.1 rva 0x7ae040) runs the item list's window while it is open.
//
// er-r3-view keeps its board up until the item list has gone `ITEM_LIST_GONE_MS` without a Run,
// which the player sees as a one-second delay after B. A shorter draw-side cutoff is safe only if
// it is several times the longest gap between Runs while the list is open; this sends that gap.
// One message per 120 item-list Runs, from inside the hook, so nothing here runs on a timer.
'use strict';

const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const TARGET = '.?AVGaitemSelectDialog@CS@@';
const BATCH = 120;

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

let last = 0;
let gaps = [];
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    let w;
    try { w = args[0].add(JOB_WINDOW).readPointer(); } catch (e) { return; }
    if (w.isNull() || rtti(w) !== TARGET) return;
    const now = Date.now();
    if (last !== 0) gaps.push(now - last);
    last = now;
    if (gaps.length >= BATCH) {
      const sorted = gaps.slice().sort((a, b) => a - b);
      send({
        tag: 'cadence',
        runs: gaps.length,
        median_ms: sorted[sorted.length >> 1],
        p99_ms: sorted[Math.floor(sorted.length * 0.99)],
        max_ms: sorted[sorted.length - 1],
      });
      gaps = [];
    }
  },
});

send({ tag: 'armed' });
