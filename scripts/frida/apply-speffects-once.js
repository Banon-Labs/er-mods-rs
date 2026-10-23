// One-shot: apply the SpEffects in IDS to the main player on one frame, for testing a duel
// referee against effects nobody here can produce by hand. Applied from the game's own frame
// tick so the call runs on the game thread; the hook does nothing after that and goes when the
// script is unloaded.
//
// Game addresses are 2.7.2.0. A literal that moved from 1.17.1 keeps its old value in its
// comment.
//
//   3160  [Item] Fire Grease - Right, spCategory 162, 60 s: on Unarmed (isEnhance 0) it is the
//         end state of a buff transfer.
//   3505  Crimson Crystal Tear's heal, duration 0: gone before any reader samples the list, so
//         only a referee that sees the apply itself can report it.
'use strict';

const IDS = [3160, 3505];

const game = Process.getModuleByName('eldenring.exe');
const at = (rva) => game.base.add(rva);
const WORLD_CHR_MAN = 0x3d6d018; // 1.17.1 0x3d69ff8
const MAIN_PLAYER = 0x1e508;
const FRAME_TICK = 0x773db0; // 1.17.1 0x773900
/// `CS::ChrIns::ApplySpEffect(chr, uint id, bool shouldNotSync) -> bool`.
const applySpEffect = new NativeFunction(at(0x3e8dc0), 'bool', ['pointer', 'uint32', 'bool']);

const out = new File(game.path.replace(/[^\\/]*$/, '') + 'apply-speffects-once.log', 'a');
const log = (line) => {
    console.log(line);
    out.write(`[${new Date().toISOString()}] ${line}\n`);
    out.flush();
};

let done = false;
Interceptor.attach(at(FRAME_TICK), {
    onEnter() {
        if (done) return;
        try {
            const wcm = at(WORLD_CHR_MAN).readPointer();
            if (wcm.isNull()) return;
            const player = wcm.add(MAIN_PLAYER).readPointer();
            if (player.isNull()) return;
            done = true;
            for (const id of IDS) log(`apply-speffects-once: ApplySpEffect(${player}, ${id}) -> ${applySpEffect(player, id, 1)}`);
        } catch (e) {
            log(`apply-speffects-once: ${e}`);
        }
    },
});
log(`apply-speffects-once: armed for ${IDS.join(', ')}`);
