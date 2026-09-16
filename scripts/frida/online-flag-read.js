// What does the game actually think its online state is?
//
// Patching two predicates changed nothing on screen, so the next question is whether the FLAG they
// read is itself zero -- a write, not a stub -- which is a different mechanism and a different fix.
//
// The global is resolved from the getter's own bytes rather than from a pinned address:
//   0x67ae80:  48 8b 05 <disp32>      mov rax, [rip+disp32]     <- the GameMan pointer
//              0f b6 80 c8 0b 00 00   movzx eax, [rax+0xbc8]    <- the flag
//              c3
// so if the prologue is not that shape, this reports it instead of reading a wrong address.
const GAME_MAN_GETTER_RVA = 0x67ae80;
const ONLINE_FLAG_OFFSET = 0xbc8;

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
  console.log('online-flag: eldenring.exe is not loaded');
} else {
  const getter = game.base.add(GAME_MAN_GETTER_RVA);
  const head = new Uint8Array(getter.readByteArray(14));
  const shape = Array.from(head, (b) => b.toString(16).padStart(2, '0')).join(' ');
  if (head[0] !== 0x48 || head[1] !== 0x8b || head[2] !== 0x05) {
    console.log(`online-flag: getter @${getter} opens ${shape} -- not the mov rax,[rip] shape, REFUSING to guess the global`);
  } else {
    const disp = getter.add(3).readS32();
    const globalAt = getter.add(7).add(disp);
    const gameMan = globalAt.readPointer();
    console.log(`online-flag: getter @${getter} = ${shape}`);
    console.log(`online-flag: GameMan* held at ${globalAt} -> ${gameMan}`);
    if (gameMan.isNull()) {
      console.log('online-flag: GameMan is null -- nothing to read yet');
    } else {
      const flag = gameMan.add(ONLINE_FLAG_OFFSET).readU8();
      console.log(`online-flag: GameMan+0x${ONLINE_FLAG_OFFSET.toString(16)} = ${flag}  (1 = online, 0 = offline)`);
      const around = new Uint8Array(gameMan.add(ONLINE_FLAG_OFFSET - 8).readByteArray(24));
      console.log('online-flag: bytes around it: ' + Array.from(around, (b) => b.toString(16).padStart(2, '0')).join(' '));
    }
  }
}
