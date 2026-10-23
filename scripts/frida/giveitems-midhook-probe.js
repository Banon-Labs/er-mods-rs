// Prove the normal path of er-seamless-bugfixes' two GiveItems mid hooks (2026-10-19).
//
// Game addresses are 2.7.2.0. A literal that moved from 1.17.1 keeps its old value in its
// comment.
//
// Each ilhook jmp-ret hook moves `MOV RCX,[RCX+0x1e508]; MOV RAX,[RCX]; CALL [RAX+0x168]` into a
// trampoline and, with a local player present, runs them there and jumps back to the instruction
// after the 16-byte window. This agent hooks that instruction at both sites, so a hit means the
// trampoline ran the moved call and came back with a PlayerGameData in RAX. It also counts
// GiveItems entries, so a session with entries and no site hits simply never took either branch.
// Read-only: nothing is written to game memory or registers. Offsets are 1.17.1 (fn 0x561400).
'use strict';

const game = Process.getModuleByName('eldenring.exe');
const at = (rva) => game.base.add(rva);
const GIVE_ITEMS = 0x5618b0; // 1.17.1 0x561400
const STORAGE_AFTER_WINDOW = GIVE_ITEMS + 0x1d9 + 16; // MOV R15,RAX
const PICKUP_LOG_AFTER_WINDOW = GIVE_ITEMS + 0x34a + 16; // MOV RBX,RAX

const counts = { entries: 0, storage: 0, pickupLog: 0 };

Interceptor.attach(at(GIVE_ITEMS), {
    onEnter(args) {
        counts.entries += 1;
        send({ kind: 'give_items_entry', count: args[1].readU32(), counts });
    },
});

for (const [name, rva] of [['storage', STORAGE_AFTER_WINDOW], ['pickupLog', PICKUP_LOG_AFTER_WINDOW]]) {
    Interceptor.attach(at(rva), {
        onEnter() {
            counts[name] += 1;
            send({ kind: 'after_window', site: name, player_game_data: this.context.rax.toString(), counts });
        },
    });
}

send({ kind: 'armed', give_items: at(GIVE_ITEMS).toString(), counts });
