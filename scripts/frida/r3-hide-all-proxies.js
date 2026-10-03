// Prototype: in view 3, fade every SceneObjProxy of the item list to alpha 0, not just four.
//
// er-r3-view fades the four `CS::MenuWindow` proxies of `GaitemSelectDialog` (+0x120, +0x188,
// +0x230, +0x3b8) and sets detail level 0, which left two of the menu's three panels drawn. The rest
// live as further `SceneObjProxy` members, embedded either in the window or in the
// `DetailStatusViewParts` the R3 step runs on. This scans both objects at 8-byte stride for an
// embedded proxy (vtable RTTI `.?AVSceneObjProxy@CS@@`), reports each with its alpha and visible
// flag, and on entering view 3 fades every visible one, restoring exactly those on leaving it.
//
// Alpha rather than `Visible`: a hidden item list stops taking R3 (measured, 18 presses lost).
// Runs inside `apply` (1.17 rva `0x975890`, count 3), which er-r3-view's step calls on the menu
// thread, so the Scaleform calls are on the thread that owns them.
'use strict';

const APPLY_RVA = 0x975890;
const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const MODE_TO_PARTS = 0x8f8; // apply's list is parts+0x8f8
const WINDOW_SCAN_BYTES = 0x2000;
const PARTS_SCAN_BYTES = 0x1000;

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
  return { alpha: info.add(0x28).readDouble(), visible: info.add(0xd6).readU8() !== 0 };
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

let itemList = null;
let reset = false;
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const w = args[0].add(JOB_WINDOW).readPointer();
      if (!w.isNull() && rttiOfVtable(w.readPointer()) === '.?AVGaitemSelectDialog@CS@@') {
        itemList = w;
        // Once, on the menu thread: undo the alpha 0 the previous version of this agent left on
        // the window's own proxies, which er-r3-view then saved as their original value.
        if (!reset) {
          reset = true;
          const done = [0x120, 0x230].map(o => { try { const v = gfx(w.add(o)); if (v) setAlpha(v, 100); return v !== null; } catch (e) { return false; } });
          send({ tag: 'reset-item-list-alpha', done });
        }
      }
    } catch (e) {}
  },
});

let faded = [];
Interceptor.attach(lo.add(APPLY_RVA), {
  onEnter (args) {
    this.list = args[0];
    this.mode = args[1].toInt32();
  },
  onLeave () {
    let count;
    try { count = this.list.add(0x250).readU64().toNumber(); } catch (e) { return; }
    if (count !== 3) return;
    const parts = this.list.sub(MODE_TO_PARTS);
    if (this.mode === count) {
      const report = [];
      // The direct scan faded ten proxies and view 0's detail panel stayed up, so its display
      // objects hang off a heap child. Add every heap pointer held in `parts` one level down.
      // The center panel survived the window and parts scans, so add the window's heap children too.
      const targets = [['window', itemList, WINDOW_SCAN_BYTES], ['parts', parts, PARTS_SCAN_BYTES]];
      const seen = new Set();
      for (const [root, rootLabel, rootBytes] of [[parts, 'parts', PARTS_SCAN_BYTES], [itemList, 'window', WINDOW_SCAN_BYTES]]) {
        if (root === null) continue;
        for (let o = 0; o < rootBytes; o += 8) {
          let p;
          try { p = root.add(o).readPointer(); } catch (e) { break; }
          if (p.isNull() || inImage(p) || p.compare(ptr('0x10000')) < 0 || p.compare(ptr('0x7fffffffffff')) > 0) continue;
          if (p.and(7).toInt32() !== 0 || p.equals(parts) || (itemList && p.equals(itemList)) || seen.has(p.toString())) continue;
          seen.add(p.toString());
          targets.push([rootLabel + '+0x' + o.toString(16) + '->', p, 0x600]);
        }
      }
      for (const [label, obj, bytes] of targets) {
        if (obj === null) continue;
        for (const off of proxiesIn(obj, bytes)) {
          // er-r3-view owns the window's own four proxies; fading them first made it record alpha 0
          // as their original value and restore them to 0 in every view.
          if (label === 'window' && [0x120, 0x188, 0x230, 0x3b8].includes(off)) continue;
          const proxy = obj.add(off);
          try {
            const v = gfx(proxy);
            if (v === null) continue;
            const s = getInfo(v);
            if (s.visible && s.alpha > 0) {
              report.push(label + '+0x' + off.toString(16) + ' a=' + s.alpha);
              setAlpha(v, 0);
              faded.push({ proxy, alpha: s.alpha });
            }
          } catch (e) { report.push(label + '+0x' + off.toString(16) + ' err ' + e); }
        }
      }
      send({ tag: 'faded', count: faded.length, report });
    } else if (faded.length) {
      for (const f of faded) {
        try { const v = gfx(f.proxy); if (v) setAlpha(v, f.alpha); } catch (e) {}
      }
      send({ tag: 'restored', count: faded.length });
      faded = [];
    }
  },
});

send({ tag: 'armed' });
