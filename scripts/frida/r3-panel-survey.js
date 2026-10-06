// Read-only: which display objects are still drawn in view 3, against views 0-2?
//
// The center (detail) panel shows in every view and survives er-r3-view's view 3, and fading
// objects to find it polluted the menu state for the rest of a session, because these windows
// outlive a menu close. So this only reads. A frame after each three-pane `apply` (1.17 rva
// `0x975890`, count 3), on the menu thread, it records `Visible` and `Alpha` of every
// `SceneObjProxy` embedded in the item-list window, in its `DetailStatusViewParts` (`apply`'s list
// minus 0x8f8), and one level into the heap objects either of them points at. On view 3 it reports
// every object drawn there (visible, alpha > 0) with the views 0-2 it was also drawn in.
'use strict';

const APPLY_RVA = 0x975890;
const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const MODE_TO_PARTS = 0x8f8;
const WINDOW_SCAN_BYTES = 0x2000;
const PARTS_SCAN_BYTES = 0x1000;
const CHILD_SCAN_BYTES = 0x600;

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;
const follow = (a) => a.readU8() === 0xe9 ? a.add(5).add(a.add(1).readS32()) : a;

const names = new Map();
function rttiOfVtable (vt) {
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

const info = Memory.alloc(0xd8);
function state (proxy) {
  const getValue = new NativeFunction(proxy.readPointer().readPointer(), 'pointer', ['pointer']);
  const value = getValue(proxy);
  if (value.isNull() || (value.add(0x20).readU8() & 0x8f) === 0) return null;
  const iface = value.add(0x18).readPointer();
  if (iface.isNull()) return null;
  const get = new NativeFunction(iface.readPointer().add(0xd8).readPointer(), 'void', ['pointer', 'pointer', 'pointer']);
  info.writeByteArray(new Array(0xd8).fill(0));
  get(iface, value.add(0x28).readPointer(), info);
  return { visible: info.add(0xd6).readU8() !== 0, alpha: info.add(0x28).readDouble() };
}

function proxiesIn (obj, bytes) {
  const out = [];
  for (let o = 0; o < bytes; o += 8) {
    let vt;
    try { vt = obj.add(o).readPointer(); } catch (e) { break; }
    if (rttiOfVtable(vt) === '.?AVSceneObjProxy@CS@@') out.push(o);
  }
  return out;
}

function heapChildren (obj, bytes) {
  const out = [];
  for (let o = 0; o < bytes; o += 8) {
    let p;
    try { p = obj.add(o).readPointer(); } catch (e) { break; }
    if (p.isNull() || inImage(p) || p.compare(ptr('0x10000')) < 0 || p.compare(ptr('0x7fffffffffff')) > 0) continue;
    if (p.and(7).toInt32() !== 0) continue;
    out.push([o, p]);
  }
  return out;
}

function snapshot (window, parts) {
  const seen = new Set();
  const out = new Map();
  const targets = [['window', window, WINDOW_SCAN_BYTES], ['parts', parts, PARTS_SCAN_BYTES]];
  for (const [label, root, bytes] of [['window', window, WINDOW_SCAN_BYTES], ['parts', parts, PARTS_SCAN_BYTES]]) {
    for (const [o, p] of heapChildren(root, bytes)) {
      if (p.equals(window) || p.equals(parts) || seen.has(p.toString())) continue;
      seen.add(p.toString());
      targets.push([label + '+0x' + o.toString(16) + '->', p, CHILD_SCAN_BYTES]);
    }
  }
  for (const [label, obj, bytes] of targets) {
    for (const off of proxiesIn(obj, bytes)) {
      try {
        const s = state(obj.add(off));
        if (s !== null) out.set(label + '+0x' + off.toString(16), s);
      } catch (e) {}
    }
  }
  return out;
}

let itemList = null;
let pending = null;
const drawnIn = new Map(); // key -> Set of modes it was drawn in
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const w = args[0].add(JOB_WINDOW).readPointer();
      if (w.isNull() || rttiOfVtable(w.readPointer()) !== '.?AVGaitemSelectDialog@CS@@') return;
      itemList = w;
      if (pending === null) return;
      const { mode, parts } = pending;
      pending = null;
      const snap = snapshot(w, parts);
      const drawnNow = [];
      for (const [k, s] of snap) {
        if (!(s.visible && s.alpha > 0)) continue;
        drawnNow.push(k);
        if (!drawnIn.has(k)) drawnIn.set(k, new Set());
        drawnIn.get(k).add(mode);
      }
      if (mode === 3) {
        send({ tag: 'drawn-in-view-3', count: drawnNow.length, objects: drawnNow.map(k => k + ' also:' + [...drawnIn.get(k)].filter(m => m !== 3).join('')) });
      } else {
        send({ tag: 'view', mode, drawn: drawnNow.length, proxies: snap.size });
      }
    } catch (e) { send({ tag: 'error', err: String(e) }); }
  },
});

Interceptor.attach(lo.add(APPLY_RVA), {
  onEnter (args) {
    let count;
    try { count = args[0].add(0x250).readU64().toNumber(); } catch (e) { return; }
    if (count !== 3) return;
    pending = { mode: args[1].toInt32(), parts: args[0].sub(MODE_TO_PARTS) };
  },
});

send({ tag: 'armed' });
