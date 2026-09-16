// Which of `CanUseBreakInItem`'s three tests says no?
//
// `IsOnlineMode` was never called once while the inventory was open, so the online theory is dead.
// The real predicate is named: `CS::PlayerIns::CanUseBreakInItem`, and its whole body is
//
//   if (IsInSafePosRange(p))
//     if (WorldChrManImp::CanStartBreakIn(GLOBAL_WorldChrMan))
//       return IsBreakInLimitedByEventFlagId(&p->playRegionId);
//   return false;
//
// so exactly one of three named calls is the gate.
//
// # Why this asks the function instead of waiting for it
//
// The first version only attached and waited. It printed nothing while the user hovered a
// Festering Bloody Finger with the use row greyed, and the conclusion drawn from that -- "nothing
// calls it" -- was not supported: the trampolines went in while the player was already standing in
// the world, so a call made during world load happened before the hook existed and a result cached
// into the menu's state is indistinguishable from a function nobody calls.
//
// So the agent now does both. The passive hooks stay, and they count every call rather than
// reporting each distinct answer once, which is what makes a single load-time call visible. On top
// of that the predicates are invoked directly with the live `PlayerIns`, which answers the same
// question in the current frame with no reload.
//
// The direct call is issued from inside a `PeekMessageW` hook. The game pumps its message queue on
// its main thread every frame, so that callback runs on the thread that owns the player and the
// menu, rather than on Frida's JS thread -- these predicates read singletons and event flags, and
// a wrong thread is the difference between a read and a race. The hook detaches itself after one
// shot for the same reason a monitor is not left armed.
//
// Addresses are 1.16.2 -> 1.17 candidates from scripts/map-rvas-1162-to-1170.py, all below rva
// 0xafefe9 so the 1.17.0 -> 1.17.1 `+0x70` does not apply to any of them. Each prologue is read
// back and printed, which is what rules out a candidate that landed mid-instruction.
const SITES = [
  { name: 'CanUseBreakInItem', rva: 0x657d50, confidence: 'unique 38B signature' },
  { name: 'IsInSafePosRange', rva: 0x65f6a0, confidence: '2 shape candidates' },
  { name: 'CanStartBreakIn', rva: 0x50a950, confidence: 'WEAK -- 9 shape candidates' },
];

// `WorldChrMan` singleton, and the local `PlayerIns` inside it. Both from
// crates/er-game-base/src/rva.rs, where both dereferences are null-checked because the game's own
// code null-checks both.
const WORLD_CHR_MAN_GLOBAL_RVA = 0x3d65f88;
const WORLD_CHR_MAN_PLAYER_INS_OFFSET = 0x1e508;

const game = Process.findModuleByName('eldenring.exe');
const calls = new Map();

function shapeOf(at) {
  const head = new Uint8Array(at.readByteArray(8));
  return Array.from(head, (b) => b.toString(16).padStart(2, '0')).join(' ');
}

for (const site of SITES) {
  site.at = game.base.add(site.rva);
  console.log(`breakin-gate: ${site.name} @${site.at} opens ${shapeOf(site.at)}  (${site.confidence})`);
  Interceptor.attach(site.at, {
    onLeave(retval) {
      // `al` is the whole answer for a bool-returning leaf. Counted per answer rather than
      // reported once, so one call at world load is still legible against a per-frame caller.
      const answer = retval.toInt32() & 0xff;
      const key = `${site.name}=${answer}`;
      const n = (calls.get(key) || 0) + 1;
      calls.set(key, n);
      if (n === 1 || n === 10 || n % 500 === 0) {
        console.log(`breakin-gate: ${site.name} returned ${answer}  (call #${n})`);
      }
    },
  });
}

function livePlayer() {
  const worldChrMan = game.base.add(WORLD_CHR_MAN_GLOBAL_RVA).readPointer();
  if (worldChrMan.isNull()) {
    return null;
  }
  const player = worldChrMan.add(WORLD_CHR_MAN_PLAYER_INS_OFFSET).readPointer();
  return player.isNull() ? null : player;
}

function askDirectly() {
  const player = livePlayer();
  if (player === null) {
    console.log('breakin-gate: no live player yet -- not calling anything');
    return;
  }
  console.log(`breakin-gate: local PlayerIns @${player}`);

  const outer = new NativeFunction(SITES[0].at, 'bool', ['pointer']);
  const safePos = new NativeFunction(SITES[1].at, 'bool', ['pointer']);
  try {
    console.log(`breakin-gate: DIRECT CanUseBreakInItem(player) = ${outer(player)}`);
  } catch (e) {
    console.log(`breakin-gate: DIRECT CanUseBreakInItem threw ${e.message}`);
  }
  try {
    console.log(`breakin-gate: DIRECT IsInSafePosRange(player) = ${safePos(player)}`);
  } catch (e) {
    console.log(`breakin-gate: DIRECT IsInSafePosRange threw ${e.message}`);
  }
  // `CanStartBreakIn` takes the singleton, not the player -- it is the one predicate of the three
  // whose argument is `GLOBAL_WorldChrMan`, and it is also the weakest-mapped, so a throw here is
  // evidence the address is wrong rather than evidence about the gate.
  const worldChrMan = game.base.add(WORLD_CHR_MAN_GLOBAL_RVA).readPointer();
  const canStart = new NativeFunction(SITES[2].at, 'bool', ['pointer']);
  try {
    console.log(`breakin-gate: DIRECT CanStartBreakIn(worldChrMan) = ${canStart(worldChrMan)}`);
  } catch (e) {
    console.log(`breakin-gate: DIRECT CanStartBreakIn threw ${e.message}`);
  }
}

// One shot, on the game's own main thread, then the trampoline comes back out.
const peek = Module.getExportByName('user32.dll', 'PeekMessageW');
let fired = false;
const pump = Interceptor.attach(peek, {
  onEnter() {
    if (fired) {
      return;
    }
    fired = true;
    try {
      askDirectly();
    } catch (e) {
      console.log(`breakin-gate: direct call failed: ${e.message}`);
    }
    // No detach. Detaching from inside the callback is not safe, and scheduling it off the hook
    // needs a timer, which this repo bans for good reason -- a timer is a guess at readiness. The
    // `fired` latch above already makes every later call a single compare-and-return, and the
    // agent is replaced wholesale by the next hot reload.
  },
});

console.log('breakin-gate: hooked -- asking the predicates directly on the next frame');
