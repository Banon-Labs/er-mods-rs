// Does the item-greying decision even consult `IsOnlineMode`?
//
// Three patches changed nothing on screen: both online predicates restored, and the flag itself
// forced to 1 (which then reverted to 0 on its own). Each assumed the greying reads that value.
// This asks the function instead -- if the getter is never called while the inventory is open, the
// greying is decided elsewhere and every fix aimed here was aimed wrong.
//
// Read-only: an Interceptor that records its caller cannot change what the game does.
//
// Prints each caller the FIRST time it is seen, rather than buffering for a report. The previous
// revision collected into a Map and only emitted on a `recv`, which nothing sends -- so it observed
// correctly and said nothing, which is indistinguishable from observing nothing.
const GETTER_RVA = 0x67ae80;
const FLAG_OFFSET = 0xbc8;

const game = Process.findModuleByName('eldenring.exe');
const getter = game.base.add(GETTER_RVA);
const disp = getter.add(3).readS32();
const gameMan = getter.add(7).add(disp).readPointer();
const flagAt = gameMan.add(FLAG_OFFSET);

console.log(`getter-callers: flag at ${flagAt} reads ${flagAt.readU8()}`);

const seen = new Set();
let calls = 0;

Interceptor.attach(getter, {
  onEnter(_args) {
    calls += 1;
    const ret = this.returnAddress;
    const mod = Process.findModuleByAddress(ret);
    const who = mod ? `${mod.name}+0x${ret.sub(mod.base).toString(16)}` : `${ret}`;
    if (!seen.has(who)) {
      seen.add(who);
      // The call count rides along so a caller that fires once is distinguishable from the
      // per-frame ones without a second pass.
      console.log(`getter-callers: caller #${seen.size} ${who} (call ${calls}, flag ${flagAt.readU8()})`);
    }
  },
});

console.log('getter-callers: hooked, printing each new caller as it appears');
