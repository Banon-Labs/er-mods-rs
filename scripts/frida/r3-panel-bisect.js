// Bisect the center panel: each entry into view 3 fades ONE group of the objects still drawn there.
//
// `r3-panel-survey.js` found 128 `SceneObjProxy` objects drawn in view 3 (visible, alpha > 0), almost
// all of them drawn in views 0-2 too, so state alone cannot name the center panel. The player can:
// entry N into view 3 fades only group N (alpha 0) and the next R3 press restores exactly those, so
// "the panel vanished on entry N" names the group in one pass. Groups, by where the object lives:
//   1. the item-list window's own proxies (minus the four er-r3-view owns)
//   2. `DetailStatusViewParts` and the heap objects it points at
//   3. window heap children at window+0x000..0x3ff
//   4. window heap children at window+0x400..0x13ff
//   5. window heap children at window+0x1400 and up
// Runs on the menu thread, a frame after the three-pane `apply` (1.17 rva `0x975890`).
'use strict';

const APPLY_RVA = 0x975890;
const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const MODE_TO_PARTS = 0x8f8;
const WINDOW_SCAN_BYTES = 0x2000;
const PARTS_SCAN_BYTES = 0x1000;
const CHILD_SCAN_BYTES = 0x600;
const DLL_OWNED = [0x120, 0x188, 0x230, 0x3b8];

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
function gfx (proxy) {
  const getValue = new NativeFunction(proxy.readPointer().readPointer(), 'pointer', ['pointer']);
  const value = getValue(proxy);
  if (value.isNull() || (value.add(0x20).readU8() & 0x8f) === 0) return null;
  const iface = value.add(0x18).readPointer();
  if (iface.isNull()) return null;
  return { iface, data: value.add(0x28).readPointer() };
}
function getInfo (v) {
  const get = new NativeFunction(v.iface.readPointer().add(0xd8).readPointer(), 'void', ['pointer', 'pointer', 'pointer']);
  info.writeByteArray(new Array(0xd8).fill(0));
  get(v.iface, v.data, info);
  return { visible: info.add(0xd6).readU8() !== 0, alpha: info.add(0x28).readDouble() };
}
function setAlpha (v, a) {
  const set = new NativeFunction(v.iface.readPointer().add(0xe0).readPointer(), 'void', ['pointer', 'pointer', 'pointer']);
  info.writeByteArray(new Array(0xd8).fill(0));
  info.add(0xd4).writeU16(0x20);
  info.add(0x28).writeDouble(a);
  set(v.iface, v.data, info);
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

// Group N's proxies, as absolute addresses.
function group (n, window, parts) {
  const out = [];
  const add = (obj, bytes, skip) => { for (const o of proxiesIn(obj, bytes)) if (!skip || !skip.includes(o)) out.push(obj.add(o)); };
  if (n === 1) add(window, WINDOW_SCAN_BYTES, DLL_OWNED);
  if (n === 2) {
    add(parts, PARTS_SCAN_BYTES);
    for (const [, p] of heapChildren(parts, PARTS_SCAN_BYTES)) if (!p.equals(window) && !p.equals(parts)) add(p, CHILD_SCAN_BYTES);
  }
  if (n >= 3) {
    const [lo3, hi3] = n === 3 ? [0, 0x400] : n === 4 ? [0x400, 0x1400] : [0x1400, WINDOW_SCAN_BYTES];
    for (const [o, p] of heapChildren(window, WINDOW_SCAN_BYTES)) {
      if (o < lo3 || o >= hi3 || p.equals(window) || p.equals(parts)) continue;
      add(p, CHILD_SCAN_BYTES);
    }
  }
  return out;
}

let entry = 0;
let pending = null;
let faded = [];
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const w = args[0].add(JOB_WINDOW).readPointer();
      if (w.isNull() || rttiOfVtable(w.readPointer()) !== '.?AVGaitemSelectDialog@CS@@' || pending === null) return;
      const { mode, parts } = pending;
      pending = null;
      if (faded.length) {
        for (const f of faded) { try { const v = gfx(f.proxy); if (v) setAlpha(v, f.alpha); } catch (e) {} }
        send({ tag: 'restored', entry, count: faded.length });
        faded = [];
      }
      if (mode !== 3) return;
      entry = entry % 5 + 1;
      for (const proxy of group(entry, w, parts)) {
        try {
          const v = gfx(proxy);
          if (v === null) continue;
          const s = getInfo(v);
          if (s.visible && s.alpha > 0) { setAlpha(v, 0); faded.push({ proxy, alpha: s.alpha }); }
        } catch (e) {}
      }
      send({ tag: 'entry', entry, faded: faded.length });
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
