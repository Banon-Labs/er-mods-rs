// Log every animation the local player starts, so the duel referee's Light Roll rule can key on
// the roll animations rather than on equip load alone.
// Read-only: one hook on the game's frame tick, the same one er-duel-rules.js uses, reading the
// TimeAct ring that file reads (offsets from there, measured on 1.17.1).
//
// Game addresses carried to 2.7.2.0 by scripts/frida-carry-1172.py; each moved literal keeps its
// old value in its comment.
'use strict';

const game = Process.getModuleByName('eldenring.exe');
const at = (rva) => game.base.add(rva);
const WORLD_CHR_MAN = 0x3d6d018; // 1.17.1 0x3d69ff8
const MAIN_PLAYER = 0x1e508;
const CHR_MODULES = 0x190;
const MOD_TIME_ACT = 0x18;
const QUEUE = 0x20;
const ENTRY = 0x10;
const COUNT = 10;
const PLAY_TIME = 0x4;
const WRITE_IDX = 0xc0;
const FRAME_TICK = 0x773db0; // 1.17.1 0x773900

// The watcher buffers console output, so every start also goes straight to a file beside the game.
const out = new File(game.path.replace(/[^\\/]*$/, '') + 'anim-starts-probe.log', 'a');
const log = (line) => {
    console.log(line);
    out.write(`[${new Date().toISOString()}] ${line}\n`);
    out.flush();
};

let last = null;
Interceptor.attach(at(FRAME_TICK), {
    onEnter() {
        try {
            const player = at(WORLD_CHR_MAN).readPointer().add(MAIN_PLAYER).readPointer();
            if (player.isNull()) return;
            const timeAct = player.add(CHR_MODULES).readPointer().add(MOD_TIME_ACT).readPointer();
            const writeIdx = timeAct.add(WRITE_IDX).readS32();
            if (writeIdx < 0 || writeIdx >= COUNT) return;
            const history = [];
            for (let k = 0; k < COUNT; k++) {
                const e = timeAct.add(QUEUE + k * ENTRY);
                history.push([e.readS32(), e.add(PLAY_TIME).readFloat()]);
            }
            const [anim, playTime] = history[(writeIdx + COUNT - 1) % COUNT];
            if (last !== null) {
                let frames = (writeIdx + COUNT - last.writeIdx) % COUNT;
                if (frames === 0 && (anim !== last.anim || playTime !== last.playTime)) frames = COUNT;
                let [pa, pt] = [last.anim, last.playTime];
                for (let k = 0; k < frames; k++) {
                    const [a, t] = history[(last.writeIdx + k) % COUNT];
                    if (a > 0 && (a !== pa || t < pt)) log(`anim-start ${a}`);
                    [pa, pt] = [a, t];
                }
            }
            last = { writeIdx, anim, playTime };
        } catch (e) {
            // A character torn down under the read; the next frame reads again.
        }
    },
});
log('anim-starts-probe: armed');
