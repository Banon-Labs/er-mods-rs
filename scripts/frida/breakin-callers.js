// Who actually calls `CanUseBreakInItem` on 1.17, by return address rather than by signature.
//
// Two mapped functions have now been hooked and logged nothing while the behaviour they decide was
// visibly happening on screen: `CanUseGoods` (0x68ee60) and the goods popup dispatcher
// (0x7c3930). Both mappings were "unique signature" matches with prologues byte-identical to
// 1.16.2, so the addresses are real code -- they are simply not the copy this build calls.
//
// `CanUseBreakInItem` at 0x657d50 does fire. Its return address names its true caller, which is
// the measurement a signature match cannot give.
const CAN_USE_BREAK_IN_RVA = 0x657d50;

const game = Process.findModuleByName('eldenring.exe');
const callers = new Set();

Interceptor.attach(game.base.add(CAN_USE_BREAK_IN_RVA), {
  onEnter() {
    const ret = this.returnAddress;
    const rva = ret.sub(game.base);
    const key = rva.toString();
    if (callers.has(key)) {
      return;
    }
    callers.add(key);
    const owner = Process.findModuleByAddress(ret);
    console.log(
      `breakin-callers: returns to ${ret} rva 0x${rva.toString(16)} in ${owner ? owner.name : 'unknown'}`
    );
  },
});

console.log(`breakin-callers: hooked CanUseBreakInItem @${game.base.add(CAN_USE_BREAK_IN_RVA)}`);

// Integrity control. A silent hook is only evidence about its address if hooks in this attach fire
// at all -- otherwise "nothing called it" and "nothing was hooked" look identical, which is the
// mistake that already killed two theories in this session. `PeekMessageW` is pumped every frame
// by the game's message loop, so one line from it proves the Interceptor is live.
let peek = null;
try {
  peek = Module.getGlobalExportByName('PeekMessageW');
  console.log(`breakin-callers: control export resolved @${peek}`);
} catch (e) {
  console.log(`breakin-callers: control export failed: ${e.message}`);
}
let pumped = 0;
if (peek !== null) Interceptor.attach(peek, {
  onEnter() {
    pumped += 1;
    if (pumped === 1 || pumped === 600) {
      console.log(`breakin-callers: control -- PeekMessageW fired ${pumped}x, hooks are live`);
    }
  },
});

// `CanUseGoods` fires -- the return address above lands inside it -- so the earlier silence was a
// filter bug, not a wrong address: that agent only printed when `args[0]` equalled a finger's
// goods id, and Ghidra warns "unknown calling convention, parameter storage is locked" for this
// function, so the id need not be in rcx. Print the first four integer registers unfiltered once
// per distinct rcx and let the data say which one carries it.
const CAN_USE_GOODS_RVA = 0x68ee60;
const FINGERS = [102, 111, 112];
const argShapes = new Set();
console.log(`breakin-callers: attaching CanUseGoods @${game.base.add(CAN_USE_GOODS_RVA)}`);
Interceptor.attach(game.base.add(CAN_USE_GOODS_RVA), {
  onEnter(args) {
    const raw = [0, 1, 2, 3].map((i) => args[i].toUInt32());
    const key = raw[0].toString();
    if (argShapes.has(key) || argShapes.size > 40) {
      return;
    }
    argShapes.add(key);
    const hit = raw.findIndex((v) => FINGERS.includes(v & 0x0fffffff));
    console.log(
      `breakin-callers: CanUseGoods args=[${raw.map((v) => '0x' + v.toString(16)).join(', ')}]` +
        (hit >= 0 ? `  <- finger id in arg${hit}` : '')
    );
  },
});

// The goods popup, hooked from inside this agent rather than its own.
//
// `FUN_1407c2ab0` switches on the item's `opmeMenuType` and raises one of two prompts through
// `FUN_1407c2490(ctrl, a, b, messageId, 4, kind)`: 0x1312d0a (20000010) `Select the bounds for the
// attempt.` when no search is active, or 0x1312d0b (20000011) `Cancel invasion of other world?`
// when one is. The buttons are 20000015 `Nearby only` and 20000016 `Both near and far`.
//
// These were hooked once in a separate agent and logged nothing, but that attach was never shown
// to be live, so the silence proved nothing. Here they sit beside hooks that demonstrably fire.
const POPUP_DISPATCH_RVA = 0x7c3930;
const POPUP_RAISE_RVA = 0x7c3310;
const MESSAGES = {
  0x1312d0a: 'bounds prompt',
  0x1312d0b: 'cancel prompt',
  0x1312d0f: 'Nearby only',
  0x1312d10: 'Both near and far',
};
for (const [name, rva] of [['dispatch', POPUP_DISPATCH_RVA], ['raise', POPUP_RAISE_RVA]]) {
  const at = game.base.add(rva);
  console.log(`breakin-callers: popup ${name} @${at}`);
  Interceptor.attach(at, {
    onEnter(args) {
      if (name === 'dispatch') {
        console.log(`breakin-callers: popup dispatch ctrl=${args[0]}`);
        return;
      }
      const message = args[3].toUInt32();
      console.log(
        `breakin-callers: popup raise message=${message} (0x${message.toString(16)}) ` +
          `kind=${args[5].toUInt32()}  ${MESSAGES[message] || 'other'}`
      );
    },
  });
}

// `CS::CSMenuMan::OpenConversationChoicesMenu`, which is what actually serves these popups.
//
// The goods dispatcher above does NOT fire when a finger is used, measured in this same attach
// while the control probe and `CanUseGoods` were both logging -- and its address is right: the
// 1.17.1 image references the bounds message id 0x1312d0a at 0x1407c40f7, exactly 0x7c7 into
// 0x1407c3930, the same body offset it sits at in 1.16.2. So the popup the player sees is not the
// goods path's. It is a conversation-choices menu, which is the surface `lynchpin_use` already
// detours for Seamless's own dialogs.
//
// 1.16.2 0x140e9e4f0 -> 1.17.0 0x140ea02f0 (unique 49B signature) -> 1.17.1 0x140ea0360, the
// `+0x70` for being at or above rva 0xafefe9. Prologue verified identical in both images.
const OPEN_CHOICES_RVA = 0xea0360;
const choicesAt = game.base.add(OPEN_CHOICES_RVA);
console.log(`breakin-callers: choices menu @${choicesAt}`);
let choicesSeen = 0;
Interceptor.attach(choicesAt, {
  onEnter(args) {
    choicesSeen += 1;
    const ret = this.returnAddress;
    const owner = Process.findModuleByAddress(ret);
    const where = owner ? `${owner.name}+0x${ret.sub(owner.base).toString(16)}` : `${ret}`;
    console.log(
      `breakin-callers: choices menu #${choicesSeen} dialog=${args[0]} called from ${where}`
    );
  },
});

// Where does the chosen button land?
//
// Confirmed live: the goods dispatcher DOES raise this popup -- three calls logged with
// `message=20000010 kind=4` -- so it is the vanilla game's own path, not ersc.dll's. What is still
// unknown is which field of `CSPlayerMenuCtrl` receives the answer. `CSPlayerMenuCtrl` has almost
// no named methods in the dump, so this measures rather than reads: snapshot the object when the
// bounds prompt is raised, then diff it on the game's own message pump until something moves.
//
// Scope is deliberately one object and 0x80 bytes. A whole-module snapshot on a timer killed this
// game once (bd frida-whole-module-snapshot-timer-killed-the-game-2026-09-15), and a guard page is
// worse; a small read-only diff on the pump thread is neither.
const CTRL_WATCH_BYTES = 0x100;
let watchCtrl = null;
let watchBase = null;
let watchTicks = 0;

function snapshot(ptr) {
  return Array.from(new Uint8Array(ptr.readByteArray(CTRL_WATCH_BYTES)));
}

Interceptor.attach(game.base.add(POPUP_RAISE_RVA), {
  onEnter(args) {
    if (args[3].toUInt32() !== 0x1312d0a) {
      return;
    }
    watchCtrl = args[0];
    watchBase = snapshot(watchCtrl);
    watchTicks = 0;
    console.log(`breakin-callers: watching ctrl ${watchCtrl} for the chosen button`);
  },
});

if (peek !== null) {
  Interceptor.attach(peek, {
    onEnter() {
      if (watchCtrl === null || watchTicks > 4000) {
        return;
      }
      watchTicks += 1;
      const now = snapshot(watchCtrl);
      const moved = [];
      for (let i = 0; i < CTRL_WATCH_BYTES; i += 1) {
        // `+0xe4` counts on its own every couple of frames -- a timer, not a decision -- and its
        // carry moves `+0xe5` with it. Left in, it produced a line per frame and buried the
        // fields that do mean something.
        if (i >= 0xe0 && i < 0xe8) {
          continue;
        }
        if (now[i] !== watchBase[i]) {
          moved.push(`+0x${i.toString(16)}: ${watchBase[i]} -> ${now[i]}`);
        }
      }
      if (moved.length === 0 || moved.length > 16) {
        return;
      }
      console.log(`breakin-callers: ctrl moved after ${watchTicks} frames -- ${moved.join(', ')}`);
      // `+0x10` is the ctrl's own step -- the decompile assigns it 2 in one switch arm -- so a
      // change there is the popup advancing. Dump the whole object at that instant: the chosen
      // option has to be somewhere in it, and a `Nearby only` confirm against a `Both near and
      // far` confirm differ in exactly the field that carries it.
      if (moved.some((m) => m.startsWith('+0x10:'))) {
        const rows = [];
        for (let i = 0; i < CTRL_WATCH_BYTES; i += 16) {
          rows.push(
            `    +0x${i.toString(16).padStart(2, '0')}  ` +
              now.slice(i, i + 16).map((b) => b.toString(16).padStart(2, '0')).join(' ')
          );
        }
        console.log(`breakin-callers: ctrl step ${watchBase[0x10]} -> ${now[0x10]}\n${rows.join('\n')}`);
      }
      watchBase = now;
    },
  });
}

// The one function that starts the vanilla invasion for all three fingers.
//
// `FUN_1407c24f0` is the popup RESULT handler -- `CSPlayerMenuCtrl`'s step machine dispatches to it
// at step 2, which is the `2 -> 3` transition measured above. It reads the pressed button once,
// `uVar4 = FUN_1407c2390(ctrl, arg)`, then switches on the item's `opmeMenuType`. Every invasion
// finger converges on the same tail:
//
//   INVADE_BLOODY_FINGER    -> IsSearchActive_RedInvasionA
//   INVADE_WORLD_RECUSANT   -> IsSearchActive_RedIvasionB
//   INVADE_WORLD_FESTERING  -> IsSearchActive_RedInvasionALimited
//   ... if no search is active -> FUN_1407c1dd0(ctrl, choice, popup)
//
// So `FUN_1407c1dd0` is where the vanilla search begins and where the mod takes over: arg1 is the
// button, and declining to call the original is what keeps the vanilla matchmaking from running at
// all. Nothing downstream of it needs to be understood, because none of it is wanted.
//
// 1.16.2 0x1407c1dd0 -> 1.17 0x1407c2c50 is the WEAKER of the two mappings (nearest-anchor delta,
// 3 shape candidates), so the prologue is printed rather than assumed.
const INVADE_START_RVA = 0x7c2c50;
const CHOICE_GETTER_RVA = 0x7c3210;
const startAt = game.base.add(INVADE_START_RVA);
const startShape = Array.from(new Uint8Array(startAt.readByteArray(8)), (b) =>
  b.toString(16).padStart(2, '0')
).join(' ');
console.log(`breakin-callers: invade start @${startAt} opens ${startShape}`);
Interceptor.attach(startAt, {
  onEnter(args) {
    console.log(
      `breakin-callers: VANILLA INVASION START choice=${args[1].toUInt32() & 0xff} ` +
        `ctrl=${args[0]} popup=${args[2]}`
    );
  },
});
Interceptor.attach(game.base.add(CHOICE_GETTER_RVA), {
  onLeave(retval) {
    console.log(`breakin-callers: choice getter returned ${retval.toUInt32() & 0xff}`);
  },
});
