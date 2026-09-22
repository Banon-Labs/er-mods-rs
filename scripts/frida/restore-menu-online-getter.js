// Restore `Menu_IsEnableOnlineMode` to its vanilla bytes on a LIVE game, and leave
// `GameMan::IsOnlineMode` alone.
//
// er_quickload's `apply_online_disable` writes `xor eax,eax; ret` over both getters at DLL
// attach, and the restore that used to undo them was deleted (user directive 2026-09-17), so
// both stay stubbed for the whole process. This agent lifts exactly one of them, as a reversible
// in-process test of whether the extra equipment-grid slot is gated on that getter.
//
// Only the menu-display getter is touched. `IsOnlineMode` is the one that can make a modded
// client answer official FromSoftware matchmaking, which is the reason its restore was removed;
// nothing here writes to it, and its bytes are only READ so the log can say what they are.
//
// rvas are 1.17.1 (the installed build). `Menu_IsEnableOnlineMode` 1.16.2 0xe56310 -> 1.17
// 0xe58180, which is what the DLL's own `ADDRESS TRANSLATED` log line reports.

const MENU_ONLINE_GETTER_RVA = 0xe58180;
const ONLINE_MODE_GETTER_RVA = 0x67ae80;

// `push rbx; sub rsp,0x70` -- read out of eldenring-deobf-1.17.1.bin at the rva above.
const VANILLA_FIRST_THREE = [0x40, 0x53, 0x48];
// What the DLL writes over them: `xor eax,eax; ret`.
const STUB = [0x31, 0xc0, 0xc3];

function hex(ptr, n) {
  return Array.from(new Uint8Array(ptr.readByteArray(n)))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join(' ');
}

function sameAs(ptr, bytes) {
  const live = new Uint8Array(ptr.readByteArray(bytes.length));
  return bytes.every((b, i) => live[i] === b);
}

function main() {
  const game = Process.enumerateModules().find((m) => m.name.toLowerCase() === 'eldenring.exe');
  if (!game) {
    send({ tag: 'menu-online-restore', error: 'eldenring.exe not in the module list' });
    return;
  }

  const menuGetter = game.base.add(MENU_ONLINE_GETTER_RVA);
  const onlineGetter = game.base.add(ONLINE_MODE_GETTER_RVA);

  const before = hex(menuGetter, 3);
  const onlineBytes = hex(onlineGetter, 3);

  if (!sameAs(menuGetter, STUB)) {
    // Refuse rather than write: either the DLL never patched it, or this is not the function.
    send({
      tag: 'menu-online-restore',
      action: 'refused',
      why: 'live bytes are not the xor eax,eax;ret stub',
      address: menuGetter.toString(),
      before,
      online_mode_getter: onlineBytes,
    });
    return;
  }

  Memory.protect(menuGetter, 3, 'rwx');
  menuGetter.writeByteArray(VANILLA_FIRST_THREE);

  send({
    tag: 'menu-online-restore',
    action: 'restored',
    address: menuGetter.toString(),
    before,
    after: hex(menuGetter, 3),
    // Read only. This one stays stubbed on purpose.
    online_mode_getter: { address: onlineGetter.toString(), bytes: onlineBytes },
  });
}

main();

// Put the stub back when the agent unloads, so detaching does not leave the game in a state
// neither the DLL nor vanilla chose.
rpc.exports = {};
globalThis.dispose = function () {
  const game = Process.enumerateModules().find((m) => m.name.toLowerCase() === 'eldenring.exe');
  if (!game) {
    return;
  }
  const menuGetter = game.base.add(MENU_ONLINE_GETTER_RVA);
  if (sameAs(menuGetter, VANILLA_FIRST_THREE)) {
    Memory.protect(menuGetter, 3, 'rwx');
    menuGetter.writeByteArray(STUB);
  }
};
