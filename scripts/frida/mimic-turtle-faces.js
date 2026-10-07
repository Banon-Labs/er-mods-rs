// Live proof for er-npc-summons: give each Mimic Tear companion its build's face on the frame it is
// created, through CS::PlayerGameData::CopyFaceDataFromBuffer (1.17.1 0x140261010), the call
// spawn-npc.js applyFace and er-build-import-runtime face.rs use. Before the DLL does it in Rust.
//
// The faces are turtles.lua's three blobs (the game's 288-byte face buffer, magic FACE, version
// 4); they differ only in the blindfold colour. Companions are created in slot order, one
// CreateSummonChr per companion, so the Nth Moongrum-body spawn of a batch gets face N.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/mimic-turtle-faces.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));

const CREATE_SUMMON_CHR = va('0x1404baea0');
const COPY_FACE = new NativeFunction(va('0x140261010'), 'void', ['pointer', 'pointer']);
const PLAYER_GAME_DATA = 0x580;
const FACE_BLOCK = 0x768;
const FACE_LEN = 288;
// CreateSummonChr's seventh argument is the NpcParam row.
const ARG_NPC = 6;
const TURTLE_NPC = 523590024;
// Calls further apart than this start a new batch.
const BATCH_GAP_MS = 1000;

const COMMON = '46414345040000002001000096000000000000000000000000000000000000000A000000000000000000000096BE00000073F38986339E57E90E1BFF06F1FFB3FFC34E0DFF00E53A0EDF00020000201231C0FB060DEB11F6FF5CF500F7E30904A41CD70A790005F600E3F7E4800000000080808080800000000000000000008080808000000000808080808080808080808080808080808080808080808080808080808080808080808080004E64325044504440791AFF00000A3A0D0D00FFFFFF005B3123000522220047301800F5DAD0808080FF808080800000E8E6DA1A0F05FF3C1A0F05FFFFFF8A1A0F05FF3C1A0F05FFFFFF8AE8E6DAFF0023E8E6DAFF0023B9B9B9FF0023';
const TAILS = [
    ['Raphael', 'E8E6DAAA1E1E000000000000000000000000000000000000'],
    ['Donatello', 'E8E6DA6E1EAA000000000000000000000000000000000000'],
    ['Michelangelo', 'E8E6DA47280A000000000000000000000000000000000000'],
];
const faces = TAILS.map(([name, tail]) => {
    const hex = COMMON + tail;
    const buf = Memory.alloc(FACE_LEN);
    buf.writeByteArray(hex.match(/../g).map((b) => parseInt(b, 16)));
    return { name, buf };
});

let lastAt = 0;
let index = 0;
const hook = Interceptor.attach(CREATE_SUMMON_CHR, {
    onEnter(args) {
        this.npc = args[ARG_NPC].toInt32();
    },
    onLeave(ret) {
        if (this.npc !== TURTLE_NPC || ret.isNull()) return;
        const now = Date.now();
        if (now - lastAt > BATCH_GAP_MS) index = 0;
        lastAt = now;
        const face = faces[index % faces.length];
        index += 1;
        try {
            const pgd = ret.add(PLAYER_GAME_DATA).readPointer();
            COPY_FACE(pgd, face.buf);
            const after = new Uint8Array(pgd.add(FACE_BLOCK).readByteArray(FACE_LEN));
            const want = new Uint8Array(face.buf.readByteArray(FACE_LEN));
            let differs = 0;
            for (let i = 0; i < FACE_LEN; i++) if (after[i] !== want[i]) differs++;
            send({ kind: 'turtle-face', name: face.name, chr: ret.toString(), stillDiffers: differs });
        } catch (e) {
            send({ kind: 'turtle-face-error', name: face.name, error: e.message });
        }
    },
});

send({ kind: 'armed', createSummonChr: CREATE_SUMMON_CHR.toString() });

rpc.exports.dispose = () => hook.detach();
