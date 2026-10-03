// With er-r3-view loaded: does R3 still reach the step from view 3, and which windows stay drawn?
//
// Read-only. Two hooks:
//   `apply` (1.17 rva `0x975890`), which er-r3-view's step calls with the new mode -- one message
//   per R3 press that reached the step, so a press from view 3 that never arrives is a missing
//   line rather than a guess.
//   `MenuWindowJob::Run` (1.17 rva `0x7ae040`) through `job+0x130`, to name every window pumped,
//   whatever its class, and to report which of its `SceneObjProxy` members report visible.
// The pad poll (1.17.1 rva `0x1f6d940`, `device+0x890`, `XINPUT_GAMEPAD_RIGHT_THUMB` 0x80) marks
// each R3 press at the pad, so "pressed but the step never ran" is visible as such.
'use strict';

const APPLY_RVA = 0x975890;
const RUN_RVA = 0x7ae040;
const POLL_RVA = 0x1f6d940;
const JOB_WINDOW = 0x130;
const PROXY_OFFSETS = [0x120, 0x188, 0x230, 0x3b8];

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;
const follow = (a) => a.readU8() === 0xe9 ? a.add(5).add(a.add(1).readS32()) : a;

const names = new Map();
function rtti (obj) {
  try {
    const vt = obj.readPointer();
    if (!inImage(vt)) return null;
    const k = vt.toString();
    if (names.has(k)) return names.get(k);
    const col = vt.sub(8).readPointer();
    let n = null;
    if (col.readU32() === 1) {
      const base = col.sub(col.add(0x14).readU32());
      n = base.add(col.add(0x0c).readU32()).add(0x10).readCString();
    }
    names.set(k, n);
    return n;
  } catch (e) { return null; }
}

// Visible flag of a proxy, read through the same GetDisplayInfo er-r3-view uses (+0xd8, byte +0xd6).
const info = Memory.alloc(0xd8);
function proxyVisible (proxy) {
  if (rtti(proxy) !== '.?AVSceneObjProxy@CS@@') return null;
  const getValue = new NativeFunction(proxy.readPointer().readPointer(), 'pointer', ['pointer']);
  const value = getValue(proxy);
  if (value.isNull() || (value.add(0x20).readU8() & 0x8f) === 0) return null;
  const iface = value.add(0x18).readPointer();
  const get = new NativeFunction(iface.readPointer().add(0xd8).readPointer(), 'void', ['pointer', 'pointer', 'pointer']);
  info.writeByteArray(new Array(0xd8).fill(0));
  get(iface, value.add(0x28).readPointer(), info);
  return info.add(0xd6).readU8() !== 0;
}

let windows = new Map();
let wantSurvey = false;
let probed = false;
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const w = args[0].add(JOB_WINDOW).readPointer();
      if (w.isNull()) return;
      const cls = rtti(w);
      if (cls === null) return;
      windows.set(w.toString(), { cls, at: Date.now() });
      // Once: the leading doubles and the tail of a visible proxy's DisplayInfo, to place Alpha.
      if (!probed && cls === '.?AVFrontEndView@CS@@') {
        probed = true;
        const proxy = w.add(0x120);
        if (proxyVisible(proxy)) {
          const doubles = [];
          for (let o = 0; o < 0x80; o += 8) doubles.push(o.toString(16) + '=' + info.add(o).readDouble());
          send({ tag: 'display-info', doubles, varsSet: info.add(0xd4).readU16(), visible: info.add(0xd6).readU8() });
        }
      }
      // Runs on the menu thread, which is where the Scaleform reads belong.
      if (wantSurvey) {
        wantSurvey = false;
        const now = Date.now();
        const out = [];
        for (const [p, s] of windows) {
          if (now - s.at > 500) continue;
          const vis = PROXY_OFFSETS.map(o => { try { return proxyVisible(ptr(p).add(o)); } catch (e) { return 'err'; } });
          out.push({ cls: s.cls, window: p, proxies: vis });
        }
        send({ tag: 'survey', windows: out });
      }
    } catch (e) {}
  },
});

Interceptor.attach(lo.add(APPLY_RVA), {
  onEnter (args) {
    const index = args[1].toInt32();
    let count = null;
    try { count = args[0].add(0x250).readU64().toNumber(); } catch (e) {}
    // Another caller applies (-1, count 2) every frame; only the item list's three-pane step is
    // the R3 press.
    if (count !== 3) return;
    const list = args[0];
    const panes = [];
    for (let i = 0; i < count; i++) {
      try {
        // Same slot arithmetic as FUN_140975890.
        const slot = list.add(i * 0x48 + ((-(list.toInt32() + 8)) & 7) + 0x48);
        const obj = slot.readPointer();
        const vt = obj.readPointer();
        panes.push({
          i,
          obj: obj.toString(),
          vt: 'rva 0x' + vt.sub(lo).toString(16),
          show: 'rva 0x' + vt.add(0x10).readPointer().sub(lo).toString(16),
          words: Array.from(new Uint32Array(obj.readByteArray(0x40))).map(v => '0x' + v.toString(16)),
        });
      } catch (e) { panes.push({ i, err: String(e) }); }
    }
    send({ tag: 'step', mode: index, count, list: list.toString(), panes });
    wantSurvey = index === 3;
  },
});

const last = new Map();
Interceptor.attach(lo.add(POLL_RVA), {
  onEnter (args) { this.d = args[0]; },
  onLeave () {
    try {
      const b = this.d.add(0x890).readU16();
      const k = this.d.toString();
      const was = last.get(k) || 0;
      last.set(k, b);
      if ((b & 0x80) && !(was & 0x80)) send({ tag: 'r3-press' });
    } catch (e) {}
  },
});

send({ tag: 'armed' });
