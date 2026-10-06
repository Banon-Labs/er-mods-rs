// Which instruction writes the item list's R3 view mode, and where does it wrap at 3?
//
// The mode is a u32 at `(*(GaitemSelectDialog+0xf98)) + 0x7f0` (a `CS::SceneObjProxy`), measured by
// `r3-menu-view-toggle.js`: 0, 1, 2, 0 on successive R3 presses. A write watchpoint on that one
// address names the writer.
//
// Armed on one thread: the one running `MenuWindowJob::Run` (1.17 rva `0x7ae040`) for the item
// list, which is the menu thread. Arming every thread has killed this game twice (AGENTS.md). The
// slot is cleared by that thread's next `Run` after `MAX_HITS` hits, and in `dispose`, so nothing
// outlives the agent.
'use strict';

const MENU_WINDOW_JOB_RUN_RVA = 0x7ae040;
const MENU_WINDOW_JOB_WINDOW_OFFSET = 0x130;
const VIEW_PROXY_OFFSET = 0xf98;
const VIEW_MODE_OFFSET = 0x7f0;
const SLOT = 0;
const MAX_HITS = 3;

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;

function follow (address) {
  return address.readU8() === 0xe9 ? address.add(5).add(address.add(1).readS32()) : address;
}

function rttiName (vtable) {
  try {
    const col = vtable.sub(8).readPointer();
    if (col.readU32() !== 1) return null;
    const imageBase = col.sub(col.add(0x14).readU32());
    return imageBase.add(col.add(0x0c).readU32()).add(0x10).readCString();
  } catch (e) { return null; }
}

let armed = null; // { thread, address }
let hits = 0;

function threadById (id) {
  for (const t of Process.enumerateThreads()) if (t.id === id) return t;
  return null;
}

function disarm () {
  if (armed === null) return;
  try {
    const t = threadById(armed.thread);
    if (t !== null) t.unsetHardwareWatchpoint(SLOT);
    send({ tag: 'disarmed', thread: armed.thread });
    armed = null;
  } catch (e) {
    // Keep `armed` so the handler goes on absorbing hits rather than letting one kill the game.
    send({ tag: 'disarm-failed', thread: armed.thread, err: String(e) });
  }
}

function describe (pc) {
  const lines = [];
  let p = pc.sub(0x60);
  // Walk forward from a little before the hit so the compare and wrap show up in context; the first
  // few decodes may be misaligned, the ones near `pc` are not.
  for (let i = 0; i < 60 && p.compare(pc.add(0x80)) < 0; i++) {
    try {
      const ins = Instruction.parse(p);
      lines.push('0x' + p.sub(lo).toString(16) + ' ' + ins.toString());
      p = ins.next;
    } catch (e) { p = p.add(1); }
  }
  return lines;
}

Process.setExceptionHandler(function (details) {
  if (armed === null || Process.getCurrentThreadId() !== armed.thread) return false;
  hits += 1;
  const pc = details.context.pc;
  let value = null;
  try { value = armed.address.readU32(); } catch (e) {}
  send({
    tag: 'writer',
    hit: hits,
    type: details.type,
    pc: pc.toString(),
    rva: inImage(pc) ? '0x' + pc.sub(lo).toString(16) : null,
    value,
    backtrace: Thread.backtrace(details.context, Backtracer.ACCURATE).slice(0, 8)
      .map(a => inImage(a) ? 'rva 0x' + a.sub(lo).toString(16) : a.toString()),
    code: hits === 1 ? describe(pc) : undefined,
  });
  // Not from inside the handler: the handler resumes the thread from `details.context`, debug
  // registers included, which could put the slot straight back. The next `MenuWindowJob::Run` on
  // the armed thread clears it instead, from the same place that armed it.
  return true;
});

Interceptor.attach(follow(lo.add(MENU_WINDOW_JOB_RUN_RVA)), {
  onEnter (args) {
    if (armed !== null && hits >= MAX_HITS && Process.getCurrentThreadId() === armed.thread) {
      disarm();
      return;
    }
    if (armed !== null || hits >= MAX_HITS) return;
    try {
      const window = args[0].add(MENU_WINDOW_JOB_WINDOW_OFFSET).readPointer();
      if (window.isNull()) return;
      const vt = window.readPointer();
      if (!inImage(vt) || rttiName(vt) !== '.?AVGaitemSelectDialog@CS@@') return;
      const address = window.add(VIEW_PROXY_OFFSET).readPointer().add(VIEW_MODE_OFFSET);
      const id = Process.getCurrentThreadId();
      const t = threadById(id);
      if (t === null) return;
      t.setHardwareWatchpoint(SLOT, address, 4, 'w');
      armed = { thread: id, address };
      send({ tag: 'armed', thread: id, address: address.toString(), value: address.readU32() });
    } catch (e) {
      send({ tag: 'arm-failed', err: String(e) });
      armed = { thread: -1, address: ptr(0) }; // do not retry every frame
    }
  },
});

rpc.exports.dispose = disarm;
send({ tag: 'loaded' });
