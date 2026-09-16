// Write the online flag back to 1 and watch whether anything writes it down again.
//
// Restoring both getters changed nothing because neither was the problem: `GameMan+0xbc8` itself
// reads 0, so the getter faithfully returns the zero somebody stored. This does the direct thing --
// store 1 -- and then arms a hardware watchpoint on that byte so a rewrite names its own
// instruction instead of being guessed at.
//
// `Thread.setHardwareWatchpoint`, never `MemoryAccessMonitor`: the latter revokes access to a whole
// 4 KB page and killed this game at `ersc+0x89e23` on 2026-09-15. A watchpoint changes no
// protection and traps only the address asked for.
const GAME_MAN_GETTER_RVA = 0x67ae80;
const ONLINE_FLAG_OFFSET = 0xbc8;

const game = Process.findModuleByName('eldenring.exe');
const getter = game.base.add(GAME_MAN_GETTER_RVA);
const disp = getter.add(3).readS32();
const gameMan = getter.add(7).add(disp).readPointer();
const flagAt = gameMan.add(ONLINE_FLAG_OFFSET);

console.log(`online-force: GameMan ${gameMan}, flag at ${flagAt}, currently ${flagAt.readU8()}`);

Memory.protect(flagAt, 1, 'rw-');
flagAt.writeU8(1);
console.log(`online-force: wrote 1, reads back ${flagAt.readU8()}`);

// Name the writer if one comes back. Watchpoints are per thread, so every thread gets one.
let armed = 0;
for (const t of Process.enumerateThreads()) {
  try {
    Thread.setHardwareWatchpoint(t.id, flagAt, 1, 'w', function (details) {
      const pc = details.context.pc;
      const mod = Process.findModuleByAddress(pc);
      const where = mod ? `${mod.name}+0x${pc.sub(mod.base).toString(16)}` : `${pc}`;
      console.log(`online-force: WRITER ${where} set the flag to ${flagAt.readU8()}`);
    });
    armed += 1;
  } catch (e) {
    // A thread that will not take one is not a failure of the experiment; say how many did.
  }
}
console.log(`online-force: watchpoint armed on ${armed} thread(s) -- open the multiplayer menu now`);
