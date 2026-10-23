// Which menu binding id is "cancel", asked of the game rather than of a keyboard.
//
// Every game address in this file is a 2.7.2.0 rva (`eldenring-deobf-1.17.2.bin`, VA =
// 0x140000000 + rva).
//
// # Why a binding id and not a virtual key
//
// The bounds-popup close currently fires on `Q` or `Escape` or pad `B`, which are three constants
// standing in for one fact. The player, 2026-09-18: "Just so you're aware, user's can rebind keys
// and gamepad buttons." A rebind moves every one of those and the popup becomes inescapable again
// for the person who moved it.
//
// The game has the rebind-aware answer already:
//
// ```text
//   FUN_14075ebf0(pad, bindingId) -> repeat count
//     FUN_140757300(&{pad, bindingId})        ; analog magnitude, and the reason this is the
//       GLOBAL_CSPcKeyConfig                  ; rebind-aware layer -- the config IS consulted
//       FUN_140242ab0(cfg, out, bindingId, 2)
//     FUN_140759e30(pad, bindingId)           ; the boolean half
// ```
//
// So a BINDING id is the logical action and `CSPcKeyConfig` maps the player's physical key or
// button onto it. Bake the binding id and a rebind is followed for free; bake a virtual key and it
// is not. Two ids in this namespace are already measured and both are confirmed above by their own
// wrappers -- `0x2c` list-down (`FUN_14075a170`) and `0x2d` list-up (`FUN_14075a150`).
//
// # What this agent does
//
// It never guesses which id cancel is. It captures a live `CSEzMenuViewerPad` from the game's own
// per-frame call, then asks the game `FUN_14075ebf0(pad, id)` for every id in the namespace and
// reports the ones that answer nonzero, on change. Whatever the player presses to back out lights
// up its own id, whatever they have it bound to.
//
// The sweep runs inside a hook on the game's menu thread rather than from Frida's own thread,
// because the function it calls reads menu state that thread owns.
//
// Measured on `eldenring-deobf-1.17.2.bin` 2026-10-23. `0x14075ebf0` is the only match for the
// whole repeat-count body (`48895c2408 57 4883ec30 488bf9 48894c2420 488d4c2420 89542428 8bda
// e8 .. 0f57c9 0f2ec1 7a20 751e 8bd3 488bcf e8 .. 33c9 84c0 0f95c1`); its first call lands on
// `0x140757300` (the analog half, which calls `FUN_140242ab0(cfg, out, [rbx+8], 2)`) and its
// second on `0x140759e30`. `0x140759e30` is the only match for the boolean half's prologue, and at
// `+0x65` it calls `0x14075ec70`, the `bindingForward` hook below. The list wrappers
// `0x14075a170` / `0x14075a150` load `edx = 0x2c` / `0x2d` and call `0x14075ebf0`.
//
// Every capture and intercept failure in this agent's history -- the `0x35` read, both `unable to
// intercept` errors, and the `bindingForward` hook -- was taken in a 1.17.1 process at the 1.16.2
// addresses `0x140756000`, `0x14075d8f0`, `0x14075d970` and `0x140758b30`. In
// `eldenring-deobf-1.17.1.bin` each of those lies inside a different function (`.pdata`: `+0x20`,
// `+0x30`, `+0x30`, `+0x70`), so those runs measured nothing about the functions named here, and
// the history is not repeated below. Whether `bindingPressed` and `bindingBoolean` can take a
// trampoline on 2.7.2.0 is unmeasured; `0x14075ebf0` opens with 10 bytes of plain instructions
// (`mov [rsp+8], rbx; push rdi; sub rsp, 0x30`). The agent only calls them.

const RVA = {
  // (pad, bindingId) -> repeat count. The question this agent asks the game. Called, never
  // hooked. Whether it can take a trampoline on 2.7.2.0 is unmeasured; see the header. Calling it
  // needs no trampoline.
  bindingPressed: 0x75ebf0,
  // `FUN_14075ec70(pad, lambda, ...)`, the function `FUN_140759e30` forwards to. The pad is `rcx`
  // and the binding id is the dword at `lambda + 8` -- `FUN_140759e30` builds that lambda as
  // `local_48 = CONCAT44(uStackX_1c, param_2) & 0xffffff00ffffffff`, so the id it was asked for
  // travels inside it.
  //
  // This is the capture site: the pad is taken where the game passes it, which needs no
  // assumption about a caller's stack layout. It also reports the ids the menu system asks for by
  // itself, which is the answer outright if any menu polls cancel while the popup is up.
  bindingForward: 0x75ec70,
  // The boolean half, `FUN_140759e30(pad, bindingId)`. Called, never hooked.
  bindingBoolean: 0x759e30,
  // The bounds popup's step-2 handler: runs once a frame while the dialog is up, on the menu
  // thread, and is where the sweep is driven from.
  startInvasion: 0x7c3100,
};

// The namespace the known ids sit in. `0x2c`/`0x2d` are list down/up, `0x3d` confirm, `0x30`/`0x31`
// tab; the sweep covers all of it rather than a neighbourhood of confirm, because "cancel is
// probably next to confirm" is the kind of guess this file exists to stop making.
const BINDING_ID_MAX = 0x60;

// Known ids, named in the output so an unknown one is obvious at a glance.
const KNOWN = {
  0x01: 'popup-accept?',
  0x2c: 'list-down',
  0x2d: 'list-up',
  0x30: 'tab-left',
  0x31: 'tab-right',
  0x3d: 'confirm',
};

const base = Process.getModuleByName('eldenring.exe').base;

function follow (address) {
  try {
    return address.readU8() === 0xe9
      ? address.add(5).add(address.add(1).readS32())
      : address;
  } catch (e) {
    return address;
  }
}

const bindingEntry = follow(base.add(RVA.bindingPressed));
const bindingPressed = new NativeFunction(bindingEntry, 'uint32', ['pointer', 'uint32']);

// The keyboard, read the same way the product reads it, so the log can say "you were holding this
// key and the game called it this binding" in one line.
const getAsyncKeyState = (function () {
  const found = Process.findModuleByName('user32.dll');
  if (found === null) return null;
  const address = found.findExportByName('GetAsyncKeyState');
  return address === null ? null : new NativeFunction(address, 'int16', ['int']);
})();

let pad = null;
let padSaid = false;
let last = null;
let sweeps = 0;

// Capture the pad where the game passes it, and record every id the menu system asks about.
const asked = {};

Interceptor.attach(follow(base.add(RVA.bindingForward)), {
  onEnter: function (args) {
    const candidate = args[0];
    // A heap pointer, not the small integer the first capture attempt produced.
    if (candidate.compare(ptr('0x10000')) < 0) return;
    pad = candidate;
    try {
      asked['0x' + (args[1].add(8).readU32() & 0xff).toString(16)] = true;
    } catch (e) {
      // A lambda laid out differently is not worth a fault; the sweep is the real answer.
    }
    if (!padSaid) {
      padSaid = true;
      send({ tag: 'pad-captured', pad: pad.toString(), note: 'a live CSEzMenuViewerPad taken from rcx; the sweep can now ask the game about any binding id' });
    }
  },
});

Interceptor.attach(follow(base.add(RVA.startInvasion)), {
  onEnter: function () {
    if (pad === null) return;
    sweeps++;
    const hot = [];
    for (let id = 0; id <= BINDING_ID_MAX; id++) {
      let answer = 0;
      try {
        answer = bindingPressed(pad, id);
      } catch (e) {
        continue;
      }
      if (answer !== 0) {
        hot.push('0x' + id.toString(16) + (KNOWN[id] === undefined ? '' : '(' + KNOWN[id] + ')'));
      }
    }
    const keys = [];
    if (getAsyncKeyState !== null) {
      for (let vk = 1; vk <= 0xff; vk++) {
        if ((getAsyncKeyState(vk) & 0x8000) !== 0) keys.push('0x' + vk.toString(16));
      }
    }
    const now = JSON.stringify({ hot, keys });
    if (now === last) return;
    last = now;
    send({
      tag: 'binding-sweep',
      bindings: hot.length === 0 ? 'none' : hot.join(','),
      keys: keys.length === 0 ? 'none' : keys.join(','),
      pad: pad.toString(),
      askedByTheGame: Object.keys(asked).join(',') || 'none',
      sweeps,
      note: 'Bindings the game itself reports as pressed, asked through FUN_14075ebf0 which consults GLOBAL_CSPcKeyConfig. The id that lights up beside your cancel key is the rebind-safe constant the product should carry.',
    });
  },
});

send({
  tag: 'armed',
  bindingPressed: bindingEntry.toString(),
  stubbed: !bindingEntry.equals(base.add(RVA.bindingPressed)),
  note: 'Raise the bounds popup with a finger and press whatever you use to back out. Lines are emitted on change only.',
});
