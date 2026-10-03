// In view 3, run pane 0's own "enter view" callback, so the game hides its right and center panels.
//
// The player reports that one native view hides the right and center panels and leaves only the
// left list. Each pane's callback is `vtable+0x10(pane, &shown)` on the slot objects at
// `list + 0x48 + i*0x48` (8-aligned), and pane 0's (1.17 `FUN_140999130`) runs
// `FUN_140999750(parts, 0)`, `FUN_140999930(parts, 0)`, `FUN_140999bd0(parts)` and the `parts+0xb50`
// sub-list apply. er-r3-view's view 3 only copies the first of those. This calls the whole callback
// with `shown = true` right after the step applies mode 3, on the menu thread inside `apply`'s
// caller, and sends one line per call so the effect can be lined up with what the player saw.
'use strict';

const APPLY_RVA = 0x975890;
const mod = Process.findModuleByName('eldenring.exe');
const shown = Memory.alloc(8);
shown.writeU8(1);

Interceptor.attach(mod.base.add(APPLY_RVA), {
  onEnter (args) {
    this.list = args[0];
    this.mode = args[1].toInt32();
  },
  onLeave () {
    let count;
    try { count = this.list.add(0x250).readU64().toNumber(); } catch (e) { return; }
    if (count !== 3 || this.mode !== 3) return;
    try {
      // Same slot arithmetic as FUN_140975890 (`apply`).
      const slot = this.list.add(((-(this.list.toInt32() + 8)) & 7) + 0x48);
      const pane = slot.readPointer();
      const callback = new NativeFunction(pane.readPointer().add(0x10).readPointer(), 'void', ['pointer', 'pointer']);
      callback(pane, shown);
      send({ tag: 'entered-view0-layout', callback: pane.readPointer().add(0x10).readPointer().sub(mod.base).toString() });
    } catch (e) { send({ tag: 'error', err: String(e) }); }
  },
});

send({ tag: 'armed' });
