// Let the local player summon Spirit Ashes anywhere, so the duel-rules referee's Spirit Ashes rule
// can be seen live on a character with no Rebirth Monument in reach.
//
// Game addresses carried to 2.7.2.0 by scripts/frida-carry-1172.py; each moved literal keeps its
// old value in its comment.
//
// What vanilla needs, read out of the 1.16.2 dump (`CS::SummonBuddyManager`):
//
// - `SummonGoodsState(mgr, goodsId)` answers `Use` only when the player carries a SpEffect with
//   stateInfo 373 and `buddyStoneTalkChrEntityId` (+0x38) is non-zero and differs from
//   `activeSummonBuddyStoneEntityId` (+0x3c). `CanUseGoods` greys the ash out otherwise. A monument's
//   talk script supplies both: it keeps SpEffect 9530 (stateInfo 373, 0.06 s) on the player and
//   calls `SetBuddySpawnPoint` with its own entity id (talk command 0x7a).
// - `BuddyGenerator` spawns nothing unless the active stone id resolves to a `BuddyStoneParam` row,
//   and it sets that row's `summonedEventFlagId`. Row 99020102 (talkChrEntityId 99020120, the m99
//   test map) is the only row with a talk entity and a zero flag, so it writes no save flag.
// - `CheckActivateRanges` clears `isWithinActivateRange` (+0xb5) every frame and sets it only by
//   distance to the active stone, and the per-frame update despawns every summon when neither it nor
//   the warn range (+0xb7) is set. The m99 stone is never loaded, so the flag is put back after it.
//
// Noble Sorcerer Ashes (BuddyParam 24100000) has `appearOnAroundSekihi` 0, so it spawns beside the
// player through `FUN_1404b9950` and never needs the stone's position.
//
// Addresses are 1.17.1's: each 1.16.2 function was mapped by `scripts/map-rvas-1162-to-1170.py` and
// read back out of `eldenring-deobf-1.17.1.bin` (the 0x175 and +0x38/+0x3c compares, the +0xb5..+0xb8
// stores, the same 72-instruction body). All are below the `0xafefe9` boundary.
'use strict';

const GAME = 'eldenring.exe';

const WORLD_CHR_MAN_GLOBAL_RVA = 0x3d6d018; // 1.17.1 0x3d69ff8
const WCM_MAIN_PLAYER = 0x1e508;

/// `CS::SummonBuddyManager::SummonGoodsState(mgr, int goodsId) -> BuddyGoodsState`.
const SUMMON_GOODS_STATE_RVA = 0x4b73d0; // 1.17.1 0x4b72f0
/// `CS::SummonBuddyManager::CheckActivateRanges(mgr)`.
const CHECK_ACTIVATE_RANGES_RVA = 0x4bd950; // 1.17.1 0x4bd870
/// `SummonBuddyManager::MainPlayerHasAliveSummon(mgr) -> bool`.
const MAIN_PLAYER_HAS_ALIVE_SUMMON_RVA = 0x4b7bf0; // 1.17.1 0x4b7b10
/// `CS::ChrIns::ApplySpEffect(chr, uint id, bool shouldNotSync) -> bool`.
const APPLY_SPEFFECT_RVA = 0x3e8dc0;

const REQUEST_SUMMON_SPEFFECT = 0x20;
const TALK_STONE_ENTITY = 0x38;
const ACTIVE_STONE_ENTITY = 0x3c;
const IN_ACTIVATE_RANGE = 0xb5;
const IN_WARN_RANGE = 0xb7;

const ENABLE_SPIRIT_ASHES_SPEFFECT = 9530;
const FAKE_STONE_ENTITY = 99020120;

const game = Process.findModuleByName(GAME);
const at = (rva) => game.base.add(rva);
const applySpEffect = new NativeFunction(at(APPLY_SPEFFECT_RVA), 'bool', ['pointer', 'uint32', 'bool']);
const hasAliveSummon = new NativeFunction(at(MAIN_PLAYER_HAS_ALIVE_SUMMON_RVA), 'bool', ['pointer']);

const seen = new Set();
function once(key, line) {
    if (seen.has(key)) return;
    seen.add(key);
    console.log(`summon-anywhere: ${line}`);
}

Interceptor.attach(at(SUMMON_GOODS_STATE_RVA), {
    onEnter(args) {
        const mgr = args[0];
        const talk = mgr.add(TALK_STONE_ENTITY).readU32();
        // A real monument's id is left alone; the fake only fills the gap where none is talking.
        if (talk !== 0 && talk !== FAKE_STONE_ENTITY) return;
        if (talk === 0) {
            mgr.add(TALK_STONE_ENTITY).writeU32(FAKE_STONE_ENTITY);
            once('talk', `buddyStoneTalkChrEntityId 0 -> ${FAKE_STONE_ENTITY} (mgr ${mgr})`);
        }
    },
});

// Vanilla allows one summon per stone; clearing the active id once the summon is gone lets the ash
// be used again without a rest. `MainPlayerHasAliveSummon` reads false for a spirit in its first
// frames (measured 2026-10-16: cleared in the frame it spawned, so the next range check despawned
// it), so the id is cleared only after the summon has read dead for this many frames in a row.
const GONE_FRAMES = 120;
let goneFrames = 0;
let wasAlive = null;
function releaseStoneWhenSummonGone(mgr) {
    if (mgr.add(ACTIVE_STONE_ENTITY).readU32() !== FAKE_STONE_ENTITY
        || mgr.add(REQUEST_SUMMON_SPEFFECT).readS32() >= 0) {
        goneFrames = 0;
        return;
    }
    const alive = !!hasAliveSummon(mgr);
    if (alive !== wasAlive) {
        console.log(`summon-anywhere: MainPlayerHasAliveSummon -> ${alive} at update ${ticks}`);
        wasAlive = alive;
    }
    goneFrames = alive ? 0 : goneFrames + 1;
    if (goneFrames < GONE_FRAMES) return;
    mgr.add(ACTIVE_STONE_ENTITY).writeU32(0);
    goneFrames = 0;
    wasAlive = null;
    console.log(`summon-anywhere: no live summon for ${GONE_FRAMES} frames; the ash can be used again`);
}

// `SummonBuddyManager::Update` runs this once a frame on the game thread while the main player
// exists, so it carries the per-frame work too. The game's frame tick is left alone:
// er-duel-rules.js checks that function's first bytes before hooking it, and a second agent's
// hook there made it refuse to arm (measured 2026-10-16).
let ticks = 0;
Interceptor.attach(at(CHECK_ACTIVATE_RANGES_RVA), {
    onEnter(args) { this.mgr = args[0]; },
    onLeave() {
        const mgr = this.mgr;
        ticks += 1;
        try {
            const player = at(WORLD_CHR_MAN_GLOBAL_RVA).readPointer().add(WCM_MAIN_PLAYER).readPointer();
            if (!player.isNull()) {
                applySpEffect(player, ENABLE_SPIRIT_ASHES_SPEFFECT, 1);
                once('speffect', `applying SpEffect ${ENABLE_SPIRIT_ASHES_SPEFFECT} (stateInfo 373) each frame; summon manager ${mgr}`);
            }
            releaseStoneWhenSummonGone(mgr);
        } catch (e) {
            once(`threw:${e.message}`, `THREW ${e.message}`);
        }
        if (mgr.add(ACTIVE_STONE_ENTITY).readU32() !== FAKE_STONE_ENTITY) return;
        mgr.add(IN_ACTIVATE_RANGE).writeU8(1);
        mgr.add(IN_WARN_RANGE).writeU8(0);
        once('range', `summon out on the fake stone; holding isWithinActivateRange on (mgr ${mgr})`);
    },
});

console.log('summon-anywhere: armed');
