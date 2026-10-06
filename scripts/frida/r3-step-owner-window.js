// Where the item list's window is reachable from the R3 step's `this`.
//
// er-r3-view names the window from `MenuWindowJob::Run` (1.17.1 rva 0x7ae040), and in one run
// that never fired for `GaitemSelectDialog`, so view 3 could not fade the left panel. This asks
// two things on each R3 press in the item list: the step's `this` (1.17.1 rva 0x998260) and
// every pointer path of depth 1 or 2 from it, or from the object 0x8f8 below it, that lands on a
// `GaitemSelectDialog`. It also counts Run calls per window class to show whether Run sees the
// item list at all.
'use strict';

const STEP_RVA = 0x998260;
const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const SCAN = 0x1000;
const TARGET = '.?AVGaitemSelectDialog@CS@@';

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
function heap (p) {
  return !p.isNull() && !inImage(p) && p.compare(ptr('0x10000')) > 0 &&
    p.compare(ptr('0x7fffffffffff')) < 0 && p.and(7).toInt32() === 0;
}
function paths (root) {
  const out = [];
  for (let a = 0; a < SCAN; a += 8) {
    let p;
    try { p = root.add(a).readPointer(); } catch (e) { break; }
    if (!heap(p)) continue;
    if (rtti(p) === TARGET) out.push(`+0x${a.toString(16)}`);
    for (let b = 0; b < 0x400; b += 8) {
      let q;
      try { q = p.add(b).readPointer(); } catch (e) { break; }
      if (heap(q) && rtti(q) === TARGET) out.push(`+0x${a.toString(16)} -> +0x${b.toString(16)}`);
    }
  }
  return out;
}

const runSeen = {};
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const n = rtti(args[0].add(JOB_WINDOW).readPointer()) || '?';
      runSeen[n] = (runSeen[n] || 0) + 1;
    } catch (e) {}
  },
});

Interceptor.attach(lo.add(STEP_RVA), {
  onEnter (args) {
    const self = args[0];
    try {
      send({
        tag: 'step',
        self: self.toString(),
        selfClass: rtti(self),
        fromSelf: paths(self),
        fromBelow: paths(self.sub(0x8f8)),
        belowClass: rtti(self.sub(0x8f8)),
        runSeen,
        ours,
      });
    } catch (e) { send({ tag: 'error', err: String(e) }); }
  },
});

// er-r3-view's own `run_hook` (its log names `dll+0x4340`): is it called, and with which windows.
// The counts ride on every `step` message, and the first call for each window class sends one
// `ours` message of its own, so a class the hook never sees is the absence of that message.
const ours = {};
const r3 = Process.findModuleByName('er_r3_view.dll');
if (r3) {
  Interceptor.attach(r3.base.add(0x4340), {
    onEnter (args) {
      let n = '?';
      try { n = rtti(args[0].add(JOB_WINDOW).readPointer()) || '?'; } catch (e) {}
      const first = !(n in ours);
      ours[n] = (ours[n] || 0) + 1;
      if (first) send({ tag: 'ours', newClass: n, ours, runSeen });
    },
  });
}

send({ tag: 'armed', r3: r3 ? r3.base.toString() : null, runEntry: follow(lo.add(RUN_RVA)).sub(lo).toString(), runBytes: lo.add(RUN_RVA).readByteArray(6) });
