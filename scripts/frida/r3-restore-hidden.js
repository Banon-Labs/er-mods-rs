// Put back every proxy the R3 experiments hid and never restored, then report whether R3 works.
//
// Measured: 45 R3 presses reached the pad and none reached any item-list R3 handler, in any view.
// The menu windows outlive a menu close, so state left by earlier hides persists:
//   * er-r3-view cleared `Visible` on the menu windows' proxies, and its restore skipped windows it
//     judged closed (it restored 5-7 of 11);
//   * the hide-all prototype faded 22 proxies to alpha 0 and was detached before restoring them.
// This restores the set those runs recorded: `Visible` on the proxies er-r3-view's own log and the
// first Frida hide named, and alpha 100 on the prototype's list (every one of them read alpha 100
// before it was faded). Runs once, inside `MenuWindowJob::Run`, on the menu thread.
'use strict';

const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const PADPOLL_RVA = 0x1f6d940;

const VISIBLE = {
  '.?AVClockView@CS@@': [0x120],
  '.?AVEquipDialog@CS@@': [0x120, 0x230],
  '.?AVMainTopDialog@CS@@': [0x120, 0x188],
  '.?AVCaptionView@CS@@': [0x120],
  '.?AVBackScreen@CS@@': [0x120],
  '.?AVGaitemSelectDialog@CS@@': [0x120, 0x230],
};
const ITEM_LIST_ALPHA = [0x120, 0x230, 0x290, 0x2f0, 0x350, 0xaf8, 0xc88, 0x1d70];
// `parts` is the item list's `DetailStatusViewParts`, the step's `this`. Measured on the previous
// session: window `0x29666080`, parts `0x2966cf08`, so `window+0x6e88`. Every write below is still
// gated on the target carrying the `SceneObjProxy` RTTI.
const PARTS_FROM_WINDOW = 0x6e88;
const PARTS_CHILD_ALPHA = {
  0x510: [0x30, 0x288, 0x408, 0x468],
  0x558: [0x0],
  0xf20: [0x118, 0x248, 0x2a8, 0x308],
  0xf68: [0x60, 0xf8, 0x2b8, 0x498, 0x530],
};

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;
const follow = (a) => a.readU8() === 0xe9 ? a.add(5).add(a.add(1).readS32()) : a;

function rtti (obj) {
  try {
    const vt = obj.readPointer();
    if (!inImage(vt)) return null;
    const col = vt.sub(8).readPointer();
    if (col.readU32() !== 1) return null;
    const base = col.sub(col.add(0x14).readU32());
    return base.add(col.add(0x0c).readU32()).add(0x10).readCString();
  } catch (e) { return null; }
}

const info = Memory.alloc(0xd8);
function set (proxy, varsSet, fill) {
  if (rtti(proxy) !== '.?AVSceneObjProxy@CS@@') return false;
  const getValue = new NativeFunction(proxy.readPointer().readPointer(), 'pointer', ['pointer']);
  const value = getValue(proxy);
  if (value.isNull() || (value.add(0x20).readU8() & 0x8f) === 0) return false;
  const iface = value.add(0x18).readPointer();
  const fn = new NativeFunction(iface.readPointer().add(0xe0).readPointer(), 'void', ['pointer', 'pointer', 'pointer']);
  info.writeByteArray(new Array(0xd8).fill(0));
  info.add(0xd4).writeU16(varsSet);
  fill(info);
  fn(iface, value.add(0x28).readPointer(), info);
  return true;
}
const showProxy = (p) => set(p, 0x40, (i) => i.add(0xd6).writeU8(1));
const opaqueProxy = (p) => set(p, 0x20, (i) => i.add(0x28).writeDouble(100));

const done = new Set();
const report = [];
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const w = args[0].add(JOB_WINDOW).readPointer();
      if (w.isNull() || done.has(w.toString())) return;
      const cls = rtti(w);
      if (!(cls in VISIBLE)) return;
      done.add(w.toString());
      for (const o of VISIBLE[cls]) report.push(cls.slice(4, -3) + ' visible+0x' + o.toString(16) + '=' + showProxy(w.add(o)));
      if (cls === '.?AVGaitemSelectDialog@CS@@') {
        for (const o of ITEM_LIST_ALPHA) report.push('item alpha+0x' + o.toString(16) + '=' + opaqueProxy(w.add(o)));
        const parts = w.add(PARTS_FROM_WINDOW);
        for (const [c, offs] of Object.entries(PARTS_CHILD_ALPHA)) {
          const child = parts.add(Number(c)).readPointer();
          for (const o of offs) report.push('parts+0x' + Number(c).toString(16) + '->+0x' + o.toString(16) + '=' + opaqueProxy(child.add(o)));
        }
      }
      send({ tag: 'restored', window: cls, report: report.splice(0) });
    } catch (e) { send({ tag: 'restore-error', err: String(e) }); }
  },
});

const lastButtons = new Map();
Interceptor.attach(lo.add(PADPOLL_RVA), {
  onEnter (args) { this.d = args[0]; },
  onLeave () {
    try {
      const b = this.d.add(0x890).readU16();
      const k = this.d.toString();
      const was = lastButtons.get(k) || 0;
      lastButtons.set(k, b);
      if ((b & 0x80) && !(was & 0x80)) send({ tag: 'r3-pad' });
    } catch (e) {}
  },
});
for (const rva of [0x8f5170, 0x8f5260, 0x8f5510]) {
  Interceptor.attach(follow(lo.add(rva)), { onEnter () { send({ tag: 'r3-handler', rva: '0x' + rva.toString(16) }); } });
}

send({ tag: 'armed' });
