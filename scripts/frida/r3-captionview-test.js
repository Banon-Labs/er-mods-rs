// Does hiding `CaptionView` (the button-guide bar) stop R3 from reaching the item list?
//
// er-r3-view's view 3 clears `Visible` on `CaptionView`'s proxies, and from view 3 no R3 press has
// ever reached the step: the item list's R3 handler (a vtable slot, e.g. 1.17 `FUN_1408f5170`, which
// calls the step unconditionally) is simply not invoked. If the press is routed through the guide
// bar's prompt, putting `CaptionView` back must bring R3 back. One variable: this sets those proxies
// visible again right after the step, on the menu thread, and changes nothing else.
'use strict';

const APPLY_RVA = 0x975890;
const RUN_RVA = 0x7ae040;
const JOB_WINDOW = 0x130;
const PROXY_OFFSETS = [0x120, 0x188, 0x230, 0x3b8];
const R3_HANDLERS = [0x8f5170, 0x8f5260, 0x8f5510];

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
function setVisible (proxy, on) {
  if (rtti(proxy) !== '.?AVSceneObjProxy@CS@@') return false;
  const getValue = new NativeFunction(proxy.readPointer().readPointer(), 'pointer', ['pointer']);
  const value = getValue(proxy);
  if (value.isNull() || (value.add(0x20).readU8() & 0x8f) === 0) return false;
  const iface = value.add(0x18).readPointer();
  const set = new NativeFunction(iface.readPointer().add(0xe0).readPointer(), 'void', ['pointer', 'pointer', 'pointer']);
  info.writeByteArray(new Array(0xd8).fill(0));
  info.add(0xd4).writeU16(0x40);
  info.add(0xd6).writeU8(on ? 1 : 0);
  set(iface, value.add(0x28).readPointer(), info);
  return true;
}

let caption = null;
let pending = false;
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    try {
      const w = args[0].add(JOB_WINDOW).readPointer();
      if (w.isNull()) return;
      if (rtti(w) === '.?AVCaptionView@CS@@') caption = w;
      // A frame after the step, so the DLL's own hide has already run.
      if (pending && caption !== null) {
        pending = false;
        const shown = PROXY_OFFSETS.filter(o => { try { return setVisible(caption.add(o), true); } catch (e) { return false; } });
        send({ tag: 'caption-reshown', proxies: shown.map(o => '0x' + o.toString(16)) });
      }
    } catch (e) {}
  },
});

Interceptor.attach(lo.add(APPLY_RVA), {
  onEnter (args) {
    let count;
    try { count = args[0].add(0x250).readU64().toNumber(); } catch (e) { return; }
    if (count !== 3) return;
    const mode = args[1].toInt32();
    send({ tag: 'step', mode });
    if (mode === 3) pending = true;
  },
});

for (const rva of R3_HANDLERS) {
  Interceptor.attach(follow(lo.add(rva)), { onEnter () { send({ tag: 'r3-handler', rva: '0x' + rva.toString(16) }); } });
}

// The press at the pad (`DLUID::PadDevice::Poll`, 1.17.1 rva `0x1f6d940`, `device+0x890`,
// `XINPUT_GAMEPAD_RIGHT_THUMB` 0x80), so a press that reaches no handler is visible as one.
const lastButtons = new Map();
Interceptor.attach(lo.add(0x1f6d940), {
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

send({ tag: 'armed' });
