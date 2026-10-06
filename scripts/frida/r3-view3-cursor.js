// In R3 view 3 the item list's cursor still moves, but the detail panel never names the new
// record: `parts-b` (1.17.1 0x999930) asks the object at `parts+0x180` whether the panel is shown
// (`vtable+8`) and, in view 0's layout that view 3 borrows, skips the naming. Measured 2026-10-03:
// two cursor moves in view 3 reached parts-b and never reached the record naming at 0x99a5a0.
//
// This version reads that gate: the class of `parts+0x180`, the rva of its `vtable+8`, that
// function's first instructions, and what it returns in each view (the R3 step at 0x998260 reports
// the view). Reported once per (view, answer). No timers, no watchpoints.
'use strict';

const PARTS_B = 0x999930;
const STEP = 0x998260;
const MODE_OFFSET = 0x8f8;
const GATE_OFFSET = 0x180;

const mod = Process.findModuleByName('eldenring.exe');
const inImage = (p) => !p.isNull() && p.compare(mod.base) >= 0 && p.compare(mod.base.add(mod.size)) < 0;
const rva = (p) => inImage(p) ? '0x' + p.sub(mod.base).toString(16) : p.toString();

function rtti (obj) {
  try {
    const vt = obj.readPointer();
    if (!inImage(vt)) return null;
    const col = vt.sub(8).readPointer();
    if (!inImage(col) || col.readU32() !== 1) return null;
    const image = col.sub(col.add(0x14).readU32());
    return image.add(col.add(0x0c).readU32()).add(0x10).readCString();
  } catch (e) {
    return null;
  }
}

let view = null;
let described = false;
const seen = new Set();
let gateFn = null;

Interceptor.attach(mod.base.add(STEP), {
  onEnter (args) { this.list = args[0]; },
  onLeave () {
    try { view = this.list.add(MODE_OFFSET).readS32(); } catch (e) { view = null; }
  },
});

Interceptor.attach(mod.base.add(PARTS_B), {
  onEnter (args) {
    const gate = args[0].add(GATE_OFFSET);
    if (!described) {
      described = true;
      const fn = gate.readPointer().add(8).readPointer();
      gateFn = fn;
      const code = [];
      let at = fn;
      for (let i = 0; i < 12; i++) {
        const ins = Instruction.parse(at);
        code.push(ins.toString());
        at = ins.next;
      }
      send({ gate_class: rtti(gate), gate_vtable: rva(gate.readPointer()), gate_fn: rva(fn), code });
      Interceptor.attach(fn, {
        onEnter (a) { this.obj = a[0]; },
        onLeave (ret) {
          const answer = ret.toUInt32() & 0xff;
          const key = view + '/' + answer + '/' + this.obj;
          if (seen.has(key)) return;
          seen.add(key);
          const words = [];
          for (let off = 0; off < 0x40; off += 4) words.push(this.obj.add(off).readU32().toString(16));
          send({ view, answer, obj: this.obj.toString(), words });
        },
      });
    }
  },
});
send({ armed: ['0x' + PARTS_B.toString(16), '0x' + STEP.toString(16)] });
