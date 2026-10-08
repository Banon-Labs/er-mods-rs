// Spawn an AI-driven human in front of the player through the game's own spirit-summon path,
// SummonBuddyManager::CreateSummonChr -- the routine that creates Spirit Ash humans (c0000 plus a
// CharaInitParam row). Default: Moongrum, Carian Knight, exactly as his map entry places him
// (m14_00_00_00 part c0000_9014: NPCParamID 523590024, ThinkParamID 523590100, CharaInitID 23590).
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/spawn-npc.js \
//     --config-json '{"npcParam": 523590024, "think": 523590100, "charaInit": 23590, "distance": 8}'
//
// One live spawn at a time. The spawned ChrIns address is kept in the process environment
// (ER_FRIDA_SPAWN_NPC), which outlives a reload of this agent, so saving this file again does not
// spawn a second one while the first is alive. Measured 2026-10-05: before this guard, every save
// spawned another, and three of them killed the level-9 test character.
//
// Why not SpawnDynamicChr (0x140507d00), which this agent used first. Its only retail caller,
// CSTalkDynamicChrCtrl, always passes charaInitParam -1, so the game never builds a human through
// it. Driven with a CharaInitParam it did build a moving PlayerIns once, but later spawns were freed
// within seconds and the next one crashed the renderer 0.3 s after creation (game+0xb7256d, model
// AABB exporter on a freed character; bd human-spawn-via-spawndynamicchr-crashes-render-uaf-1171-
// measured-2026-10-05). CreateSummonChr is the path the game does use for humans, and it does what
// the dynamic path skips: a real DLInplaceStr model name, eventEntityId 35000, ChrSet::SpawnChr into
// the summon ChrSet slot, NetChrSync::SetupForEntityHandle, the creator steam id, chrSyncFlags |= 4,
// and registration in the manager's summon groups keyed by the owner's character event id, which
// is what RemoveSummonsByOwnerEventId tears down through RemoveChrIns.
//
// Addresses, 1.17.1, each read out of eldenring-deobf-1.17.1.bin (all below the 0xafefe9 shift
// boundary, so 1.17.0 == 1.17.1; names from the 1.16.2 dump on :8765):
//   SummonBuddyManager::CreateSummonChr 0x1404baea0 (1.16.2 0x1404ba980): its body loads FieldArea
//       [0x143d6d248]+0x18 and calls GetWorldBlockInfoByBlockId 0x14066ac40, then
//       GetBlockCenterInPhysicsSpace 0x140662320, as the 1.16.2 decompile does
//   WorldChrMan +0x1e538 summonBuddyManager       getter 0x140508960 `mov rax,[rcx+0x1e538]; ret`
//   ChrIns::GetBlockIdOrigin 0x1403f0490          +0x38, or +0x3c when that is -1
//   WorldBlockInfo +0x70 block centre              GetBlockCenterInPhysicsSpace 0x140662320
//   PlayerIns::CharacterEventId 0x140657230       [PlayerIns+0x580]+0x8 (PlayerGameData)
//   CSFeManImp::UpdatePlayerComponents 0x140773900, the main-thread HUD pass, used as the frame tick
//   WorldChrMan global 0x143d69ff8, main player at +0x1e508
//   ChrIns +0x190 modules -> +0x68 physics -> +0x70 position, +0x60 interpolated orientation (quat)
//
// Argument values copy the retail caller, SummonBuddyManager::BuddyGenerator (1.16.2 0x1404bbf9c):
// the owner's event id and steam id, the spawn block id, a FieldInsHandle of all 0xff / -1 (which
// selects ChrSet::SpawnChr), param_12 true, no mount, fromNetwork false. buddyStone/buddy param ids
// are -1: both lookups tolerate a missing row (they test the result for null).
//
// Position: with a known block, CreateSummonChr places the character at block centre + pos, so pos
// is passed block-relative.
//
// Not yet run against a live game.
'use strict';

const cfg = Object.assign({
    // Moonrithyll (her map entry m61_47_44_00 c0000_9003); Moongrum was 523590024 / 523590100 / 23590.
    npcParam: 524320082,
    think: 524320000,
    charaInit: 2024320,
    // Measured 2026-10-05: at 8 m the point ahead had no floor under it and the spawn fell from
    // y -22.9 to -67 in four seconds. Height is copied from the player, so closer is safer.
    distance: 3,
    // Her heal count at spawn and after every grace rest (see setHeals).
    heals: 13,
    // Remove a still-alive spawn from an earlier load of this agent (through the game's own
    // WorldChrManImp::RemoveChrIns) and spawn a fresh one, instead of adopting it.
    replace: true,
    // 'summon': SummonBuddyManager::CreateSummonChr plus the network announce, so Seamless peers
    // see her. 'dynamic': WorldChrManImp::SpawnDynamicChr, local only.
    path: 'summon',
    // Keep the main player from taking damage or dying (CSChrDataModule debug flags, below).
    god: false,
    // How many of her to summon. The first is `spawned`; the rest are `extras`, summoned one per
    // frame after it, EXTRA_SPACING m to the side, with the same gear, face and heals.
    count: 2,
}, globalThis.__ER_FRIDA_CONFIG || {});

// Which path, measured 2026-10-05 on one session each:
//   summon   The summon effect plays and the character is created, but it is a Spirit Ash to every
//            rule the game has. Its behaviour script calls RequestWarp every frame (400 requests in
//            10 s, all dropped by the hook below), and after 10 s the manager's disappear path removes
//            it anyway (RemoveChrIns from 1.16.2 FUN_1404b8d90+0x8ca). The same update despawns all
//            summons when the player is outside a summon area (requestSummonSpEffect < 0 and neither
//            isWithinWarnRange nor isWithinActivateRange), so the path is tied to Rebirth Monuments.
//   dynamic  The first in-world spawn walked up to the player and fought. Spawns during a load are
//            discarded with it, and spawns after the player's death were freed and then crashed the
//            renderer once; the 5 s stable gate and the one-live-spawn guard are aimed at those.
// Built on first use: this block sits above the `va` helper it needs.
let spawnDynamicChr = null;
const REQ = {
    SIZE: 0xc8, POSITION: 0x00, ORIENTATION: 0x10, SCALE: 0x20, UNK30: 0x30,
    NPC_PARAM: 0x40, THINK: 0x44, CHARA_INIT: 0x48, EVENT_ENTITY: 0x4c, TALK: 0x50,
    MODEL: 0x58, MODEL_BACKING: 0x08, MODEL_LEN: 0x10, MODEL_UNK18: 0x18, MODEL_CHAR_SIZE: 0x1c,
    MODEL_TYPE: 0x1e, MODEL_FLAGS: 0x1f, MODEL_BUFFER: 0x20,
};

// The request crates/er-npc-possess/src/spawn/request.rs builds, with charaInitParam >= 0 and model
// c0000 for a human (bd npc-possess-human-c0000-spawn-is-charainit-nonneg-1171-static-2026-10-05).
function spawnDynamic(pose) {
    const at = [
        pose.pos[0] - cfg.distance * Math.sin(pose.yaw),
        pose.pos[1],
        pose.pos[2] - cfg.distance * Math.cos(pose.yaw),
    ];
    const req = Memory.alloc(REQ.SIZE);
    const vec4 = (off, v) => v.forEach((c, i) => req.add(off + i * 4).writeFloat(c));
    vec4(REQ.POSITION, [at[0], at[1], at[2], 1]);
    vec4(REQ.ORIENTATION, [0, pose.yaw + Math.PI, 0, 0]);
    vec4(REQ.SCALE, [1, 1, 1, 1]);
    vec4(REQ.UNK30, [1, 1, 1, 1]);
    req.add(REQ.NPC_PARAM).writeS32(cfg.npcParam);
    req.add(REQ.THINK).writeS32(cfg.think);
    req.add(REQ.CHARA_INIT).writeS32(cfg.charaInit);
    req.add(REQ.EVENT_ENTITY).writeU32(0);
    req.add(REQ.TALK).writeS32(0);
    const name = cfg.charaInit >= 0 ? 'c0000' : 'c' + String(Math.floor(cfg.npcParam / 10000)).padStart(4, '0');
    const model = req.add(REQ.MODEL);
    model.writePointer(ptr(0));
    model.add(REQ.MODEL_BACKING).writePointer(model.add(REQ.MODEL_BUFFER));
    model.add(REQ.MODEL_LEN).writeU64(name.length);
    model.add(REQ.MODEL_UNK18).writeU32(0);
    model.add(REQ.MODEL_CHAR_SIZE).writeU16(2);
    model.add(REQ.MODEL_TYPE).writeU8(1);
    model.add(REQ.MODEL_FLAGS).writeU8(0);
    model.add(REQ.MODEL_BUFFER).writeUtf16String(name);
    if (spawnDynamicChr === null) {
        spawnDynamicChr = new NativeFunction(va('0x140507d00'), 'pointer', ['pointer', 'pointer']);
    }
    const chr = spawnDynamicChr(pose.wcm, req);
    return { chr: chr, at: at, block: null, center: null, ownerEventId: null };
}

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const CREATE_SUMMON_CHR = new NativeFunction(va('0x1404baea0'), 'pointer', [
    'pointer', // SummonBuddyManager*
    'pointer', // int* creatorEventId (0x140657230: PlayerGameData +0x8)
    'pointer', // int64* creatorSteamId (0x140657160 of the player)
    'pointer', // BlockId* spawnBlockId
    'uint32',  // param_5 (BuddyGenerator passes its entry's field 0x4)
    'pointer', // FieldInsHandle*
    'uint32',  // npcParamId
    'int32',   // npcThinkId
    'int32',   // charaInitParam
    'pointer', // FloatVector4* pos, block-relative
    'float',   // yaw
    'bool',    // param_12 (true at the retail call)
    'bool',    // hasMount
    'uint32',  // buddyStoneParamId
    'uint32',  // buddyParamId
    'uint32',  // dopingLevel
    'bool',    // fromNetwork
    'bool',    // hasMoghGreatRune
]);
const GET_BLOCK_INFO = new NativeFunction(va('0x14066ac40'), 'pointer', ['pointer', 'pointer']);
const FRAME_TICK = va('0x140773900');
const WORLD_CHR_MAN = va('0x143d69ff8');
const FIELD_AREA = va('0x143d6d248');
const MAIN_PLAYER = 0x1e508;
const SUMMON_BUDDY_MANAGER = 0x1e538;
const FIELD_AREA_WORLD_INFO = 0x18;
const BLOCK_CENTER = 0x70;
const CHR_BLOCK_ID = 0x38;
const CHR_BLOCK_ID_FALLBACK = 0x3c;
const CHR_SET_ENTRY = 0x10;
const PLAYER_GAME_DATA = 0x580;
const GAME_DATA_EVENT_ID = 0x8;

const ENV_KEY = 'ER_FRIDA_SPAWN_NPC';
const kernel32 = Process.getModuleByName('kernel32.dll');
const GET_ENV = new NativeFunction(kernel32.getExportByName('GetEnvironmentVariableW'), 'uint32', ['pointer', 'pointer', 'uint32']);
const SET_ENV = new NativeFunction(kernel32.getExportByName('SetEnvironmentVariableW'), 'int', ['pointer', 'pointer']);

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now() }, fields));
}

function physicsOf(chr) {
    const modules = chr.add(0x190).readPointer();
    if (modules.isNull()) return null;
    const physics = modules.add(0x68).readPointer();
    return physics.isNull() ? null : physics;
}

// CSChrDataModule (ChrIns+0x190 modules -> +0x0): hp at +0x138, max hp +0x13c, debug flags byte at
// +0x19b (fromsoftware-rs chr_ins/module/data.rs: bit 1 makes the character undamageable). Bit 0 is
// set too, as the no-dead flag; the HP in the heartbeat is what shows whether either one works.
const DATA_HP = 0x138;
const DATA_DEBUG_FLAGS = 0x19b;
const GOD_BITS = 0x3;

function dataOf(chr) {
    const modules = chr.add(0x190).readPointer();
    if (modules.isNull()) return null;
    const data = modules.readPointer();
    return data.isNull() ? null : data;
}

function hpOf(chr) {
    try {
        const d = dataOf(chr);
        return d === null ? null : [d.add(DATA_HP).readS32(), d.add(DATA_HP + 4).readS32()];
    } catch (e) {
        return null;
    }
}

// ChrIns +0x68 chr_type (i32), +0x6c team_type (u8): fromsoftware-rs cs/chr_ins.rs. Values are
// TEAM_TYPE (Smithbox ER enum): 1 Live (the player), 6 Enemy, 8 Ally, 47 Spirit Summon. cfg.team
// null leaves the spawn on the team it was created with; teamSpawned remembers that one so clearing
// cfg.team puts it back.
const CHR_TYPE = 0x68;
const TEAM_TYPE = 0x6c;
let teamSpawned = null;

// A summon-path spawn is put on its creator's team, which is what CreateSummonChr gives it (1, the
// player's, measured) and what a peer's copy is created with: a local 47 left a Seamless partner
// able to hit her but not lock on to her (user report 2026-10-05).
function applyTeam(chr) {
    const t = chr.add(TEAM_TYPE);
    const before = t.readU8();
    if (teamSpawned === null) teamSpawned = before;
    let want = cfg.team === null || cfg.team === undefined ? teamSpawned : cfg.team;
    if (spawnedPath === 'summon') {
        const player = WORLD_CHR_MAN.readPointer().add(MAIN_PLAYER).readPointer();
        if (!player.isNull()) want = player.add(TEAM_TYPE).readU8();
        // cfg.summonTeam overrides that locally (6 is the team every enemy measured has); a peer's
        // copy still takes the creator's team from the summon packet.
        if (cfg.summonTeam !== null && cfg.summonTeam !== undefined) want = cfg.summonTeam;
    }
    if (want !== before) {
        t.writeU8(want);
        emit('team', { chr: chr.toString(), from: before, to: want, spawnedWith: teamSpawned });
    }
    return want;
}

// ChrIns +0x90 initial position (HavokPosition, physics space) is the AI's POINT_INITIAL, its home:
// measured 2026-10-05, writing his own physics position there took GetDist(POINT_INITIAL) from
// 44.97 to 0.69. Measured the same day, twice: whenever his home is far from him (52 m from his
// spawn point, past Moongrum's maxBackhomeDist of 45; or 12 m away after the home was moved onto
// the player) none of his Lua runs at all -- the engine walks him home itself, here into a wall.
// cfg.home picks where the home is, rewritten every heartbeat:
//   'spawn'   where he was created (homeSpawned), the game's own behaviour
//   'self'    wherever he stands, so he is always home and his Lua always decides
//   'player'  the player's position (kept for the record: it starves his Lua, as above)
const INITIAL_POS = 0x90;
let homeSpawned = null;

function applyHome(chr, playerPos) {
    const h = chr.add(INITIAL_POS);
    if (homeSpawned === null) homeSpawned = [h.readFloat(), h.add(4).readFloat(), h.add(8).readFloat()];
    let want = homeSpawned;
    if (cfg.home === 'player' && playerPos) want = playerPos;
    if (cfg.home === 'self') {
        const physics = physicsOf(chr);
        if (physics !== null) want = [0, 4, 8].map((o) => physics.add(0x70 + o).readFloat());
    }
    for (let i = 0; i < 3; i += 1) h.add(i * 4).writeFloat(want[i]);
    return cfg.home || 'spawn';
}

// Equipment overrides for the spawn, from lab_equip in the AI mods: cfg.equip is
// {think: {slot: protector id}}, applied only when the spawn's think id is the key. A key of
// "think.N" applies to the Nth summon only (0 is `spawned`, 1.. the extras in creation order) and
// wins over the plain think key slot by slot, so one think id can field differently geared
// characters (scripts/frida/ai-lua/mods/turtles.lua).
//
// The spawn holds two ChrAsm copies (fromsoftware-rs cs/player_game_data.rs ChrAsm,
// equipment_param_ids at +0x7c, one i32 per ChrAsmSlot). Measured 2026-10-05 on Moongrum, whose
// CharaInitParam helm is 980000: it reads at PlayerIns +0x638 -> +0xac, and inline in his
// PlayerGameData (PlayerIns +0x580) at +0x3c8, the EquipGameData ChrAsm at +0x31c. Both are written,
// at creation and every heartbeat, so neither copy can put the piece back. PlayerGameData is
// PLAYER_GAME_DATA, above.
const CHR_ASM_PTR = 0x638;
const PGD_CHR_ASM = 0x31c;
const CHR_ASM_PARAM_IDS = 0x7c;
// ChrAsmSlot order: the weapon slots interleave left and right (0 = WeaponLeft1, 1 = WeaponRight1).
const EQUIP_SLOT = {
    left1: 0, right1: 1, left2: 2, right2: 3, left3: 4, right3: 5,
    head: 12, chest: 13, hands: 14, legs: 15,
};
let equipApplied = null;

// A weapon slot also gets its own gaitem, because the skill is resolved through the gaitem and not
// the param id: measured 2026-10-05 on Moonrithyll, after right1's param id was rewritten to
// Bloodfiend's Arm, ai:GetArtsID still answered 5550 (Tremendous Phalanx) and the gaitem her ChrAsm
// named still held 4690000, her CharaInitParam sword, with no gem. The gaitem is minted the way the
// chainsaw driver's grant mints one (0x140672b30 into a 16-byte handle buffer, gem -1 for none) and
// its handle written into ChrAsm gaitem_handles (+0x24, one u32 per ChrAsmSlot). The handle buffer
// is never released, so the gaitem keeps its reference for as long as she lives.
const CHR_ASM_GAITEM = 0x24;
const CSGAITEM = va('0x143d6d900');
const MINT_WEAPON = new NativeFunction(va('0x140672b30'), 'pointer', ['pointer', 'pointer', 'int', 'int']);
const minted = new Map();

function mintKey(chr, slot, piece) {
    return `${chr}:${slot}:${piece.id}:${piece.gem}`;
}

// What `table` (cfg.equip or cfg.face) holds for the summon at `index`: its own "think.N" entry
// over the think-wide one.
function forSpawn(table, index) {
    const all = table || {};
    const shared = all[String(cfg.think)];
    const own = index === undefined ? undefined : all[`${cfg.think}.${index}`];
    if (shared === undefined) return own;
    if (own === undefined) return shared;
    return typeof own === 'string' ? own : Object.assign({}, shared, own);
}

// Memorized spells, "think:spells=id,id,..." (_lab.lua lab_spells; MagicParam ids, which are the
// spells' goods ids: Swift Glintstone Shard 4010, Bestial Sling 6800). fromsoftware-rs
// cs/player_game_data.rs: EquipGameData (PlayerGameData +0x2b0) holds EquipMagicData* just before
// EquipItemData, i.e. at +0x280 (EquipItemData's quick slots, measured at +0x290, follow its
// vftable at +0x288). EquipMagicData: vftable, EquipGameData* back-pointer at +0x8, 14 entries of
// {param_id i32, charges i32} at +0x10, selected_slot at +0x80. The back-pointer is checked before
// anything is written, so a wrong offset writes nothing and says so.
const EGD_IN_PGD = 0x2b0;
const EGD_MAGIC_PTR = 0x280;
const MAGIC_BACKPTR = 0x8;
const MAGIC_ENTRIES = 0x10;
const MAGIC_SLOTS = 14;
const MAGIC_SELECTED = 0x80;
const spellsWarned = new Set();

function applySpells(chr, index) {
    const want = forSpawn(cfg.spells, index);
    if (!Array.isArray(want) || want.length === 0) return;
    const egd = chr.add(PLAYER_GAME_DATA).readPointer().add(EGD_IN_PGD);
    const magic = egd.add(EGD_MAGIC_PTR).readPointer();
    if (magic.isNull() || !magic.add(MAGIC_BACKPTR).readPointer().equals(egd)) {
        const key = chr.toString();
        if (!spellsWarned.has(key)) {
            spellsWarned.add(key);
            emit('spells-error', { chr: key, why: 'EquipMagicData back-pointer mismatch', magic: magic.toString() });
        }
        return;
    }
    const changed = [];
    for (let i = 0; i < MAGIC_SLOTS; i++) {
        const at = magic.add(MAGIC_ENTRIES + i * 8);
        const id = i < want.length ? want[i] : -1;
        const before = at.readS32();
        if (before !== id) {
            at.writeS32(id);
            changed.push({ slot: i, from: before, to: id });
        }
    }
    const sel = magic.add(MAGIC_SELECTED);
    if (sel.readS32() < 0 || sel.readS32() >= want.length) sel.writeS32(0);
    if (changed.length > 0) emit('spells', { chr: chr.toString(), changed, selected: sel.readS32() });
}

// Each summon's current Ash of War, set by artPlanner (ChrIns string -> EquipParamGem id).
const artNow = new Map();

// canMint is true only at creation; the heartbeat only re-writes handles minted earlier, plus any
// a new artPlanner pick needs.
function applyEquip(chr, canMint, index) {
    // Spells first: a spawn that keeps its CharaInitParam gear has no equip entry at all.
    applySpells(chr, index);
    const want = forSpawn(cfg.equip, index);
    if (!want) return null;
    const asms = [chr.add(CHR_ASM_PTR).readPointer(), chr.add(PLAYER_GAME_DATA).readPointer().add(PGD_CHR_ASM)];
    const changed = [];
    for (const [slot, base] of Object.entries(want)) {
        const index = EQUIP_SLOT[slot];
        if (index === undefined) continue;
        // artPlanner's pick replaces the right hand's gem; the weapon, and so the grip, stay.
        const art = slot === 'right1' ? artNow.get(chr.toString()) : undefined;
        const piece = art === undefined ? base : { id: base.id, gem: art };
        let handle = null;
        if (index <= 5) {
            const key = mintKey(chr, slot, piece);
            if ((canMint || art !== undefined) && !minted.has(key)) {
                const buf = Memory.alloc(16);
                MINT_WEAPON(CSGAITEM.readPointer(), buf, piece.id, piece.gem);
                minted.set(key, { buf, handle: buf.readU32() >>> 0 });
            }
            if (minted.has(key)) handle = minted.get(key).handle;
            if (handle === 0) {
                emit('equip-error', { slot, id: piece.id, gem: piece.gem, why: 'mint_failed' });
                handle = null;
            }
        }
        for (const asm of asms) {
            const at = asm.add(CHR_ASM_PARAM_IDS + index * 4);
            const before = at.readS32();
            if (before !== piece.id) {
                at.writeS32(piece.id);
                changed.push({ slot, from: before, to: piece.id });
            }
            if (handle !== null) {
                const g = asm.add(CHR_ASM_GAITEM + index * 4);
                const was = g.readU32() >>> 0;
                if (was !== handle) {
                    g.writeU32(handle);
                    changed.push({ slot, gaitem: handle, gem: piece.gem, was });
                }
            }
        }
    }
    if (changed.length > 0) emit('equip', { chr: chr.toString(), think: cfg.think, changed });
    equipApplied = want;
    return want;
}

// The build's face: a planner `faceData` blob is the game's own 288-byte face buffer (magic FACE,
// version 4), applied with CS::PlayerGameData::CopyFaceDataFromBuffer (1.17.1 0x140261010:
// add rcx,0x760; jmp FaceData::CopyFromBuffer), the call er-build-import-runtime face.rs uses on
// the player. An NPC's PlayerGameData holds the same face block (+0x768, filled from FaceParam by
// the CharaInitParam apply), so the same call works on her. Static reading, by a research pass
// on 2026-10-05; called once at creation, since every accepted call makes the model re-apply it.
const COPY_FACE = new NativeFunction(va('0x140261010'), 'void', ['pointer', 'pointer']);
const SUMMON_NET_ENABLED = new NativeFunction(va('0x1404b7710'), 'bool', []);

// The spirit-summon network calls, 1.17.1, read out of BuddyGenerator 0x1404bbdd0 (bd
// buddy-summon-broadcast-path-1171-2026-10-05).
const PLAYER_STEAM_ID = new NativeFunction(va('0x140657160'), 'pointer', ['pointer', 'pointer']);
const PLAYER_EVENT_ID = new NativeFunction(va('0x140657230'), 'pointer', ['pointer', 'pointer']);
const P2P_HANDLE_OF = new NativeFunction(va('0x14037c670'), 'pointer', ['pointer', 'pointer']);
const P2P_HANDLE_DROP = new NativeFunction(va('0x1404dc280'), 'void', ['pointer']);
const SET_LOCAL_CONTROL = new NativeFunction(va('0x1404df5c0'), 'void', ['pointer', 'pointer', 'uint8']);
const INIT_CHR_OWNED = new NativeFunction(va('0x1404deac0'), 'void', ['pointer', 'pointer', 'uint32']);
const SUMMON_SYNC_FLAGS = new NativeFunction(va('0x1403f59b0'), 'void', ['pointer', 'uint32']);
const BUDDY_PACKET_INIT = new NativeFunction(va('0x140ca68b0'), 'void', ['pointer']);
const BUDDY_PACKET_FLAG = new NativeFunction(va('0x140ca8490'), 'void', ['pointer', 'uint8']);
const BROADCAST_BUDDY_SUMMON = new NativeFunction(va('0x140ca00f0'), 'void', ['pointer', 'pointer']);
// NotifyBuddyUnsummon(SummonBuddyManager*, FieldInsHandle*): the AI's own unsummon. Deferred: the
// manager's post-physics update unlinks the group entry, removes the character and broadcasts
// packet 79, which is the order RemoveChrIns alone gets wrong.
const NOTIFY_BUDDY_UNSUMMON = new NativeFunction(va('0x1404b84b0'), 'void', ['pointer', 'pointer']);
const NET_CHR_SYNC = 0x1e5e0;
const CHR_CREATOR_STEAM_ID = 0x218;
const SUMMON_ARG5 = 0xffffffff;
// BuddyPacketEntry, 72 bytes, as BuddyGenerator fills it.
const BUDDY_PACKET = {
    SIZE: 0x48, BLOCK: 0x04, FIELD_INS_HANDLE: 0x08, CREATOR_STEAM_ID: 0x10, ARG5: 0x18, POS: 0x1c,
    YAW: 0x28, NPC: 0x2c, THINK: 0x30, CHARA_INIT: 0x34, BUDDY_PARAM: 0x38, DOPING: 0x3c,
    BUDDY_STONE: 0x40,
};
const FACE_BLOCK = 0x768;

// A summon's displayed name. Every name plate and lock-on tag gets its text from
// GetChrName(MenuString* out, ChrIns* chr, bool decorate), 1.16.2 0x14075f750 = 1.17.x 0x1407605a0
// (bd npc-overhead-name-source-getchrname-1171-2026-10-06). For an NPC it is NpcParam's nameId,
// never PlayerGameData character_name: writing that changed nothing on screen. The hook below
// points the returned MenuString's rawString (+0) at a buffer of ours for each named summon; the
// MenuString destructor frees only its own heap buffer, so the buffer just has to stay alive.
// A name's buffer is never freed (a tag may hold the pointer past the call); only the ChrIns ->
// name map is cleared, on respawn, since ChrIns addresses are reused.
const GET_CHR_NAME = va('0x1407605a0');
const nameTexts = new Map();
const nameplates = new Map();

function applyName(chr, index) {
    const name = forSpawn(cfg.name, index);
    if (!name) return null;
    if (!nameTexts.has(name)) nameTexts.set(name, Memory.allocUtf16String(name));
    const key = chr.toString();
    const had = nameplates.get(key);
    nameplates.set(key, { name, text: nameTexts.get(name) });
    namesByHandle.set(chr.add(8).readU64().toString(), name);
    return { name, changed: had === undefined || had.name !== name };
}

// The summon sidebar (spirit-ash panel) has one name label for the whole panel and no text per
// bar (bd spirit-ash-hud-panel-name-is-one-label-1171-2026-10-06). Its per-frame producer,
// 0x140771a20 (CSFeManImp*), fills CSFeManImp +0x4d58 visible (u8), +0x4da0 the label MenuString,
// +0x4dd8 the bar count and +0x4de0 the bars (stride 0x18, ChrIns handle first), and takes the
// label from the summoning goods, which our summons never had, so it is blank. After it runs, the
// label's rawString is pointed at our names joined in bar order; the label setter 0x140736200 uses
// rawString when it is set.
const FE_BUDDY_PANEL = va('0x140771a20');
const namesByHandle = new Map();
const panelTexts = new Map();
let panelSeen = null;
const panelHook = Interceptor.attach(FE_BUDDY_PANEL, {
    onEnter(args) {
        this.fe = args[0];
    },
    onLeave() {
        try {
            const fe = this.fe;
            if (fe.add(0x4d58).readU8() === 0) return;
            const n = Math.min(fe.add(0x4dd8).readS32(), 5);
            const handles = [];
            const parts = [];
            for (let i = 0; i < n; i++) {
                const h = fe.add(0x4de0 + i * 0x18).readU64().toString();
                handles.push(h);
                if (namesByHandle.has(h)) parts.push(namesByHandle.get(h));
            }
            const seen = `${n}:${parts.join(',')}`;
            if (seen !== panelSeen) {
                panelSeen = seen;
                emit('panel-names', { bars: n, handles, names: parts });
            }
            if (parts.length === 0) return;
            const s = parts.join(' / ');
            if (!panelTexts.has(s)) panelTexts.set(s, Memory.allocUtf16String(s));
            fe.add(0x4da0).writePointer(panelTexts.get(s));
        } catch (e) {
            emit('hook-error', { where: 'panel-names', error: e.message });
        }
    },
});

const nameHook = Interceptor.attach(GET_CHR_NAME, {
    onEnter(args) {
        this.out = args[0];
        this.plate = nameplates.get(args[1].toString());
    },
    onLeave() {
        if (this.plate !== undefined) this.out.writePointer(this.plate.text);
    },
});

function applyFace(chr, index) {
    const hex = forSpawn(cfg.face, index);
    if (!hex) return null;
    const pgd = chr.add(PLAYER_GAME_DATA).readPointer();
    const bytes = new Uint8Array(hex.match(/../g).map((b) => parseInt(b, 16)));
    const buf = Memory.alloc(bytes.length);
    buf.writeByteArray(bytes);
    const before = pgd.add(FACE_BLOCK).readByteArray(bytes.length);
    COPY_FACE(pgd, buf);
    const after = new Uint8Array(pgd.add(FACE_BLOCK).readByteArray(bytes.length));
    const before8 = new Uint8Array(before);
    let changed = 0;
    let differs = 0;
    for (let i = 0; i < bytes.length; i++) {
        if (before8[i] !== after[i]) changed++;
        if (after[i] !== bytes[i]) differs++;
    }
    return { bytes: bytes.length, changed, stillDiffers: differs };
}

// Every character in WorldChrMan's chr sets (fromsoftware-rs WorldChrMan: chr_sets[196] directly
// below null_chr_set, player_grid_area and main_player +0x1e508; ChrSet capacity +0x10, entries
// +0x18, 16-byte ChrSetEntry with the ChrIns first).
const CHR_SETS = 0x1e508 - 0x10 - 196 * 8;
function allChrs() {
    const wcm = WORLD_CHR_MAN.readPointer();
    const out = [];
    for (let s = 0; s < 196; s++) {
        const set = wcm.add(CHR_SETS + s * 8).readPointer();
        if (set.isNull()) continue;
        const cap = set.add(0x10).readU32();
        const entries = set.add(0x18).readPointer();
        if (entries.isNull() || cap > 4096) continue;
        for (let i = 0; i < cap; i++) {
            const chr = entries.add(i * 16).readPointer();
            if (!chr.isNull()) out.push(chr);
        }
    }
    return out;
}

function posOf(chr) {
    const physics = physicsOf(chr);
    if (physics === null) return null;
    const p = physics.add(0x70);
    return [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()];
}

// Inventory entries of a PlayerIns (fromsoftware-rs EquipGameData at PlayerGameData +0x2b0,
// EquipInventoryData at +0x158 in it, 24-byte entries: gaitem handle, item id, quantity).
function inventoryOf(chr) {
    const inv = chr.add(PLAYER_GAME_DATA).readPointer().add(0x2b0 + 0x158);
    const head = inv.add(0x10).readPointer();
    const len = inv.add(0x18).readU32();
    return Array.from({ length: Math.min(len, 1024) }, (_, i) => head.add(i * 24));
}

// Her heals are goods 50201 (CharaInitParam 2024320 item_03; SpEffect 19391 heals 30% of max HP).
// A simulated player carries one FP flask and the rest HP, so cfg.heals defaults to 13 of the 14.
const HEAL_ITEM = 0x40000000 | 50201;
// A goods gaitem handle is not indexed: the category bits over the item id (her entry read
// 0xb000c419 for 50201).
const HEAL_HANDLE = 0xb0000000 | 50201;
// The inventory add the chainsaw driver's grant uses: (EquipGameData*, handle buffer, quantity,
// u8, u8). Game thread only, so a missing entry is queued for the frame tick.
const ADD_BY_HANDLE = new NativeFunction(va('0x140246480'), 'int', ['pointer', 'pointer', 'uint32', 'uint8', 'uint8']);
let pendingHealAdd = null;

// Food kept in every summon's quick slots for brain_turtles.lua to eat when idle: one of each at a
// time (both are max held 1), and a new one restockMs after the last was eaten. Restocking is the
// stew's only cooldown; crab is eaten whenever neither buff is up, so it just comes back quickly.
//   Scorpion Stew  goods 2001202, slot 1  physical damage negation up and 8 HP a second
//   Boiled Crab    goods 820,     slot 2  physical damage negation greatly up
// Quick slots, measured 2026-10-06 on a turtle's EquipGameData (PlayerGameData +0x2b0): slot N is
// {gaitem handle u32, inventory index i32} at +0x290 + 8N, mirrored as an item id at +0x3a0 + 4N.
// The heal sat in slot 0 as {0xb000c419, 4} with its entry first in the normal item list and a key
// item capacity of 4, so an inventory index is key capacity + position in the normal list.
// When one is eaten, 'food' reports the SpEffect ids that appeared since it was stocked: that is
// how the stew's (20501201, 20501202) were found, and how the crab's will be.
// The two greases ride the same path, but only for a summon whose right-hand weapon takes grease
// (EquipParamWeapon isEnhance; the game's own use check refuses the rest):
//   Dragonbolt Grease             goods 2001410, slot 3  SpEffect 20501410 (left 20501412), 60 s
//   Drawstring Dragonbolt Grease  goods 2001510, slot 4  SpEffect 20501411 (left 20501413), 25 s
// brain_turtles.lua applies the first when idle and the second in battle.
const FOODS = [
    { name: 'stew', goods: 2001202, slot: 1, restockMs: 60000 },
    { name: 'crab', goods: 820, slot: 2, restockMs: 5000 },
    { name: 'grease', goods: 2001410, slot: 3, restockMs: 2000, needsEnhance: true },
    { name: 'drawstring', goods: 2001510, slot: 4, restockMs: 2000, needsEnhance: true },
    // Throwables from the turtles' planner builds, for brain_turtles.lua's volley, a full stack at
    // a time (EquipParamGoods maxNum). Slot 10 is pouch 0: EquipItemData's six pouch slots follow
    // its ten quick slots, and ChrAsm's pouch ids follow its quick item ids, so +8N and +4N reach it.
    { name: 'kukri', goods: 1730, slot: 5, restockMs: 3000, qty: 30 },
    { name: 'harpoon', goods: 2001710, slot: 6, restockMs: 3000, qty: 5 },
    { name: 'hefty-pot', goods: 2000690, slot: 7, restockMs: 3000, qty: 10 },
    { name: 'fetid-pot', goods: 330, slot: 8, restockMs: 3000, qty: 10 },
    { name: 'albinauric-pot', goods: 610, slot: 9, restockMs: 3000, qty: 10 },
    { name: 'spark-aromatic', goods: 3510, slot: 10, restockMs: 3000, qty: 10 },
    // Boluses for statusCheck below, in pouch slots 1..5. Each bolus's SpEffect chain ends on a row
    // with -99999 build-up of its status (3061 poison, 3071 rot, 3051 bleed, 3093 frost); the
    // poison, rot and frost ones also end a running ailment (parent stateInfo 10, 11, 276).
    { name: 'neutralizing', goods: 900, slot: 11, restockMs: 3000, qty: 5 },
    { name: 'preserving', goods: 940, slot: 12, restockMs: 3000, qty: 5 },
    { name: 'stanching', goods: 910, slot: 13, restockMs: 3000, qty: 5 },
    // The last two pouch slots went to these (user rules 2026-10-06), in place of the Thawfrost and
    // Clarifying Boluses: all sixteen slots are taken, and ChangeEquipItem_ById only finds an item
    // that is in one (measured: an item held in the inventory alone was refused).
    //   Uplifting Aromatic  goods 3500, bullet 10350000 -> SpEffects 503500 / 503501, 40 s, area
    //   Mimic's Veil        goods 3040 (not consumed), bullet 10304000 -> SpEffect 503040
    { name: 'uplifting', goods: 3500, slot: 14, restockMs: 3000, qty: 10 },
    { name: 'mimic-veil', goods: 3040, slot: 15, restockMs: 3000, qty: 1 },
];

// EquipParamWeapon isEnhance for the weapon in a summon's right hand, read from the live param
// table the way scripts/frida/goods-row-live.js walks EquipParamGoods: SoloParamRepository (1.17
// .data rva 0x3d85f58) -> holder index 0 is EquipParamWeapon -> PARAM blob, rows at +0x40 with a
// 24-byte stride. The row is the param id with the upgrade level dropped ((id / 100) * 100, as
// fromsoftware-rs solo_param_repository.rs looks it up); isEnhance is +0x106 bit 7
// (EQUIP_PARAM_WEAPON_ST bits_106). Regulation 1.17.1: Keen Parrying Dagger 1, Keen Flail 1,
// Staff of the Avatar 0.
const SOLO_PARAM_REPOSITORY = va('0x143d85f58');
const WEAPON_IS_ENHANCE = 0x106;
const enhanceOf = new Map();

// Row `id` of solo param `index` (fromsoftware-rs solo_param_repository.rs: 0 EquipParamWeapon,
// 15 SpEffectParam), by binary search: PARAM rows are sorted by id.
function paramRow(index, id) {
    const repo = SOLO_PARAM_REPOSITORY.readPointer();
    if (repo.isNull() || repo.add(0x80 + index * 72).readS32() <= 0) return null;
    const fd4 = repo.add(0x88 + index * 9 * 8).readPointer().add(0x80).readPointer();
    const blob = fd4.add(0x80).readPointer();
    let lo = 0;
    let hi = blob.add(0x0a).readU16() - 1;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const entry = blob.add(0x40 + mid * 24);
        const at = entry.readU32();
        if (at === id) return blob.add(entry.add(8).readU64().toNumber());
        if (at < id) lo = mid + 1;
        else hi = mid - 1;
    }
    return null;
}

function weaponRow(id) {
    return paramRow(0, id);
}

// SpEffectParam spCategory, u16 at +0x13e (Smithbox paramdef laid out by er-param-read.py; row
// size 0x390 matches the regulation's stride). A status in progress is a live entry of category
// 10003..10010, and the game refuses a second row of that category until it ends (bd
// er-status-reproc-lockout-spcategory-2026-09-29).
const SP_CATEGORY = 0x13e;
const categoryOf = new Map();

function spCategory(id) {
    if (!categoryOf.has(id)) {
        const row = paramRow(15, id);
        if (row === null) return -1;
        categoryOf.set(id, row.add(SP_CATEGORY).readU16());
    }
    return categoryOf.get(id);
}

function statusCategories(chr) {
    const cats = new Set();
    for (const id of speffectIds(chr)) {
        const c = spCategory(id);
        if (c >= 10003 && c <= 10010) cats.add(c);
    }
    return cats;
}

function greasable(chr) {
    const id = chr.add(CHR_ASM_PTR).readPointer().add(CHR_ASM_PARAM_IDS + EQUIP_SLOT.right1 * 4).readS32();
    const base = Math.floor(id / 100) * 100;
    if (!enhanceOf.has(base)) {
        const row = weaponRow(base);
        if (row === null) return false;
        enhanceOf.set(base, (row.add(WEAPON_IS_ENHANCE).readU8() & 0x80) !== 0);
        emit('greasable', { weapon: base, enhance: enhanceOf.get(base) });
    }
    return enhanceOf.get(base);
}
const EGD = 0x2b0;
const EGD_QUICK_SLOTS = 0x290;
const EGD_QUICK_ITEM_IDS = 0x3a0;
const INV_KEY_CAPACITY = 0x158 + 0x1c;
const foodGiven = new Set();
const foodEatenAt = new Map();
const foodBefore = new Map();

function speffectIds(chr) {
    const ids = [];
    let entry = chr.add(0x178).readPointer().add(0x08).readPointer();
    for (let i = 0; i < 512 && !entry.isNull(); i++) {
        ids.push(entry.add(0x08).readS32());
        entry = entry.add(0x30).readPointer();
    }
    return ids;
}

function tendFood(chr) {
    for (const food of FOODS) {
        try {
            if (food.needsEnhance && !greasable(chr)) continue;
            const r = stockFood(chr, food);
            if (r !== null) emit('food', { chr: chr.toString(), food: food.name, ...r });
        } catch (e) {
            emit('hook-error', { where: `food-${food.name}`, error: e.message });
        }
    }
}

function foodAt(chr, item) {
    return inventoryOf(chr).findIndex((e) => (e.add(4).readU32() >>> 0) === (item >>> 0)
        && e.add(8).readU32() > 0);
}

function stockFood(chr, food) {
    const item = 0x40000000 | food.goods;
    const handle = 0xb0000000 | food.goods;
    const egd = chr.add(PLAYER_GAME_DATA).readPointer().add(EGD);
    const key = `${chr}:${food.name}`;
    let at = foodAt(chr, item);
    if (at < 0) {
        // The first comes at once; each later one restockMs after the last was eaten.
        if (foodGiven.has(key)) {
            if (!foodEatenAt.has(key)) {
                foodEatenAt.set(key, Date.now());
                const before = new Set(foodBefore.get(key) || []);
                return { eaten: true, newEffects: speffectIds(chr).filter((id) => !before.has(id)) };
            }
            if (Date.now() - foodEatenAt.get(key) < food.restockMs) return null;
        }
        const buf = Memory.alloc(16);
        buf.writeU32(handle >>> 0);
        buf.add(4).writeU32(item >>> 0);
        ADD_BY_HANDLE(egd, buf, food.qty || 1, 1, 1);
        at = foodAt(chr, item);
        if (at < 0) return { added: false };
        foodGiven.add(key);
        foodBefore.set(key, speffectIds(chr));
    }
    foodEatenAt.delete(key);
    const index = egd.add(INV_KEY_CAPACITY).readU32() + at;
    const slot = egd.add(EGD_QUICK_SLOTS + 8 * food.slot);
    if ((slot.readU32() >>> 0) === (handle >>> 0) && slot.add(4).readS32() === index) return null;
    slot.writeU32(handle >>> 0);
    slot.add(4).writeS32(index);
    egd.add(EGD_QUICK_ITEM_IDS + 4 * food.slot).writeU32(item >>> 0);
    return { stocked: true, index };
}

function setHeals(chr) {
    for (const e of inventoryOf(chr)) {
        if ((e.add(4).readU32() >>> 0) === (HEAL_ITEM >>> 0)) {
            const before = e.add(8).readU32();
            if (before !== cfg.heals) e.add(8).writeU32(cfg.heals);
            return { before, after: cfg.heals };
        }
    }
    // Drunk to zero, the entry is gone: put it back through the game's own add.
    pendingHealAdd = { chr, qty: cfg.heals };
    return { before: 0, queued: cfg.heals };
}

function addHeals() {
    if (pendingHealAdd === null) return;
    const { chr, qty } = pendingHealAdd;
    pendingHealAdd = null;
    try {
        if (!isAlive(chr).alive) return;
        const buf = Memory.alloc(16);
        buf.writeU32(HEAL_HANDLE >>> 0);
        buf.add(4).writeU32(HEAL_ITEM >>> 0);
        const egd = chr.add(PLAYER_GAME_DATA).readPointer().add(0x2b0);
        const r = ADD_BY_HANDLE(egd, buf, qty, 1, 1);
        const entry = inventoryOf(chr).find((e) => (e.add(4).readU32() >>> 0) === (HEAL_ITEM >>> 0));
        emit('heals-added', { chr: chr.toString(), qty, result: r, now: entry ? entry.add(8).readU32() : null });
    } catch (e) {
        emit('hook-error', { where: 'heals-add', error: e.message });
    }
}

// Flasks refill only when the player rests at a Site of Grace (or respawns at one), so a rise in
// the player's own flask count is the rest signal. Flasks are goods 1000..1099 (Crimson and
// Cerulean, every upgrade level).
let lastPlayerFlasks = null;
function playerFlasks(player) {
    let total = 0;
    const ids = [];
    for (const e of inventoryOf(player)) {
        const item = e.add(4).readU32() >>> 0;
        if ((item & 0xf0000000) >>> 0 !== 0x40000000) continue;
        const id = item & 0x0fffffff;
        if (id < 1000 || id >= 1100) continue;
        const qty = e.add(8).readU32();
        total += qty;
        ids.push([id, qty]);
    }
    return { total, ids };
}

// Sitting at a grace plays 68010 (sit down) then 68011 (seated), measured 2026-10-05 on the
// player's TimeAct queue while the flask count stayed at its full 14, which is why the flask rise
// alone misses a rest taken with full flasks.
const GRACE_SIT_ANIMS = [68010, 68011];
let lastPlayerSat = false;
// Resting dismisses the summons: measured 2026-10-06, 0.6 s after the player sat, all three were
// removed through ersc.dll+0x8a384 -> eldenring.exe+0x4b96fa, the game's own spirit-ash dismissal.
// Set when that happens while seated; watchGraceRest respawns the squad when the player stands.
let graceResummon = false;

function watchGraceRest(player) {
    try {
        const f = playerFlasks(player);
        const anim = (attackerState(player).last || {}).anim;
        const sat = GRACE_SIT_ANIMS.includes(anim);
        const rose = lastPlayerFlasks !== null && f.total > lastPlayerFlasks;
        if ((sat && !lastPlayerSat) || rose) {
            const heals = spawned !== null && isAlive(spawned).alive ? setHeals(spawned) : null;
            emit('grace-rest', { why: sat && !lastPlayerSat ? 'sit' : 'flasks', anim, flasks: f.ids,
                before: lastPlayerFlasks, after: f.total, heals });
        }
        // Standing up from a rest that dismissed the squad brings it back.
        if (graceResummon && lastPlayerSat && !sat && !despawnedEarlier()) {
            graceResummon = false;
            emit('grace-resummon', { anim });
            rpc.exports.respawn();
        }
        lastPlayerSat = sat;
        lastPlayerFlasks = f.total;
        return f;
    } catch (e) {
        return { error: e.message };
    }
}

// Applied every heartbeat, because the player's ChrIns and its data module are rebuilt on death
// and on every load.
function applyGod() {
    try {
        const wcm = WORLD_CHR_MAN.readPointer();
        if (wcm.isNull()) return null;
        const player = wcm.add(MAIN_PLAYER).readPointer();
        if (player.isNull()) return null;
        const d = dataOf(player);
        if (d === null) return null;
        const f = d.add(DATA_DEBUG_FLAGS);
        const before = f.readU8();
        const after = cfg.god ? (before | GOD_BITS) : (before & ~GOD_BITS);
        if (after !== before) f.writeU8(after);
        return after;
    } catch (e) {
        return null;
    }
}

// The class name behind a vtable, from its MSVC RTTI complete object locator.
function rttiName(vt) {
    try {
        const col = vt.sub(8).readPointer();
        const td = game.base.add(col.add(12).readU32());
        return td.add(16).readCString();
    } catch (e) {
        return null;
    }
}

// Alive means: still a ChrIns kind by RTTI, and its ChrSet entry still points back at it. The
// constructor sets entry->chrIns = this and ChrSetEntry::Reset zeroes it, so a freed character, or
// a new object reusing its memory, fails the second test even when it passes the first. Measured
// 2026-10-05: a freed spawn's address held a MoWwiseSoundObj a minute later.
function isAlive(chr) {
    try {
        const cls = rttiName(chr.readPointer());
        if (cls !== '.?AVPlayerIns@CS@@' && cls !== '.?AVEnemyIns@CS@@') return { alive: false, cls: cls };
        const entry = chr.add(CHR_SET_ENTRY).readPointer();
        return { alive: !entry.isNull() && entry.readPointer().equals(chr), cls: cls };
    } catch (e) {
        return { alive: false, cls: null };
    }
}

// The extras ride in a third variable, as a comma list.
function rememberExtras() {
    SET_ENV(Memory.allocUtf16String(ENV_KEY + '_EXTRA'),
        Memory.allocUtf16String(extras.map((c) => c.toString()).join(',')));
}

function rememberedExtras() {
    const buf = Memory.alloc(1024);
    const n = GET_ENV(Memory.allocUtf16String(ENV_KEY + '_EXTRA'), buf, 500);
    if (n === 0 || n >= 500) return [];
    return buf.readUtf16String().split(',').filter((s) => s.length > 0).map((s) => ptr(s));
}

// Is this field-ins handle (8 bytes, as RequestWarp gets it) one of our summons.
function isOurHandle(handle) {
    const h = handle.readU64();
    if (spawned !== null && h.equals(spawned.add(8).readU64())) return true;
    return extras.some((c) => isAlive(c).alive && h.equals(c.add(8).readU64()));
}

// Unsummon a summon-path character the game's way (see NOTIFY_BUDDY_UNSUMMON).
function unsummon(chr) {
    const wcm = WORLD_CHR_MAN.readPointer();
    if (wcm.isNull() || !isAlive(chr).alive) return false;
    NOTIFY_BUDDY_UNSUMMON(wcm.add(SUMMON_BUDDY_MANAGER).readPointer(), chr.add(8));
    return true;
}

// Every extra goes to the unsummon queue, and none are still to come.
function dropExtras() {
    for (const c of extras) if (isAlive(c).alive) extrasToRemove.push(c);
    extras = [];
    extrasWanted = 0;
    rememberExtras();
}

// Gear, face and heals on a freshly created character, on its creation frame. cfg.dress false
// leaves her as CharaInitParam made her. `index` is the summon's place (0 is `spawned`).
function dressNew(chr, index) {
    if (cfg.dress === false) return;
    applyEquip(chr, true, index);
    try {
        const face = applyFace(chr, index);
        if (face !== null) emit('face', { chr: chr.toString(), ...face });
    } catch (e) {
        emit('hook-error', { where: 'face', error: e.message });
    }
    try {
        const name = applyName(chr, index);
        if (name !== null) emit('name', { chr: chr.toString(), ...name });
    } catch (e) {
        emit('hook-error', { where: 'name', error: e.message });
    }
    try {
        emit('heals', { chr: chr.toString(), ...(setHeals(chr) || { missing: true }) });
    } catch (e) {
        emit('hook-error', { where: 'heals', error: e.message });
    }
}

function rememberedSpawn() {
    const buf = Memory.alloc(64);
    const n = GET_ENV(Memory.allocUtf16String(ENV_KEY), buf, 32);
    if (n === 0 || n >= 32) return null;
    return ptr(buf.readUtf16String());
}

// The spawn path rides beside the remembered pointer, because a summon-path spawn must never be
// removed with RemoveChrIns (see respawn()).
function rememberSpawn(chr, path) {
    SET_ENV(Memory.allocUtf16String(ENV_KEY), Memory.allocUtf16String(chr.toString()));
    SET_ENV(Memory.allocUtf16String(ENV_KEY + '_PATH'), Memory.allocUtf16String(path));
}

function rememberedPath() {
    const buf = Memory.alloc(64);
    const n = GET_ENV(Memory.allocUtf16String(ENV_KEY + '_PATH'), buf, 32);
    if (n === 0 || n >= 32) return 'dynamic';
    return buf.readUtf16String();
}

// The path the live spawn was created through.
let spawnedPath = 'dynamic';

// Same conversion as er-npc-possess intent::yaw_of_quaternion: the heading of local +Z.
function yawOf(q) {
    const [x, y, z, w] = q;
    const rx = 2 * (w * y + x * z);
    const rz = 1 - 2 * (x * x + y * y);
    return Math.atan2(rx, rz);
}

// Where the player stands and faces, or a reason it cannot be read yet.
function playerPose() {
    try {
        const wcm = WORLD_CHR_MAN.readPointer();
        if (wcm.isNull()) return { why: 'WorldChrMan is null' };
        const player = wcm.add(MAIN_PLAYER).readPointer();
        if (player.isNull()) return { why: 'no main player' };
        const physics = physicsOf(player);
        if (physics === null) return { why: 'player has no physics module' };
        const p = physics.add(0x70);
        const pos = [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()];
        if (!pos.every(Number.isFinite) || pos.every((v) => v === 0)) return { why: 'player not placed' };
        const o = physics.add(0x60);
        const q = [o.readFloat(), o.add(4).readFloat(), o.add(8).readFloat(), o.add(12).readFloat()];
        return { wcm: wcm, player: player, pos: pos, yaw: yawOf(q) };
    } catch (e) {
        return { why: 'read faulted: ' + e.message };
    }
}

// `side` moves the spot sideways from straight ahead, in metres, for the extras.
function spawn(pose, side) {
    const manager = pose.wcm.add(SUMMON_BUDDY_MANAGER).readPointer();
    if (manager.isNull()) return { why: 'no SummonBuddyManager' };

    // The player's own block, the way ChrIns::GetBlockIdOrigin reads it.
    let blockId = pose.player.add(CHR_BLOCK_ID).readU32();
    if (blockId === 0xffffffff) blockId = pose.player.add(CHR_BLOCK_ID_FALLBACK).readU32();
    const block = Memory.alloc(4);
    block.writeU32(blockId);
    const info = GET_BLOCK_INFO(FIELD_AREA.readPointer().add(FIELD_AREA_WORLD_INFO).readPointer(), block);
    if (info.isNull()) return { why: 'no WorldBlockInfo for block 0x' + blockId.toString(16) };
    const c = info.add(BLOCK_CENTER);
    const center = [c.readFloat(), c.add(4).readFloat(), c.add(8).readFloat()];

    // Ahead along the facing; height copied, the way intent::ahead_of places a spawn.
    const s = side || 0;
    const at = [
        pose.pos[0] - cfg.distance * Math.sin(pose.yaw) + s * Math.cos(pose.yaw),
        pose.pos[1],
        pose.pos[2] - cfg.distance * Math.cos(pose.yaw) - s * Math.sin(pose.yaw),
    ];
    const local = Memory.alloc(16);
    [at[0] - center[0], at[1] - center[1], at[2] - center[2], 0].forEach((v, i) => local.add(i * 4).writeFloat(v));

    const gameData = pose.player.add(PLAYER_GAME_DATA).readPointer();
    if (gameData.isNull()) return { why: 'player has no PlayerGameData' };
    // The creator ids BuddyGenerator passes (1.17.1 0x1404bc420..0x1404bc4b6): rdx is the player's
    // PlayerGameData +0x8 event id from 0x140657230, r8 its steam id from 0x140657160.
    // Frida frees a Memory.alloc block when its own JS object is collected, so these hold it.
    const steamId = Memory.alloc(8);
    const eventId = Memory.alloc(4);
    PLAYER_STEAM_ID(pose.player, steamId);
    PLAYER_EVENT_ID(pose.player, eventId);
    const handle = Memory.alloc(8);
    handle.writeU32(0xffffffff);
    handle.add(4).writeS32(-1);
    const yaw = pose.yaw + Math.PI;

    const chr = CREATE_SUMMON_CHR(manager, eventId, steamId, block, SUMMON_ARG5, handle,
        cfg.npcParam, cfg.think, cfg.charaInit, local, yaw,
        // Frida's 'bool' argument type takes integers; a JS boolean raises "expected an integer".
        1, 0, 0xffffffff, 0xffffffff, 0, 0, 0);
    let net = null;
    if (!chr.isNull() && cfg.net !== false) net = announceSummon(chr, blockId, local, yaw);
    return {
        chr: chr, at: at, block: '0x' + blockId.toString(16), center: center,
        ownerEventId: eventId.readS32(), steamId: steamId.readU64().toString(16), net: net,
    };
}

// What BuddyGenerator does after CreateSummonChr when spirit summons are networked (1.17.1
// 0x1404bc4cd..0x1404bc69b): take local control of the summon's net sync, then broadcast packet 78,
// whose receiver (CreateBuddyFromPacket) makes every peer create the same summon. The gate is
// IsSpiritSummmonNetworkingEnabled, true under Seamless with allow_summons = 1 (measured
// 2026-10-05); the peer applies it again before it creates anything.
function announceSummon(chr, blockId, local, yaw) {
    if (!SUMMON_NET_ENABLED()) return { sent: false, why: 'summon networking is off' };
    const netChrSync = WORLD_CHR_MAN.readPointer().add(NET_CHR_SYNC).readPointer();
    const handle = Memory.alloc(16);
    P2P_HANDLE_OF(chr, handle);
    SET_LOCAL_CONTROL(netChrSync, handle, 1);
    P2P_HANDLE_DROP(handle);
    const handle2 = Memory.alloc(16);
    P2P_HANDLE_OF(chr, handle2);
    INIT_CHR_OWNED(netChrSync, handle2, 0xfff);
    P2P_HANDLE_DROP(handle2);
    SUMMON_SYNC_FLAGS(chr, 0xfff);

    const packet = Memory.alloc(BUDDY_PACKET.SIZE);
    BUDDY_PACKET_INIT(packet);
    BUDDY_PACKET_FLAG(packet, 1);
    packet.add(BUDDY_PACKET.BLOCK).writeU32(blockId);
    packet.add(BUDDY_PACKET.FIELD_INS_HANDLE).writeU64(chr.add(8).readU64());
    packet.add(BUDDY_PACKET.CREATOR_STEAM_ID).writeU64(chr.add(CHR_CREATOR_STEAM_ID).readU64());
    packet.add(BUDDY_PACKET.ARG5).writeU32(SUMMON_ARG5);
    for (let i = 0; i < 3; i++) packet.add(BUDDY_PACKET.POS + i * 4).writeFloat(local.add(i * 4).readFloat());
    packet.add(BUDDY_PACKET.YAW).writeFloat(yaw);
    packet.add(BUDDY_PACKET.NPC).writeS32(cfg.npcParam);
    packet.add(BUDDY_PACKET.THINK).writeS32(cfg.think);
    packet.add(BUDDY_PACKET.CHARA_INIT).writeS32(cfg.charaInit);
    packet.add(BUDDY_PACKET.BUDDY_PARAM).writeS32(-1);
    packet.add(BUDDY_PACKET.DOPING).writeS32(0);
    packet.add(BUDDY_PACKET.BUDDY_STONE).writeS32(-1);
    const unused = Memory.alloc(16);
    BROADCAST_BUDDY_SUMMON(unused, packet);
    return { sent: true, creator: chr.add(CHR_CREATOR_STEAM_ID).readU64().toString(16) };
}

// Who removes the spawn. WorldChrManImp::RemoveChrIns(WorldChrManImp*, ChrIns*) is the despawn
// entry (bd er-spawn-dynamic-chr-safe-entry-and-enedat-drift-2026-09-02; 1.17.1 0x14050b340).
const REMOVE_CHR_INS = va('0x14050b340');
const removeHook = Interceptor.attach(REMOVE_CHR_INS, {
    onEnter(args) {
        if (spawned === null || !args[1].equals(spawned)) return;
        const frames = Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 10).map((a) => {
            const m = Process.findModuleByAddress(a);
            return m === null ? a.toString() : m.name + '+0x' + a.sub(m.base).toString(16);
        });
        emit('removed', { chr: args[1].toString(), ageMs: Date.now() - spawnedAt, frames: frames,
            playerSat: lastPlayerSat });
        if (lastPlayerSat) graceResummon = true;
    },
});

// What one of the spawn's hits does, for the AI's kill prediction (mods/kill_confirm.lua). Every hit
// is computed by CalculateDamage2 (1.17.1 0x140448910, 1.16.2 0x1404483b0; the hook
// chainsaw-driver.js measured live): the attacker is argument 1, the victim [argument 0 + 0x8], and
// on return the HP the hit takes off is the damage info's (argument 2) +0x228. The fact sent is the
// weakest of her last SWING_KEEP hits, so a kill is predicted only when even that one would do it.
// Quiet: it updates LAB_WORLD without making her replan, since it changes on every hit.
const CALC_DAMAGE2 = va('0x140448910');
const DAMAGE_HP = 0x228;
const SWING_KEEP = 5;
const swings = [];
const damageInfo = {};
const GET_CHR_FROM_HANDLE = new NativeFunction(va('0x140508a50'), 'pointer', ['pointer', 'pointer']);

function throwState(chr) {
    try {
        const tm = chr.add(0x190).readPointer().add(0x88).readPointer();
        const node = tm.add(0x10).readPointer();
        const words = node.isNull() ? null
            : Array.from({ length: 10 }, (_, i) => node.add(0x58 + i * 4).readU32() >>> 0);
        return { throwFlags: tm.add(0x18).readU32(), throwNode: words };
    } catch (e) {
        return { throwError: e.message };
    }
}

// Who hit her and how far into which animation (fromsoftware-rs ChrIns +0x60 npc_param_id;
// modules +0x18 CSChrTimeActModule: anim_queue at +0x20, ten {anim_id, play_time, play_time2,
// anim_length}, write_idx +0xc0, read_idx +0xc4). The play time at the hit is that attack's windup.
function attackerState(chr) {
    try {
        const ta = chr.add(0x190).readPointer().add(0x18).readPointer();
        const w = ta.add(0xc0).readU32();
        const r = ta.add(0xc4).readU32();
        const slot = (i) => {
            const e = ta.add(0x20 + (i % 10) * 16);
            return { anim: e.readS32(), t: Math.round(e.add(4).readFloat() * 1000) / 1000,
                len: Math.round(e.add(12).readFloat() * 1000) / 1000 };
        };
        return { attacker: chr.toString(), npcParam: chr.add(0x60).readS32(), w, r,
            last: slot((w + 9) % 10), read: slot(r) };
    } catch (e) {
        return { attacker: chr.toString(), error: e.message };
    }
}

// The squad's view of an attack on any of them, for brain_turtles.lua: a hostile landing a hit on
// the player or on any of our summons bumps tt_ally_hit (a count, so every turtle can tell a new hit
// from one it has already answered) and names the victim in tt_ally_victim (-1 for the player, else
// the summon index). Quiet: each turtle reads it at its next decision. Both are numbers because
// er-ai-lab.py route_world turned only numbers, booleans and lists into Lua: measured 2026-10-06,
// a string victim made it raise on all 250 squad hits, so no turtle ever saw one.
let playerChr = null;
let squadHits = 0;

// The player landing a hit on anything but the squad starts the squad's fight too (user,
// 2026-10-06: they held back while the player fought and joined only once hit themselves).
// tt_player_hit counts those hits; each turtle answers a new one like an ally hit.
let playerHits = 0;

function squadHit(victim, attacker) {
    try {
        if (playerChr !== null && attacker.equals(playerChr) && !victim.equals(playerChr)
            && !victim.equals(spawned) && !extras.some((c) => c.equals(victim))) {
            playerHits++;
            emit('world', { facts: { tt_player_hit: playerHits }, quiet: true });
            return;
        }
        let who = null;
        if (playerChr !== null && victim.equals(playerChr)) who = -1;
        else if (victim.equals(spawned)) who = 0;
        else {
            const i = extras.findIndex((c) => c.equals(victim));
            if (i >= 0) who = i + 1;
        }
        // Any attacker but the player and our own summons. Not TEAM_HOSTILE: measured 2026-10-06,
        // the enemy hitting the turtles was team 7, and a team-6 test let every squad hit through.
        if (who === null) return;
        if ((playerChr !== null && attacker.equals(playerChr)) || attacker.equals(spawned)
            || extras.some((c) => c.equals(attacker))) return;
        learnWindup(attacker);
        if (who !== -1 && artNow.get(victim.toString()) === GEM.parry) {
            const st = attackerState(attacker);
            const swingKey = st.last === undefined ? null : `${st.npcParam}:${st.last.anim}`;
            if (swingKey !== null && Date.now() - (parryRead.get(swingKey) || 0) <= PARRY_WINDOW_MS
                && !parryFailed.has(swingKey)) {
                parryFailed.add(swingKey);
                emit('parry-failed', { swing: swingKey, victim: victim.toString() });
            }
        }
        squadHits++;
        emit('world', { facts: { tt_ally_hit: squadHits, tt_ally_victim: who }, quiet: true });
    } catch (e) {
        emit('hook-error', { where: 'squad-hit', error: e.message });
    }
}

const damageHook = Interceptor.attach(CALC_DAMAGE2.readU8() === 0xe9 ? CALC_DAMAGE2.add(5).add(CALC_DAMAGE2.add(1).readS32()) : CALC_DAMAGE2, {
    onEnter(args) {
        if (spawned === null) return;
        const victim = args[0].add(8).readPointer();
        squadHit(victim, args[1]);
        // A hit on any of ours: who, from how far (centre to centre, metres), for the hitbox question.
        if (victim.equals(spawned) || extras.some((c) => c.equals(victim))) {
            try {
                const a = posOf(args[1]);
                const v = posOf(victim);
                const d = a && v ? Math.round(Math.hypot(a[0] - v[0], a[1] - v[1], a[2] - v[2]) * 100) / 100 : null;
                emit('hit-extra', { victim: victim.toString(), attacker: args[1].toString(),
                    attackerNpc: args[1].add(0x60).readS32(), d });
            } catch (e) {
                emit('hook-error', { where: 'hit-extra', error: e.message });
            }
        }
        if (victim.equals(spawned)) {
            damageInfo[this.threadId] = { info: args[2], attacker: args[1], incoming: true };
            return;
        }
        if (!args[1].equals(spawned)) return;
        damageInfo[this.threadId] = { info: args[2], victim };
    },
    onLeave() {
        const d = damageInfo[this.threadId];
        if (d === undefined) return;
        delete damageInfo[this.threadId];
        if (d.incoming) {
            emit('incoming', { damage: d.info.add(DAMAGE_HP).readS32(), ...attackerState(d.attacker) });
            return;
        }
        const damage = d.info.add(DAMAGE_HP).readS32();
        if (damage <= 0) return;
        swings.push(damage);
        if (swings.length > SWING_KEEP) swings.shift();
        const weakest = Math.min.apply(null, swings);
        emit('world', { facts: { swing_damage: weakest }, quiet: true, hit: damage, victim: d.victim.toString(),
            victimHp: hpOf(d.victim) });
    },
});

// Why the summon-path spawn vanished 1.2 s after appearing (measured 2026-10-05, backtrace through
// the RemoveChrIns hook above): SummonBuddyManager's per-frame update (1.16.2 FUN_1404b8d90, from
// WorldChrMan_PostPhysics) removes every summon whose entry has warpRequested set, and the flag is
// set by SummonBuddyManager::RequestWarp, which is called from HksAct -- the character's own
// behaviour script asks to be warped out. So requests naming our spawn are dropped here.
//   RequestWarp(SummonBuddyManager*, FieldInsHandle*)  1.16.2 0x1404b7e40 -> 1.17.1 0x1404b8360
//   (functions map, and a 24-byte prologue match unique in eldenring-deobf-1.17.1.bin)
//   ChrIns+0x8 fieldInsHandle, the field RequestWarp compares against.
const REQUEST_WARP = va('0x1404b8360');
const requestWarpOriginal = new NativeFunction(REQUEST_WARP, 'void', ['pointer', 'pointer']);
let warpsDropped = 0;
Interceptor.replace(REQUEST_WARP, new NativeCallback(function (manager, handle) {
    try {
        if (isOurHandle(handle)) {
            warpsDropped += 1;
            if (warpsDropped <= 3 || warpsDropped % 100 === 0) {
                emit('warp-dropped', { chr: spawned.toString(), n: warpsDropped, ageMs: Date.now() - spawnedAt });
            }
            return;
        }
    } catch (e) {
        emit('hook-error', { where: 'RequestWarp', error: e.message });
    }
    requestWarpOriginal(manager, handle);
}, 'void', ['pointer', 'pointer']));

// SummonBuddyManager::DespawnAll (1.17.1 0x1404b8160) broadcasts packet 0x50 and dismisses every
// summon; statically the post-physics update calls it whenever no summon area is in range. Measured
// 2026-10-05: it fired every frame from 3.9 s after a summon-path spawn, and the post-physics
// disappear pass removed her 1.3 s later. So while she is the live summon it is dropped, which
// also keeps the dismiss packet from reaching peers.
const DESPAWN_ALL = va('0x1404b8160');
const despawnAllOriginal = new NativeFunction(DESPAWN_ALL, 'void', ['pointer']);
let despawnAlls = 0;
Interceptor.replace(DESPAWN_ALL, new NativeCallback(function (manager) {
    try {
        const ours = (spawned !== null && spawnedPath === 'summon' && isAlive(spawned).alive)
            || extras.some((c) => isAlive(c).alive);
        if (ours) {
            despawnAlls += 1;
            if (despawnAlls <= 3 || despawnAlls % 600 === 0) {
                emit('despawn-all-dropped', { n: despawnAlls, ageMs: Date.now() - spawnedAt });
            }
            return;
        }
    } catch (e) {
        emit('hook-error', { where: 'DespawnAll', error: e.message });
    }
    despawnAllOriginal(manager);
}, 'void', ['pointer']));

let done = false;
let lastWhy = null;
let spawned = null;
let spawnedAt = 0;
// A player pose is readable during the world load too, and a character spawned then is thrown
// away with the load (measured: a spawn at load time left its ChrIns reading 0x4 seconds later).
// So the pose has to have been readable without a break for this long first.
const STABLE_MS = 5000;
let stableSince = null;

// The summons after the first (cfg.count), how many are still to come, and those to unsummon.
const EXTRA_SPACING = 1.5;
let extras = [];
let extrasWanted = 0;
const extrasToRemove = [];
for (const c of rememberedExtras()) {
    if (!isAlive(c).alive) continue;
    if (cfg.replace) extrasToRemove.push(c);
    else extras.push(c);
}

// A spawn from an earlier load of this agent that is still alive is adopted, not duplicated.
const previous = rememberedSpawn();
let pendingRemove = null;
// The path pendingRemove was spawned through: a summon goes through NotifyBuddyUnsummon.
let pendingRemovePath = 'dynamic';
// Set by despawn(): after the pending removal, stop instead of spawning again.
let holdSpawn = false;
if (previous !== null && isAlive(previous).alive) {
    spawnedPath = rememberedPath();
    if (cfg.replace) {
        pendingRemove = previous;
        pendingRemovePath = spawnedPath;
    } else {
        spawned = previous;
        spawnedAt = Date.now();
        done = true;
        if (spawnedPath === 'summon') extrasWanted = Math.max(0, (cfg.count || 1) - 1 - extras.length);
        emit('adopted', { chr: previous.toString(), extras: extras.length, extrasWanted });
    }
} else if (despawnedEarlier()) {
    // despawn() was the last word from an earlier load: a reload must not summon again.
    done = true;
}

// Whether despawn() ran after the last respawn(), across reloads of this agent.
function despawnedEarlier() {
    const buf = Memory.alloc(16);
    return GET_ENV(Memory.allocUtf16String(ENV_KEY + '_HELD'), buf, 4) > 0;
}

function setDespawned(held) {
    SET_ENV(Memory.allocUtf16String(ENV_KEY + '_HELD'), held ? Memory.allocUtf16String('1') : NULL);
}
const removeChrIns = new NativeFunction(REMOVE_CHR_INS, 'void', ['pointer', 'pointer']);

// Her lock-on target, sampled on the game thread every TARGET_EVERY frames and emitted when its
// throw node or animation changes, so a backstab or riposte on it shows up in the log.
const TARGET_EVERY = 15;
let targetFrames = 0;
let lastTargetKey = null;

// The throw-node words and throw flags of every character within THROW_RADIUS of her, emitted as
// 'throw-change' when they change, so a backstab or riposte nearby shows which word holds
// ThrowNodeState (4 InThrowTarget, 6 DeathTarget for the victim).
const THROW_RADIUS = 30;
const throwSeen = new Map();

function sampleThrows() {
    const me = posOf(spawned);
    if (me === null) return;
    for (const chr of allChrs()) {
        const p = posOf(chr);
        if (p === null || Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2]) > THROW_RADIUS) continue;
        const t = throwState(chr);
        const key = JSON.stringify([t.throwNode, t.throwFlags]);
        const k = chr.toString();
        const was = throwSeen.get(k);
        if (was !== undefined && was !== key) {
            emit('throw-change', { chr: k, npcParam: chr.add(0x60).readS32(), hp: hpOf(chr), was: JSON.parse(was),
                now: [t.throwNode, t.throwFlags], anim: attackerState(chr).last });
        }
        throwSeen.set(k, key);
    }
}

// The brain's view of the characters around her, pushed into LAB_WORLD as quiet facts (no replan),
// only when it changes:
//   mr_unhittable  {d1, d2, ...} distances (m, one decimal) of hostiles she must not swing at: 0 HP,
//                  or the throw module's death-transition flag (bit 1, set by TAE ChrActionFlag 69
//                  THROW_DEATH_TRANSITION_DEFENDER, fromsoftware-rs ThrowModuleFlags)
//   mr_near        hostiles within MR_NEAR m of her (the parry rule's "only one enemy near")
//   mr_heavy_reach hostiles within MR_R2_REACH m (a heavy attack's "hits more than one target");
//                  fact names must match [a-z_]+ to pass er-ai-lab.py's route_world
// Hostile is TEAM_HOSTILE until the team table is measured further; seen so far: 6, and 7 for the
// enemy that hit the turtles on 2026-10-06.
const TEAM_HOSTILE = new Set([6, 7]);
const MR_NEAR = 6;
const MR_R2_REACH = 3.2;
let lastBrainFacts = null;

function brainFacts() {
    const me = posOf(spawned);
    if (me === null) return;
    const unhittable = [];
    let near = 0;
    let reach = 0;
    for (const chr of allChrs()) {
        if (chr.equals(spawned) || !TEAM_HOSTILE.has(chr.add(TEAM_TYPE).readU8())) continue;
        const p = posOf(chr);
        if (p === null) continue;
        const d = Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2]);
        if (d > 30) continue;
        const hp = hpOf(chr);
        const t = throwState(chr);
        const dead = hp !== null && hp[0] <= 0;
        if (dead || (t.throwFlags & 2) !== 0) unhittable.push(Math.round(d * 10) / 10);
        if (dead) continue;
        if (d <= MR_NEAR) near++;
        if (d <= MR_R2_REACH) reach++;
    }
    const facts = { mr_unhittable: unhittable, mr_near: near, mr_heavy_reach: reach };
    const key = JSON.stringify(facts);
    if (key === lastBrainFacts) return;
    lastBrainFacts = key;
    emit('world', { facts, quiet: true });
}

function sampleTarget() {
    if (++targetFrames % TARGET_EVERY !== 0 || spawned === null) return;
    try {
        if (isAlive(spawned).alive) {
            sampleThrows();
            brainFacts();
        }
    } catch (e) {
        emit('hook-error', { where: 'throws', error: e.message });
    }
    try {
        if (!isAlive(spawned).alive) return;
        const handlePtr = spawned.add(0x6b0);
        if ((handlePtr.readU32() >>> 0) === 0xffffffff) {
            if (lastTargetKey !== null) emit('target', { handle: null });
            lastTargetKey = null;
            return;
        }
        const chr = GET_CHR_FROM_HANDLE(WORLD_CHR_MAN.readPointer(), handlePtr);
        if (chr.isNull()) return;
        const t = { chr: chr.toString(), npcParam: chr.add(0x60).readS32(), hp: hpOf(chr), ...throwState(chr),
            anim: attackerState(chr).last };
        const key = JSON.stringify([t.chr, t.throwNode, t.throwFlags, t.anim && t.anim.anim, t.hp && t.hp[0] === 0]);
        if (key !== lastTargetKey) emit('target', t);
        lastTargetKey = key;
    } catch (e) {
        emit('hook-error', { where: 'target', error: e.message });
    }
}

// Input reading for brain_turtles.lua's roll. Every frame, the attack animation of each enemy near
// a turtle is read off its TimeAct queue (attackerState). An attack animation's windup -- its play
// time at the moment it hits -- is learned from real hits (learnWindup, from squadHit), the
// earliest seen per enemy NpcParam and animation. When a watched attack's play time reaches
// windup - ROLL_LEAD, tt_roll is bumped with tt_roll_d, the attacker's distance to each turtle in
// range, and the turtle whose own enemy sits at that distance rolls. An attack not yet learned
// rolls at DEFAULT_WINDUP. An animation id is a section times 1000000 plus the TAE id, and enemy
// attacks are TAE 3000..3999 in any section: measured 3003000..3003008 on NpcParam 43111110 and
// 3000, 3003, 3016, 3017 on NpcParam 42700041.
const READ_RADIUS = 8;
const READ_LIST_MS = 300;
const ROLL_RADIUS = 4.5;
const ROLL_LEAD = 0.3;
const DEFAULT_WINDUP = 0.5;
const ANIM_SECTION = 1000000;
const ATTACK_ANIM_MIN = 3000;
const ATTACK_ANIM_MAX = 3999;
const windups = new Map();
const swingSeen = new Map();
let watched = [];
let watchedAt = 0;
let rollSignals = 0;

function squadChrs() {
    const out = [];
    if (spawned !== null && isAlive(spawned).alive) out.push(spawned);
    for (const c of extras) if (isAlive(c).alive) out.push(c);
    for (const c of mimics) if (isAlive(c).alive) out.push(c);
    return out;
}

// Turtles summoned natively by the Mimic Tear Ashes (mimic-tear-turtles.js swaps the params inside
// BuddyGenerator 0x1404bbdd0). Kept out of `spawned`/`extras` so the game's own dismissal, FP cost
// and one-summon rule still apply; here they are only dressed and counted as squad. The Nth body
// a request creates is dressed as turtle N.
const MIMIC_TRIGGER = 207000;

// NpcParam 523590024 is Moongrum's enemy row, so a turtle is created on team 6 and attacks the
// player (user report 2026-10-06); the Mimic Tear's own row puts it on the player's. Called at
// creation and every frame.
function mimicTeam(chr) {
    const player = WORLD_CHR_MAN.readPointer().add(MAIN_PLAYER).readPointer();
    if (player.isNull()) return;
    const want = player.add(TEAM_TYPE).readU8();
    const t = chr.add(TEAM_TYPE);
    if (t.readU8() !== want) t.writeU8(want);
}
let mimics = [];
let mimicTid = null;
let mimicIndex = 0;
Interceptor.attach(va('0x1404bbdd0'), {
    onEnter(args) {
        const request = args[0].add(0x20).readS32();
        if (request < 0 || Math.floor(request / 100) * 100 !== MIMIC_TRIGGER) return;
        mimicTid = this.threadId;
        mimicIndex = 0;
        mimics = [];
        this.mimic = true;
    },
    onLeave() {
        if (!this.mimic) return;
        mimicTid = null;
        emit('mimic-squad', { chrs: mimics.map(String) });
    },
});
Interceptor.attach(va('0x1404baea0'), {
    onLeave(ret) {
        if (this.threadId !== mimicTid || ret.isNull()) return;
        // `ret` is live and reads whatever rax holds later, so keep a copy.
        const chr = ptr(ret.toString());
        mimics.push(chr);
        try {
            mimicTeam(chr);
            dressNew(chr, mimicIndex);
        } catch (e) {
            emit('hook-error', { where: 'mimic-dress', error: e.message });
        }
        mimicIndex += 1;
    },
});

function isAttackAnim(anim) {
    const tae = anim % ANIM_SECTION;
    return anim >= 0 && tae >= ATTACK_ANIM_MIN && tae <= ATTACK_ANIM_MAX;
}

function learnWindup(attacker) {
    const s = attackerState(attacker);
    if (s.last === undefined || !isAttackAnim(s.last.anim)) return;
    const key = `${s.npcParam}:${s.last.anim}`;
    const before = windups.get(key);
    if (before === undefined || s.last.t < before) {
        windups.set(key, s.last.t);
        emit('windup', { key, t: s.last.t, before: before === undefined ? null : before });
    }
}

// Parry learning, for brain_turtles.lua's Parry (user rule 2026-10-06: only a low-risk,
// parryable attack). parryRead is when each NpcParam:anim swing was last read at a turtle;
// a hit from that swing on a turtle carrying Parry within PARRY_WINDOW_MS of the read means the
// parry did not take, and the swing goes into parryFailed for good.
const PARRY_WINDOW_MS = 1500;
const parryRead = new Map();
const parryFailed = new Set();

function readSwings() {
    const squad = squadChrs();
    if (squad.length === 0) return;
    const now = Date.now();
    if (now - watchedAt >= READ_LIST_MS) {
        watchedAt = now;
        const at = squad.map(posOf).filter((p) => p !== null);
        watched = allChrs().filter((chr) => {
            if (squad.some((c) => c.equals(chr)) || (playerChr !== null && chr.equals(playerChr))) return false;
            const hp = hpOf(chr);
            const p = posOf(chr);
            if (hp === null || hp[0] <= 0 || p === null) return false;
            return at.some((q) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) <= READ_RADIUS);
        });
    }
    for (const chr of watched) {
        const s = attackerState(chr);
        if (s.last === undefined) continue;
        const key = chr.toString();
        let seen = swingSeen.get(key);
        if (seen === undefined || seen.w !== s.w || seen.anim !== s.last.anim) {
            seen = { w: s.w, anim: s.last.anim, fired: false };
            swingSeen.set(key, seen);
        }
        if (seen.fired || !isAttackAnim(s.last.anim)) continue;
        const learned = windups.get(`${s.npcParam}:${s.last.anim}`);
        const windup = learned === undefined ? DEFAULT_WINDUP : learned;
        if (s.last.t < windup - ROLL_LEAD) continue;
        seen.fired = true;
        const p = posOf(chr);
        if (p === null) continue;
        // tt_roll_d is each threatened turtle's distance to the attacker, which a turtle in battle
        // matches against its own enemy; tt_roll_p is its distance to the player, which an idle
        // one matches instead. Measured 2026-10-06: NpcParam 21500064 hit all three for seconds
        // while none of them entered battle, so 142 signals went to turtles that had no enemy.
        const me = playerChr === null ? null : posOf(playerChr);
        const dists = [];
        const toPlayer = [];
        for (const c of squad) {
            const q = posOf(c);
            if (q === null) continue;
            const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
            // Only a turtle in reach of the swing is warned: measured 2026-10-06, warning every
            // one within READ_RADIUS kept all three rolling instead of attacking.
            if (d > ROLL_RADIUS) continue;
            dists.push(Math.round(d * 100) / 100);
            if (me !== null) toPlayer.push(Math.round(Math.hypot(q[0] - me[0], q[1] - me[1], q[2] - me[2]) * 100) / 100);
        }
        if (dists.length === 0) continue;
        rollSignals++;
        // Parry is only worth trying on a swing that has never got through one (parryFailed).
        const swingKey = `${s.npcParam}:${s.last.anim}`;
        parryRead.set(swingKey, Date.now());
        emit('world', { facts: { tt_roll: rollSignals, tt_roll_d: dists, tt_roll_p: toPlayer,
            tt_roll_parry: parryFailed.has(swingKey) ? 0 : 1 }, quiet: true });
        emit('read-swing', { attacker: key, npcParam: s.npcParam, anim: s.last.anim, t: s.last.t, windup,
            learned: learned !== undefined, dists });
    }
}

// Permission to throw, for brain_turtles.lua's volley (user rule 2026-10-06): the player has held
// lock-on on the same enemy for VOLLEY_LOCK_MS. The player's lock-on target is PlayerIns +0x6b0, a
// handle (scripts/frida/target-bars-probe.js), resolved like sampleTarget's. While it holds,
// tt_volley is 1 and tt_volley_hp is the enemy's HP; a turtle throws only when its own target's
// HP equals it, which is how it tells the player's enemy from another one. Both are re-sent when
// the HP changes, and tt_volley drops to 0 on unlock, a new target, or the enemy's death.
const VOLLEY_LOCK_MS = 2000;
let volleyLock = null;
let volleySent = null;

function volleyCheck() {
    if (playerChr === null) return;
    const handlePtr = playerChr.add(0x6b0);
    const handle = handlePtr.readU32() >>> 0;
    let hp = null;
    let cats = [];
    let human = 0;
    if (handle !== 0xffffffff) {
        const chr = GET_CHR_FROM_HANDLE(WORLD_CHR_MAN.readPointer(), handlePtr);
        const h = chr.isNull() ? null : hpOf(chr);
        if (h !== null && h[0] > 0) {
            hp = h[0];
            // The statuses already running on it, so no turtle throws one it would refuse.
            cats = Array.from(statusCategories(chr)).sort();
            human = isHuman(chr) ? 1 : 0;
        }
    }
    const now = Date.now();
    if (hp === null) volleyLock = null;
    else if (volleyLock === null || volleyLock.handle !== handle) volleyLock = { handle, since: now };
    const on = volleyLock !== null && now - volleyLock.since >= VOLLEY_LOCK_MS;
    const sent = on ? `1:${hp}:${cats.join(',')}:${human}` : '0';
    if (sent === volleySent) return;
    volleySent = sent;
    emit('world', { facts: { tt_volley: on ? 1 : 0, tt_volley_hp: on ? hp : -1, tt_volley_cats: on ? cats : [],
        tt_volley_human: on ? human : 0 }, quiet: true });
    emit('volley', { on, handle: on ? handle : null, hp, cats, human });
}

// A turtle that cannot get back to the player, for brain_turtles.lua's jumping recovery (user rule
// 2026-10-06: no teleport). Measured the same day: one stood ~66 m away for minutes while its
// approach goal kept running. Stuck means either:
//   pinned  - more than STUCK_GAP m from the player and moved less than STUCK_MOVE m in STUCK_MS;
//   losing  - more than STUCK_FAR m away and no STUCK_GAIN m closer in STUCK_MS while the
//             player, who could be running off, moved less than STUCK_MOVE.
// Each stuck turtle is named by its distance to the player, as in tt_cure.
// Tightened 2026-10-06 (user: "far too loose"): from 3 s / 6 m / 1.5 m / 0.5 s checks.
const STUCK_MS = 1500;
const STUCK_GAP = 5;
const STUCK_FAR = 15;
const STUCK_MOVE = 1.0;
const STUCK_GAIN = 1.5;
const STUCK_CHECK_MS = 250;
// Beyond melee reach plus a margin: closer than this it is fighting, not travelling.
const STUCK_ENEMY_GAP = 3.5;
const stuckTrail = new Map();
let stuckAt = 0;
let stuckSent = null;

function stuckCheck() {
    const now = Date.now();
    if (now - stuckAt < STUCK_CHECK_MS || playerChr === null) return;
    stuckAt = now;
    const me = posOf(playerChr);
    if (me === null) return;
    const out = [];
    const outEnemy = [];
    const why = [];
    for (const c of squadChrs()) {
        const p = posOf(c);
        const hp = hpOf(c);
        if (p === null || hp === null || hp[0] <= 0) continue;
        const gap = Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2]);
        const key = c.toString();
        const trail = (stuckTrail.get(key) || []).filter((s) => now - s.t <= STUCK_MS + STUCK_CHECK_MS);
        trail.push({ t: now, p, me, gap });
        stuckTrail.set(key, trail);
        const old = trail[0];
        if (now - old.t < STUCK_MS) continue;
        const moved = Math.hypot(p[0] - old.p[0], p[1] - old.p[1], p[2] - old.p[2]);
        const playerMoved = Math.hypot(me[0] - old.me[0], me[1] - old.me[1], me[2] - old.me[2]);
        const pinned = gap > STUCK_GAP && moved < STUCK_MOVE;
        const losing = gap > STUCK_FAR && old.gap - gap < STUCK_GAIN && playerMoved < STUCK_MOVE;
        // The same toward its own enemy (user rule 2026-10-06: an enemy it cannot walk to is
        // reached by jumping): more than STUCK_ENEMY_GAP m from its lock-on target and either not
        // moving, or not closing while the enemy stays put. Named by its distance to that enemy.
        const t = enemyOf(c);
        const tq = t === null ? null : posOf(t.chr);
        if (tq !== null) {
            const egap = Math.hypot(p[0] - tq[0], p[1] - tq[1], p[2] - tq[2]);
            const was = old.tq === undefined ? null : old.tq;
            const enemyMoved = was === null ? 0 : Math.hypot(tq[0] - was[0], tq[1] - was[1], tq[2] - was[2]);
            const closed = old.egap === undefined ? Infinity : old.egap - egap;
            if (egap > STUCK_ENEMY_GAP && (moved < STUCK_MOVE || (closed < STUCK_GAIN && enemyMoved < STUCK_MOVE))) {
                outEnemy.push(Math.round(egap * 100) / 100);
                why.push({ chr: key, enemyGap: Math.round(egap * 10) / 10, moved: Math.round(moved * 10) / 10,
                    kind: 'enemy' });
            }
            trail[trail.length - 1].tq = tq;
            trail[trail.length - 1].egap = egap;
        }
        if (!pinned && !losing) continue;
        out.push(Math.round(gap * 100) / 100);
        why.push({ chr: key, gap: Math.round(gap * 10) / 10, moved: Math.round(moved * 10) / 10,
            kind: pinned ? 'pinned' : 'losing' });
    }
    const sent = JSON.stringify(why.map((w) => [w.chr, w.kind]));
    if (sent === stuckSent && out.length === 0 && outEnemy.length === 0) return;
    if (sent !== stuckSent && why.length > 0) emit('stuck', { why });
    stuckSent = sent;
    emit('world', { facts: { tt_stuck_p: out, tt_stuck_e: outEnemy }, quiet: true });
}

// Which Ash of War each turtle carries right now (user rules 2026-10-06), one of the gems its
// build lists (cfg.arts, _lab.lua lab_arts). brain_turtles.lua reads it with GetArtsID and
// decides when to press it; this decides what is on the weapon:
//   Bloodhound's Step  80100   below ART_LOW_HP with its enemy within ART_ESCAPE m: get away to heal
//   Thunderbolt 21600 / Storm Blade 21000   its enemy below ART_FINISH HP and over ART_REACH m
//                       away: catch it (Thunderbolt from ART_BOLT m, Storm Blade nearer)
//   Poisonous Mist 22800 / Chilling Mist 22700   its enemy without that status running: one use,
//                       then off at once (FP drop seen) and not again for ART_MIST_REST ms
//   Blinkbolt 413000   its enemy over ART_REACH m away: get in on it
//   otherwise the first gem listed (Raph's Parry, Mikey's own skill).
// The target is the summon's lock-on handle (PlayerIns +0x6b0, as sampleTarget reads it). FP is the
// data module's +0x148 (fromsoftware-rs chr_ins/module/data.rs). A pick is held ART_HOLD_MS
// unless it is the mist switching off.
const GEM = { parry: 30200, thunderbolt: 21600, chilling: 22700, poison: 22800, storm: 21000,
    blinkbolt: 413000, bloodhound: 80100 };
const GEM_FP = { 21600: 10, 21000: 10, 22700: 14, 22800: 14, 413000: 8, 80100: 5, 30200: 0 };
const ART_LOW_HP = 0.5;
const ART_ESCAPE = 6;
const ART_FINISH = 0.25;
const ART_REACH = 4;
const ART_BOLT = 6;
const ART_MIST_REST = 10000;
// Held this long before another pick: at 1 s, Raph changed every second as the enemy's distance
// crossed the rules' boundaries (measured 2026-10-06).
const ART_HOLD_MS = 3000;
const ART_MIST_ON_MS = 4000;
const ART_CHECK_MS = 250;
const DATA_FP = 0x148;
const artState = new Map();
let artAt = 0;
// rpc forceArt: every summon whose build lists `gem` carries it for `ms`, for checking a switch.
let artForce = null;

function fpOf(chr) {
    const d = dataOf(chr);
    return d === null ? 0 : d.add(DATA_FP).readS32();
}

function lockTargetOf(chr) {
    const handlePtr = chr.add(0x6b0);
    if ((handlePtr.readU32() >>> 0) === 0xffffffff) return null;
    const t = GET_CHR_FROM_HANDLE(WORLD_CHR_MAN.readPointer(), handlePtr);
    if (t.isNull()) return null;
    const hp = hpOf(t);
    return hp === null || hp[0] <= 0 ? null : { chr: t, hp };
}

// Its enemy: the lock-on target if it has one, else the nearest living character readSwings is
// watching near the squad. Measured 2026-10-06: in a session of fights artPlanner only ever
// picked the default gems, because the summons' +0x6b0 lock-on handle stayed empty.
let enemyWhy = null;

function enemyOf(chr) {
    const locked = lockTargetOf(chr);
    if (locked !== null) return locked;
    const p = posOf(chr);
    if (p === null) return null;
    let best = null;
    let bestD = Infinity;
    const why = [];
    for (const w of watched) {
        const hp = hpOf(w);
        const q = posOf(w);
        if (hp === null || hp[0] <= 0 || q === null) {
            why.push({ hp, q: q !== null });
            continue;
        }
        const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
        why.push({ d, q });
        if (best === null || d < bestD) {
            bestD = d;
            best = { chr: w, hp };
        }
    }
    enemyWhy = { n: watched.length, why, p, found: best !== null };
    return best;
}

function pickArt(chr, arts, st) {
    const has = (g) => arts.includes(g);
    const fp = fpOf(chr);
    const can = (g) => has(g) && fp >= GEM_FP[g];
    const t = enemyOf(chr);
    if (t === null) return arts[0];
    const p = posOf(chr);
    const q = posOf(t.chr);
    if (p === null || q === null) return arts[0];
    const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    const mine = hpOf(chr);
    const share = t.hp[0] / t.hp[1];
    if (mine !== null && mine[0] / mine[1] < ART_LOW_HP && d < ART_ESCAPE && can(GEM.bloodhound)) return GEM.bloodhound;
    if (share < ART_FINISH && d > ART_REACH) {
        if (d >= ART_BOLT && can(GEM.thunderbolt)) return GEM.thunderbolt;
        if (can(GEM.storm)) return GEM.storm;
        if (can(GEM.thunderbolt)) return GEM.thunderbolt;
    }
    if (Date.now() - (st.mistUsedAt || 0) >= ART_MIST_REST) {
        const cats = statusCategories(t.chr);
        if (!cats.has(10004) && can(GEM.poison)) return GEM.poison;
        if (!cats.has(10007) && can(GEM.chilling)) return GEM.chilling;
    }
    if (d > ART_REACH && can(GEM.blinkbolt)) return GEM.blinkbolt;
    return arts[0];
}

function artPlanner() {
    const now = Date.now();
    if (now - artAt < ART_CHECK_MS) return;
    artAt = now;
    const all = [spawned].concat(extras);
    all.forEach((chr, index) => {
        if (chr === null || !isAlive(chr).alive) return;
        const arts = forSpawn(cfg.arts, index);
        if (!Array.isArray(arts) || arts.length === 0) return;
        const key = chr.toString();
        const st = artState.get(key) || { gem: undefined, at: 0, fp: 0 };
        const fp = fpOf(chr);
        // A mist is one use: the FP drop of its cost means it went off, so switch it off now.
        // NPC FP never drops (measured 2026-10-06: 69 through a whole fight of skills), so a mist
        // counts as used once its status is running on the enemy, or ART_MIST_ON_MS after it went on.
        const mist = st.gem === GEM.poison || st.gem === GEM.chilling;
        let used = false;
        if (mist) {
            const foe = enemyOf(chr);
            const cat = st.gem === GEM.poison ? 10004 : 10007;
            used = now - st.at >= ART_MIST_ON_MS || (foe !== null && statusCategories(foe.chr).has(cat));
        }
        if (used) st.mistUsedAt = now;
        const forced = artForce !== null && now < artForce.until && arts.includes(artForce.gem);
        const want = forced ? artForce.gem : pickArt(chr, arts, st);
        // What it saw, reported when that changes, for checking the rules against a real fight.
        const t = enemyOf(chr);
        const seen = t === null ? 'no-enemy' : `enemy:${t.chr}:${Math.round(t.hp[0] / t.hp[1] * 20) / 20}`;
        if (seen !== st.seen) {
            st.seen = seen;
            emit('art-sees', { chr: key, seen, want, watched: watched.length, fp });
        }
        // Diagnostic: every 2 s while anything is watched, what each watched entry looks like.
        if (watched.length > 0 && now - (st.dbgAt || 0) > 2000) {
            st.dbgAt = now;
            const p = posOf(chr);
            emit('art-debug', { chr: key, lock: (chr.add(0x6b0).readU32() >>> 0).toString(16),
                watched: watched.slice(0, 4).map((w) => {
                    const hp = hpOf(w);
                    const q = posOf(w);
                    return { w: w.toString(), hp, d: p === null || q === null ? null
                        : Math.round(Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) * 10) / 10 };
                }) });
        }
        if (want !== st.gem && (used || st.gem === undefined || now - st.at >= ART_HOLD_MS)) {
            emit('art', { chr: key, from: st.gem === undefined ? null : st.gem, to: want, fp, mistUsed: used });
            const swapped = st.gem !== undefined;
            st.gem = want;
            st.at = now;
            artNow.set(key, want);
            applyEquip(chr, false, index);
            // A swap costs the grip (user rule 2026-10-06); brain_turtles.lua drops and retakes it
            // when GetArtsID changes. Writing ChrAsm arm style (+0x8) here does not: measured
            // 2026-10-06, GetWeaponBothHandState stayed two-handed after it.
            void swapped;
        }
        st.fp = fp;
        artState.set(key, st);
    });
}

// A real player: a PlayerIns with no NpcParam (ChrIns +0x60). An NPC built as a PlayerIns, ours
// included, carries one (the turtles read 523590024); the PlayerIns constructor sets it only
// when the creation npcParamId is nonzero (docs/er-mechanics/status.md s2).
function isHuman(chr) {
    return rttiName(chr.readPointer()) === '.?AVPlayerIns@CS@@' && chr.add(0x60).readS32() <= 0;
}

// The player's Mimic's Veil (SpEffect 503040, held until the veil breaks), for brain_turtles.lua:
// while it is up, every turtle puts on its own and does nothing else. Not quiet, so they replan
// the moment it changes.
const MIMIC_VEIL = 503040;
let veilSent = null;

// The player crouching, for brain_turtles.lua (user rule 2026-10-06: crouch with them when no
// enemy is on anyone). SpEffect 8001 "[HKS] Is Stealth" is on while crouched: measured
// 2026-10-06, it appeared with the crouch animations 10390000 / 10300000 and left with the
// stand-up 10390001, together with 150 and 373. Not quiet, so they react at once.
// 8001 alone flapped while moving crouched (measured 2026-10-06: tt_crouch flipped every 2-3 s
// with the player in crouch-move animation 10320110), so the crouch animation family 103xxxxx
// counts too, minus the stand-up 10390001, and a change must hold CROUCH_STEADY_MS first.
const STEALTH = 8001;
const CROUCH_STEADY_MS = 400;
let crouchSent = null;
let crouchSeen = null;

function crouchCheck() {
    if (playerChr === null) return;
    const st = attackerState(playerChr);
    const anim = st.last === undefined ? -1 : st.last.anim % 100000000;
    const family = anim >= 10300000 && anim <= 10399999 && anim !== 10390001;
    const on = speffectIds(playerChr).includes(STEALTH) || family ? 1 : 0;
    const now = Date.now();
    if (crouchSeen === null || crouchSeen.on !== on) crouchSeen = { on, since: now };
    if (on === crouchSent || now - crouchSeen.since < CROUCH_STEADY_MS) return;
    crouchSent = on;
    emit('world', { facts: { tt_crouch: on }, quiet: false, anim });
}

// An enemy whose AI has picked a turtle (user rule 2026-10-06: a turtle an enemy locks onto is free
// to engage). The enemy's TARGET_ENE_0 is the highest-scoring node of its AI's enemy search list
// (bd ai-target-ene0-chain-1171-static-2026-10-06): manipulator at EnemyIns +0x580 (PlayerIns
// +0x588) -> AiIns +0xc0 -> CSTargetingSystem +0xc950 -> list head +0x20, node next +0x10, score
// float +0x38, kind s32 +0x34 (1 is a character), its FieldInsHandle at +0x68. tt_targeted_p names
// each picked turtle by its distance to the player. Not quiet, so they react at once.
const AI_ENEMY_NO_SCORE = -99999.9;
let targetedSent = null;

function aiTargetHandle(chr) {
    const isPlayer = rttiName(chr.readPointer()) === '.?AVPlayerIns@CS@@';
    const man = chr.add(isPlayer ? 0x588 : 0x580).readPointer();
    if (man.isNull()) return null;
    const aiIns = man.add(0xc0).readPointer();
    if (aiIns.isNull()) return null;
    const ts = aiIns.add(0xc950).readPointer();
    if (ts.isNull()) return null;
    let best = null;
    let bestScore = AI_ENEMY_NO_SCORE;
    let guard = 0;
    for (let n = ts.add(0x20).readPointer(); !n.isNull() && guard < 64; n = n.add(0x10).readPointer(), guard++) {
        const s = n.add(0x38).readFloat();
        if (s > bestScore) {
            bestScore = s;
            best = n;
        }
    }
    if (best === null || best.add(0x34).readS32() !== 1) return null;
    return best.add(0x68).readU64();
}

function targetedCheck() {
    if (playerChr === null) return;
    const me = posOf(playerChr);
    if (me === null) return;
    const squad = squadChrs();
    const out = [];
    const who = [];
    for (const c of squad) {
        const h = c.add(8).readU64();
        const by = watched.find((e) => {
            try {
                const t = aiTargetHandle(e);
                return t !== null && t.equals(h);
            } catch (err) {
                return false;
            }
        });
        if (by === undefined) continue;
        const p = posOf(c);
        if (p === null) continue;
        out.push(Math.round(Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2]) * 100) / 100);
        who.push([c.toString(), by.toString()]);
    }
    const sent = JSON.stringify(who);
    if (sent === targetedSent) return;
    targetedSent = sent;
    emit('world', { facts: { tt_targeted_p: out }, quiet: false });
    emit('targeted', { who });
}

function veilCheck() {
    try {
        crouchCheck();
    } catch (e) {
        emit('hook-error', { where: 'crouch', error: e.message });
    }
    try {
        targetedCheck();
    } catch (e) {
        emit('hook-error', { where: 'targeted', error: e.message });
    }
    if (playerChr === null) return;
    const on = speffectIds(playerChr).includes(MIMIC_VEIL) ? 1 : 0;
    if (on === veilSent) return;
    veilSent = on;
    emit('world', { facts: { tt_veil: on }, quiet: false });
    emit('veil', { on });
}

// One group per summon in the per-player tag list, the one co-op phantoms get, each with its own
// name and HP bar (bd npc-summons-in-friendly-tag-list-via-producer-probes-1171-2026-10-06). Its
// producer 0x140778200 rebuilds all 7 entries (CSFeManImp +0x5c60, stride 0xb0) every frame, slot
// i from GetFieldInsHandleByMemberIndex(i) resolved to a ChrIns, HP from that ChrIns and the name
// from GetChrName -- so nameHook above names them. Nothing is added to PartyMemberInfo: AddMember
// is real party state (player limit, red/white counts, join notice) and a risk to Seamless's sync.
// Instead two probes inside the producer raise its member count by our summons and hand slots
// real..real+n-1 our handles (ChrIns +0x8), and the voice-chat lookup it calls, which reads
// PlayerIns +0x6b8 without a null check, is told "no player" for them. All three sites
// byte-checked in eldenring-deobf-1.17.1.bin; no branch lands inside the probed bytes.
const TAG_COUNT = va('0x140778292');      // mov [rsp+0x50],eax; eax = players in the world
const TAG_HANDLE = va('0x14077851c');     // after the handle call; r15d = index, [rbp-0x78] = handle
const TAG_VOICE = va('0x140cbbc50');      // (.., .., PlayerIns*); returns 0 when r8 is null
const CS_FE_MAN = va('0x143d6f8f0');
let tagHandles = [];
let tagReal = 0;
let tagSeen = null;

function refreshTagHandles() {
    // A summon at 0 HP still passes isAlive (measured: Donnie's entry held hp 0), so drop it here.
    tagHandles = squadChrs().filter((c) => { const h = hpOf(c); return h !== null && h[0] > 0; })
        .map((c) => c.add(8).readU64());
}

const tagHooks = [
    Interceptor.attach(TAG_COUNT, function () {
        tagReal = this.context.rax.toInt32();
        if (tagHandles.length > 0) this.context.rax = ptr(Math.min(7, tagReal + tagHandles.length));
    }),
    Interceptor.attach(TAG_HANDLE, function () {
        const k = this.context.r15.toInt32() - tagReal;
        if (k >= 0 && k < tagHandles.length) this.context.rbp.sub(0x78).writeU64(tagHandles[k]);
    }),
    Interceptor.attach(TAG_VOICE, {
        onEnter(args) {
            if (args[2].isNull() || tagHandles.length === 0) return;
            const h = args[2].add(8).readU64();
            if (tagHandles.some((t) => t.equals(h))) args[2] = NULL;
        },
    }),
];

// Their names drew red: the tag colour is RoleParam.roleNameColor of row vowType*10000 + chrType,
// and our summons are chrType 5 (Npc), whose row says 2 (hostile); the host, white phantoms and
// summoned NPCs say 1 (bd npc-summon-tag-name-colour-is-rolenamecolor-1171-2026-10-06). The
// producer calls GetRoleNameColor at 0x140778802 with the ChrIns in r15 and stores al at
// 0x140778807 (mov [rsp+0x33],al); for ours, al becomes 1 there. No game state is written.
const TAG_COLOUR = va('0x140778807');
tagHooks.push(Interceptor.attach(TAG_COLOUR, function () {
    if (tagHandles.length === 0) return;
    const chr = this.context.r15;
    if (squadChrs().some((c) => c.equals(chr))) this.context.rax = this.context.rax.and(ptr('0xffffffffffffff00')).or(1);
}));

// A second, red name and HP bar showed over them when hit: the enemy tag list (CSFeManImp
// +0x59f0, 8 x 0x40), which CSChrDamageModule::UpdateUI 0x1404497e0 feeds through
// SetEntityHealthBarDamage at 0x140449b43 (bd npc-summon-enemy-damage-tag-producer-1171-2026-10-06).
// At 0x140449a4e, r12 = 1 makes the next jnz go to 0x140449b55, past that call; rdi is the damage
// module, whose owner ChrIns is at +0x8. Nothing after reads r12 on that path.
const ENEMY_BAR_SKIP = va('0x140449a4e');
tagHooks.push(Interceptor.attach(ENEMY_BAR_SKIP, function () {
    if (tagHandles.length === 0) return;
    const owner = this.context.rdi.add(8).readPointer();
    if (squadChrs().some((c) => c.equals(owner))) this.context.r12 = ptr(1);
}));

// The entries our summons landed in, reported when they change: handle +0x98, HP +0x7c, shown
// as an overhead tag +0x10 or docked off screen +0x12.
function tagCheck() {
    const fe = CS_FE_MAN.readPointer();
    if (fe.isNull()) return;
    const rows = [];
    for (let i = 0; i < 7; i++) {
        const e = fe.add(0x5c60 + i * 0xb0);
        const h = e.add(0x98).readU64();
        if (i >= tagReal + tagHandles.length) continue;
        rows.push({ slot: i, ours: tagHandles.some((t) => t.equals(h)), hp: e.add(0x7c).readS32(),
            tag: e.add(0x10).readU8(), docked: e.add(0x12).readU8(), team: e.add(0xa2).readU8(),
            colour: e.add(0x13).readU8() });
    }
    const seen = JSON.stringify(rows.map((r) => [r.slot, r.tag, r.docked, r.team, r.colour]));
    if (seen === tagSeen) return;
    tagSeen = seen;
    emit('tag-list', { real: tagReal, ours: tagHandles.length, rows });
}

// A turtle with a status to counter, for brain_turtles.lua: running poison or rot (a live entry
// of category 10004 or 10005), or a gauge at least STATUS_FILL emptied
// towards a proc. The gauges are CSChrResistModule (ChrIns modules +0x20): gauge[7] at +0x10 and
// resistance[7] at +0x2c, s32, order poison, rot, bleed, death blight, frost, sleep, madness; a
// gauge counts down from the resistance and procs below 1 (docs/er-mechanics/status.md s2).
// The Lua side cannot be told which character it is, so each request names its turtle by its
// distance to the player and its HP share, both of which the brain can read for itself.
const STATUS_FILL = 0.5;
const STATUS_CHECK_MS = 400;
const RESIST_GAUGE = 0x10;
const RESIST_MAX = 0x2c;
// Gauge index -> bolus, for build-up: poison, rot and bleed are the ones stocked.
const GAUGE_CURE = { 0: 900, 1: 940, 2: 910 };
const RUNNING_CURE = { 10004: 900, 10005: 940 };
let statusAt = 0;
let statusSent = null;

function gauges(chr) {
    const resist = chr.add(0x190).readPointer().add(0x20).readPointer();
    const out = [];
    for (let i = 0; i < 7; i++) {
        out.push([resist.add(RESIST_GAUGE + i * 4).readS32(), resist.add(RESIST_MAX + i * 4).readS32()]);
    }
    return out;
}

function statusCheck() {
    const now = Date.now();
    if (now - statusAt < STATUS_CHECK_MS || playerChr === null) return;
    statusAt = now;
    const me = posOf(playerChr);
    if (me === null) return;
    const flat = [];
    const report = [];
    for (const c of squadChrs()) {
        const hp = hpOf(c);
        const p = posOf(c);
        if (hp === null || hp[0] <= 0 || p === null) continue;
        let item = null;
        let why = null;
        for (const cat of statusCategories(c)) {
            if (RUNNING_CURE[cat] !== undefined) {
                item = RUNNING_CURE[cat];
                why = `running:${cat}`;
                break;
            }
        }
        if (item === null) {
            const g = gauges(c);
            let worst = STATUS_FILL;
            for (const [i, cure] of Object.entries(GAUGE_CURE)) {
                const [left, max] = g[i];
                const filled = max > 0 ? 1 - left / max : 0;
                if (filled >= worst) {
                    worst = filled;
                    item = cure;
                    why = `buildup:${i}:${Math.round(filled * 100)}%`;
                }
            }
        }
        if (item === null) continue;
        const d = Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2]);
        flat.push(Math.round(d * 10) / 10, Math.round(hp[0] / hp[1] * 100) / 100, item);
        report.push({ chr: c.toString(), item, why });
    }
    // Re-sent every check while anyone needs a cure, since distances move; reported on change.
    const sent = JSON.stringify(report.map((r) => [r.chr, r.item]));
    const changed = sent !== statusSent;
    if (!changed && flat.length === 0) return;
    statusSent = sent;
    emit('world', { facts: { tt_cure: flat }, quiet: true });
    if (changed) emit('status', { report });
}

// Reload the current map in place, as the map events' area reload does: TriggerAreaReload(false),
// 1.16.2 0x1405f2890 = 1.17.x 0x1405f36e0 (bytes match). With false it also runs the event-flag
// manager's reload pass first. Requested by the reloadArea RPC and run here, on the game's frame.
const TRIGGER_AREA_RELOAD = new NativeFunction(va('0x1405f36e0'), 'void', ['uint8']);
let reloadPending = false;

// A grace rest's world reset without the grace: ResetWorld() (1.16.2 0x1405f35c0 = 1.17.1
// 0x1405f4410) drops the saved records of dead map characters and sets WorldChrMan +0x1e524 to 1,
// and the engine then despawns, respawns and restarts the map events over the next frames. The
// rest then clears world-time +0xec and refills flasks (BonfirelikeRecovery) and items from the
// chest. See bd grace-rest-recipe-1171-2026-10-06. Requested by the graceRest RPC.
const GR_WORLD_CHR_MAN = va('0x143d69ff8');
const GR_CS_EVENT_MAN = va('0x143d6c768');
const GR_RESET_WORLD = new NativeFunction(va('0x1405f4410'), 'void', []);
const GR_RECOVERY = new NativeFunction(va('0x1405f4170'), 'void', []);
const GR_REPLENISH = new NativeFunction(va('0x14024dff0'), 'void', []);
const WCM_MAIN_PLAYER = 0x1e508;
const WCM_RESPAWN_STATE = 0x1e524;
let gracePending = false;
let graceWatch = -1;

function graceRestReset() {
    const wcm = GR_WORLD_CHR_MAN.readPointer();
    if (wcm.isNull()) return { ok: false, why: 'no WorldChrMan' };
    if (wcm.add(WCM_MAIN_PLAYER).readPointer().isNull()) return { ok: false, why: 'no player' };
    const state = wcm.add(WCM_RESPAWN_STATE).readS32();
    if (state !== 0) return { ok: false, why: 'respawn already running', state };
    GR_RESET_WORLD();
    const em = GR_CS_EVENT_MAN.readPointer();
    if (!em.isNull()) {
        const wat = em.add(0xc0).readPointer();
        if (!wat.isNull()) wat.add(0xec).writeU8(0);
    }
    GR_RECOVERY();
    GR_REPLENISH();
    return { ok: true, stateAfter: wcm.add(WCM_RESPAWN_STATE).readS32() };
}

function graceWatchTick() {
    const wcm = GR_WORLD_CHR_MAN.readPointer();
    if (wcm.isNull()) return;
    const s = wcm.add(WCM_RESPAWN_STATE).readS32();
    if (s !== graceWatch) {
        emit('grace-rest-state', { state: s });
        graceWatch = s === 0 ? -1 : s;
        // State 1 despawns every map character, the squad included (measured 2026-10-06: the
        // spawn was "removed" 75 ms after the reset), so summon it again once the respawn is done.
        if (s === 0 && !despawnedEarlier()) {
            emit('grace-resummon', { why: 'reset-world' });
            rpc.exports.respawn();
        }
    }
}

const hook = Interceptor.attach(FRAME_TICK, {
    onEnter() {
        for (const c of mimics) {
            try {
                if (isAlive(c).alive) mimicTeam(c);
            } catch (e) {
                emit('hook-error', { where: 'mimic-team', error: e.message });
            }
        }
        if (gracePending) {
            gracePending = false;
            try {
                const r = graceRestReset();
                emit('grace-rest', r);
                if (r.ok) graceWatch = r.stateAfter;
            } catch (e) {
                emit('hook-error', { where: 'grace-rest', error: e.message });
            }
        }
        if (graceWatch !== -1) {
            try {
                graceWatchTick();
            } catch (e) {
                emit('hook-error', { where: 'grace-watch', error: e.message });
                graceWatch = -1;
            }
        }
        if (reloadPending) {
            reloadPending = false;
            try {
                TRIGGER_AREA_RELOAD(0);
                emit('area-reload', { ok: true });
            } catch (e) {
                emit('hook-error', { where: 'area-reload', error: e.message });
            }
        }
        sampleTarget();
        try {
            statusCheck();
        } catch (e) {
            emit('hook-error', { where: 'status', error: e.message });
        }
        try {
            refreshTagHandles();
            tagCheck();
        } catch (e) {
            emit('hook-error', { where: 'tag-list', error: e.message });
        }
        try {
            artPlanner();
        } catch (e) {
            emit('hook-error', { where: 'arts', error: e.message });
        }
        try {
            stuckCheck();
        } catch (e) {
            emit('hook-error', { where: 'stuck', error: e.message });
        }
        try {
            veilCheck();
        } catch (e) {
            emit('hook-error', { where: 'veil', error: e.message });
        }
        try {
            volleyCheck();
        } catch (e) {
            emit('hook-error', { where: 'volley', error: e.message });
        }
        try {
            readSwings();
        } catch (e) {
            emit('hook-error', { where: 'read-swings', error: e.message });
        }
        addHeals();
        const now = Date.now();
        if (now - heartbeatAt >= HEARTBEAT_MS) {
            heartbeatAt = now;
            heartbeat();
        }
        if (now - liftCheckAt >= LIFT_CHECK_MS) {
            liftCheckAt = now;
            liftCheck();
        }
        if (extrasToRemove.length > 0) {
            const gone = extrasToRemove.splice(0);
            for (const c of gone) {
                try {
                    if (unsummon(c)) emit('replaced', { chr: c.toString(), via: 'NotifyBuddyUnsummon', extra: true });
                } catch (e) {
                    emit('hook-error', { where: 'unsummon-extra', error: e.message });
                }
            }
        }
        // One extra per frame once the first is up, each EXTRA_SPACING m further to the side.
        if (extrasWanted > 0 && spawned !== null && isAlive(spawned).alive) {
            extrasWanted -= 1;
            try {
                const pose = playerPose();
                if (pose.why === undefined) {
                    const n = extras.length + 1;
                    const r = spawn(pose, (n % 2 === 1 ? 1 : -1) * EXTRA_SPACING * Math.ceil(n / 2));
                    if (r.why !== undefined) {
                        emit('spawn-refused', { why: r.why, extra: true });
                    } else if (!r.chr.isNull()) {
                        extras.push(r.chr);
                        rememberExtras();
                        mimicTeam(r.chr);
                        dressNew(r.chr, n);
                        emit('spawned', { chr: r.chr.toString(), extra: n, at: r.at, path: 'summon', net: r.net });
                    }
                }
            } catch (e) {
                emit('spawn-fault', { error: e.message, extra: true });
            }
        }
        if (done) return;
        if (pendingRemove !== null) {
            const old = pendingRemove;
            pendingRemove = null;
            try {
                const wcm = WORLD_CHR_MAN.readPointer();
                if (!wcm.isNull() && isAlive(old).alive) {
                    if (pendingRemovePath === 'summon') {
                        NOTIFY_BUDDY_UNSUMMON(wcm.add(SUMMON_BUDDY_MANAGER).readPointer(), old.add(8));
                        emit('replaced', { chr: old.toString(), via: 'NotifyBuddyUnsummon' });
                    } else {
                        removeChrIns(wcm, old);
                        emit('replaced', { chr: old.toString() });
                    }
                }
            } catch (e) {
                emit('hook-error', { where: 'replace', error: e.message });
            }
            stableSince = null;
            if (holdSpawn) {
                holdSpawn = false;
                done = true;
            }
            return;
        }
        const pose = playerPose();
        if (pose.why !== undefined) {
            if (pose.why !== lastWhy) emit('waiting', { why: pose.why });
            lastWhy = pose.why;
            stableSince = null;
            return;
        }
        if (stableSince === null) stableSince = now;
        if (now - stableSince < STABLE_MS) return;
        done = true;
        try {
            const r = cfg.path === 'summon' ? spawn(pose) : spawnDynamic(pose);
            if (r.why !== undefined) {
                emit('spawn-refused', { why: r.why });
                return;
            }
            if (!r.chr.isNull()) {
                spawned = r.chr;
                teamSpawned = null;
                homeSpawned = null;
                equipApplied = null;
                spawnedAt = Date.now();
                spawnedPath = cfg.path;
                rememberSpawn(r.chr, cfg.path);
                // Same frame as creation, ahead of the first part load. The team too: the summon is
                // created on team 6 and attacked the player until the later pass fixed it.
                applyTeam(r.chr);
                dressNew(r.chr, 0);
                if (cfg.path === 'summon') extrasWanted = Math.max(0, (cfg.count || 1) - 1);
            }
            emit(r.chr.isNull() ? 'spawn-null' : 'spawned', {
                chr: r.chr.toString(), npcParam: cfg.npcParam, think: cfg.think, charaInit: cfg.charaInit,
                at: r.at, block: r.block, center: r.center, ownerEventId: r.ownerEventId, player: pose.pos,
                path: cfg.path, steamId: r.steamId, net: r.net,
            });
        } catch (e) {
            emit('spawn-fault', { error: e.message });
        }
    },
});

// Every HEARTBEAT_MS of game frames: god mode re-applied, the player's HP, and whether the spawn
// still exists and where. Run from the frame tick, on the game thread.
const HEARTBEAT_MS = 1000;
let heartbeatAt = 0;
function heartbeat() {
    const flags = applyGod();
    const pose = playerPose();
    playerChr = pose.player || null;
    const fields = {
        god: cfg.god, godFlags: flags, playerHp: pose.player ? hpOf(pose.player) : null,
        playerTeam: pose.player ? pose.player.add(TEAM_TYPE).readU8() : null,
        player: pose.pos || null, chr: null, alive: false,
        playerFlasks: pose.player ? watchGraceRest(pose.player) : null,
        playerAnim: pose.player ? attackerState(pose.player).last : null,
    };
    if (spawned !== null) {
        try {
            const state = isAlive(spawned);
            if (state.alive) {
                applyName(spawned, 0);
                tendFood(spawned);
            }
            const physics = state.alive ? physicsOf(spawned) : null;
            const p = physics === null ? null : physics.add(0x70);
            Object.assign(fields, {
                chr: spawned.toString(), alive: state.alive, cls: state.cls, ageMs: Date.now() - spawnedAt,
                pos: p === null ? null : [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()],
                npcHp: state.alive ? hpOf(spawned) : null,
                team: state.alive ? applyTeam(spawned) : null,
                home: state.alive ? applyHome(spawned, pose.pos) : null,
                equip: state.alive && cfg.dress !== false ? applyEquip(spawned, false, 0) : null,
                chrType: state.alive ? spawned.add(CHR_TYPE).readS32() : null,
            });
        } catch (e) {
            fields.error = e.message;
        }
    }
    fields.extras = extras.map((c, i) => {
        try {
            if (!isAlive(c).alive) return { chr: c.toString(), alive: false };
            applyName(c, i + 1);
            tendFood(c);
            return { chr: c.toString(), alive: true, hp: hpOf(c), team: applyTeam(c),
                home: applyHome(c, pose.pos), equip: cfg.dress !== false && applyEquip(c, false, i + 1) !== null };
        } catch (e) {
            return { chr: c.toString(), error: e.message };
        }
    });
    emit('npc', fields);
}

// Is the player standing on a lift. Reported to the lab as a world fact (kind 'world'), which sets
// LAB_WORLD.player_on_lift in the AI state.
//
// What the player stands on: physics module +0x240, a field-ins handle (1.16.2 FUN_1403ff330, the
// per-frame ground update, hands the asset it resolves to UpdateFallDamageByGeomIns; same code at
// 1.17.1 0x1403ff620), with +0x92 standing on solid ground. The low dword's top nibble is the kind:
// 0 map collision, 6 an asset, 8 an asset's collision part. Measured 2026-10-05 in Raya Lucaria:
// on a lift 0x0e000000_61201a26 (kind 6), stepping off 0x0e000000_01200041 (kind 0).
//
// Which assets are lifts: argument 3 of the common EMEVD lift events 90005500/90005501 (and the
// 9000550x variants), 63 entity ids across the base game, read out of the extracted event files
// (bd lift-detection-ground-handle-1171-2026-10-05). Their live handles come from CSWorldGeomMan
// (global 0x143d6dc18): +0x20 a std::map of loaded map blocks (node: +0x19 nil flag, +0x20 map id,
// +0x28 block data), and in each block data +0x340 a std::map of entity id (+0x20) -> handle (+0x28)
// (1.17.1 0x1406d38f0, 0x1406a76d0). Matched on the handle's index (low 20 bits) and block (high
// dword), which holds for both kind 6 and kind 8.
const LIFT_ENTITY_IDS = new Set([
    10001510, 10001515, 10001520, 11001510, 11001515, 11001520, 11001525, 11001530, 11001535, 11001610,
    11051525, 11051530, 11051535, 11051610, 12011510, 12011515, 12011520, 12011525, 12021520, 12021525,
    12051510, 12071515, 12071525, 13001510, 13001515, 13001520, 13001525, 13001530, 14001510, 14001515,
    14001520, 15001520, 15001525, 15001620, 15001625, 16001510, 16001520, 16001525, 16001530, 18001510,
    18001515, 20001510, 20001515, 20001520, 20001525, 20011510, 20011515, 20011520, 20011525, 20011530,
    20011535, 21001510, 21001515, 21001520, 21001525, 21001530, 21001535, 21011510, 21011515, 21011520,
    21011525, 21011530, 21021510,
]);
const WORLD_GEOM_MAN = va('0x143d6dc18');
const PHYS_GROUND_HANDLE = 0x240;
const PHYS_STANDING = 0x92;
const LIFT_REFRESH_MS = 3000;

// In-order walk of an MSVC std::map given its head node; visit(node) per element. Bounded, since a
// map being rebuilt under us must not spin the agent.
function walkMap(head, visit) {
    let n = 0;
    const stack = [];
    let node = head.add(0x8).readPointer();   // root
    while ((stack.length > 0 || node.add(0x19).readU8() === 0) && n < 20000) {
        if (node.add(0x19).readU8() === 0) {
            stack.push(node);
            node = node.readPointer();          // left
        } else {
            node = stack.pop();
            visit(node);
            n += 1;
            node = node.add(0x10).readPointer(); // right
        }
    }
    return n;
}

function handleKey(lo, hi) {
    return `${hi >>> 0}:${(lo & 0xfffff) >>> 0}`;
}

let liftHandles = new Map();   // handleKey -> entity id
let liftRefreshedAt = 0;

function refreshLiftHandles() {
    const found = new Map();
    const man = WORLD_GEOM_MAN.readPointer();
    if (man.isNull()) return found;
    walkMap(man.add(0x20).readPointer(), (block) => {
        const data = block.add(0x28).readPointer();
        if (data.isNull()) return;
        walkMap(data.add(0x340).readPointer(), (e) => {
            const id = e.add(0x20).readU32();
            if (!LIFT_ENTITY_IDS.has(id)) return;
            found.set(handleKey(e.add(0x28).readU32(), e.add(0x2c).readU32()), id);
        });
    });
    return found;
}

let onLift = null;
let liftError = null;
let liftOffSent = null;
const liftGround = new Map();
// Every LIFT_CHECK_MS of game frames, from the frame tick.
const LIFT_CHECK_MS = 250;
let liftCheckAt = 0;
function liftCheck() {
    try {
        const now = Date.now();
        if (now - liftRefreshedAt > LIFT_REFRESH_MS) {
            const before = liftHandles.size;
            liftHandles = refreshLiftHandles();
            liftRefreshedAt = now;
            if (liftHandles.size !== before) {
                emit('lifts', { count: liftHandles.size, handles: Object.fromEntries(liftHandles) });
            }
        }
        const pose = playerPose();
        if (!pose.player) return;
        const physics = physicsOf(pose.player);
        if (physics === null) return;
        const lo = physics.add(PHYS_GROUND_HANDLE).readU32();
        const hi = physics.add(PHYS_GROUND_HANDLE + 4).readU32();
        const kind = lo >>> 28;
        const standing = physics.add(PHYS_STANDING).readU8() !== 0;
        // Airborne (a jump, a fall) keeps the last answer rather than flapping off and on.
        if (!standing) return;
        const lift = (kind === 6 || kind === 8) ? liftHandles.get(handleKey(lo, hi)) : undefined;
        const now_on = lift !== undefined;
        if (now_on !== onLift) {
            onLift = now_on;
            emit('world', { facts: { player_on_lift: now_on }, lift: lift || null,
                ground: `0x${(hi >>> 0).toString(16)}_${(lo >>> 0).toString(16)}`, groundKind: kind });
        }
        // The turtles still off the player's lift, by their distance to the player, for
        // brain_turtles.lua TT_on_lift: one standing on the same ground handle is aboard,
        // however far from the player (measured 2026-10-06: they stopped 1.8-2 m away on the
        // lift and kept re-boarding against a fixed radius).
        const off = [];
        if (now_on) {
            const me = posOf(pose.player);
            for (const c of squadChrs()) {
                const ph = physicsOf(c);
                const q = posOf(c);
                if (ph === null || q === null || me === null) continue;
                // Same lift, not same handle: a lift's asset and its collision parts (kinds 6 and
                // 8) carry different handles, and liftHandles maps each to the lift's entity id.
                const tlo = ph.add(PHYS_GROUND_HANDLE).readU32();
                const thi = ph.add(PHYS_GROUND_HANDLE + 4).readU32();
                const same = liftHandles.get(handleKey(tlo, thi)) === lift || (tlo === lo && thi === hi);
                const d = Math.round(Math.hypot(q[0] - me[0], q[1] - me[1], q[2] - me[2]) * 100) / 100;
                if (!same) {
                    off.push(d);
                    liftGround.set(c.toString(), `0x${(thi >>> 0).toString(16)}_${(tlo >>> 0).toString(16)} kind ${tlo >>> 28} d ${d}`);
                }
            }
        }
        const offKey = off.length;
        if (offKey !== liftOffSent || off.length > 0) {
            if (offKey !== liftOffSent) emit('lift-squad', { player: `0x${(hi >>> 0).toString(16)}_${(lo >>> 0).toString(16)}`,
                lift: lift || null, off: Object.fromEntries(liftGround) });
            liftOffSent = offKey;
            emit('world', { facts: { tt_lift_off_p: off }, quiet: true });
        }
        liftGround.clear();
        liftError = null;
    } catch (e) {
        if (e.message !== liftError) {
            liftError = e.message;
            emit('lift-error', { error: e.message });
        }
    }
}

emit('armed', { cfg: cfg });

rpc.exports = {
    // Remove the current spawn, if alive, on the next frame, and spawn a fresh one in front of the
    // player once the pose has been stable for STABLE_MS. `overrides` changes cfg first.
    respawn(overrides) {
        // A summon-path spawn is removed through NotifyBuddyUnsummon, never RemoveChrIns: measured
        // 2026-10-05, RemoveChrIns on one crashed the game 0.5 s later (access violation at
        // game+0x4c2b5c reading a null ChrIns +0x190, under the SummonBuddyManager update at
        // game+0x4b8b7e, which still held its summon-group entry).
        if (overrides) Object.assign(cfg, overrides);
        if (spawned !== null && isAlive(spawned).alive) {
            pendingRemove = spawned;
            pendingRemovePath = spawnedPath;
        }
        dropExtras();
        nameplates.clear();
        namesByHandle.clear();
        foodGiven.clear();
        foodEatenAt.clear();
        foodBefore.clear();
        spawned = null;
        stableSince = null;
        holdSpawn = false;
        done = false;
        setDespawned(false);
        return cfg;
    },
    despawn() {
        setDespawned(true);
        if (spawned !== null && isAlive(spawned).alive) {
            pendingRemove = spawned;
            pendingRemovePath = spawnedPath;
        }
        dropExtras();
        spawned = null;
        holdSpawn = pendingRemove !== null;
        done = !holdSpawn;
        return true;
    },
    god(on) {
        cfg.god = !!on;
        return applyGod();
    },
    // Floats of the spawn's ChrIns +0x80..+0xb0 (chunk position, initial position, initial
    // orientation) and its physics position, for finding what the AI's POINT_INITIAL reads.
    // Both ChrAsm copies of the spawn: arm style, selected slots, gaitem handles, param ids.
    peekAsm() {
        if (spawned === null || !isAlive(spawned).alive) return null;
        const read = (asm) => {
            const u32 = (o, n) => Array.from({ length: n }, (_, i) => asm.add(o + i * 4).readS32());
            return { at: asm.toString(), armStyle: asm.add(0x8).readS32(), slots: u32(0xc, 6),
                gaitem: u32(0x24, 22), param: u32(CHR_ASM_PARAM_IDS, 22) };
        };
        return { ins: read(spawned.add(CHR_ASM_PTR).readPointer()),
            pgd: read(spawned.add(PLAYER_GAME_DATA).readPointer().add(PGD_CHR_ASM)) };
    },
    // The spawn's normal inventory (fromsoftware-rs EquipGameData at PlayerGameData +0x2b0,
    // EquipInventoryData at +0x158 in it): {handle, item, qty} per entry, `stride` bytes apart.
    // What artPlanner sees for each summon: its enemy, distance, FP and the watched list size.
    peekArts() {
        return { watched: watched.length, enemyWhy,
            enemyLocks: watched.map((w) => ({ w: w.toString(), lock: (w.add(0x6b0).readU32() >>> 0).toString(16) })),
            squadHandles: squadChrs().map((c) => (c.add(8).readU32() >>> 0).toString(16)), squad: [spawned].concat(extras).map((c, index) => {
            if (c === null || !isAlive(c).alive) return null;
            const t = enemyOf(c);
            const p = posOf(c);
            const q = t === null ? null : posOf(t.chr);
            return { chr: c.toString(), arts: forSpawn(cfg.arts, index), fp: fpOf(c), now: artNow.get(c.toString()),
                enemy: t === null ? null : { chr: t.chr.toString(), hp: t.hp, npc: t.chr.add(0x60).readS32(),
                    d: q === null || p === null ? null : Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) } };
        }) };
    },
    // Put Ash of War `gem` on every summon whose build lists it, for `ms` (artPlanner then resumes).
    // Each nearby enemy's AI target handle beside the player's and the turtles' handles, to check
    // the targetedCheck chain against a live fight.
    // The game's own display name for a ChrIns (GetChrName, undecorated). The MenuString is leaked
    // on purpose: it is a few bytes and its destructor is not ours to call.
    chrName(addr) {
        const out = Memory.alloc(0x80);
        new NativeFunction(GET_CHR_NAME, 'pointer', ['pointer', 'pointer', 'bool'])(out, ptr(addr), 0);
        const raw = out.readPointer();
        return raw.isNull() ? null : raw.readUtf16String();
    },
    // Event flags (bd great-jar-sign-flags-1171-2026-10-06): CSEventFlagMan* at 0x143d6c4b8,
    // GetEventFlag(man, id) 0x1405fa250, SetEventFlag(man, u32* id, state, caller, netSync)
    // 0x1405d2f60. netSync 0, since the sync list is not safe from this thread. `state` null reads.
    eventFlags(ids, state) {
        const man = va('0x143d6c4b8').readPointer();
        const get = new NativeFunction(va('0x1405fa250'), 'int', ['pointer', 'uint32']);
        const set = new NativeFunction(va('0x1405d2f60'), 'void', ['pointer', 'pointer', 'uint8', 'pointer', 'uint8']);
        const id = Memory.alloc(4);
        const out = {};
        for (const f of ids) {
            const before = get(man, f);
            if (state !== null && state !== undefined) {
                id.writeU32(f);
                set(man, id, state, NULL, 0);
            }
            out[f] = [before, get(man, f)];
        }
        return out;
    },
    reloadArea() {
        reloadPending = true;
        return true;
    },
    // Count already-summoned Mimic Tear turtles as squad again (after a reload of this agent).
    mimicAdopt(addrs) {
        mimics = addrs.map((a) => ptr(a)).filter((c) => isAlive(c).alive);
        mimics.forEach((c, i) => {
            mimicTeam(c);
            dressNew(c, i);
        });
        return mimics.map(String);
    },
    graceRest() {
        gracePending = true;
        return true;
    },
    peekTargets() {
        const hex = (h) => (h === null ? null : h.and(0xffffffff).toString(16));
        return {
            player: playerChr === null ? null : hex(playerChr.add(8).readU64()),
            squad: squadChrs().map((c) => hex(c.add(8).readU64())),
            enemies: watched.map((e) => {
                try {
                    return { chr: e.toString(), npcParam: e.add(0x60).readS32(), target: hex(aiTargetHandle(e)) };
                } catch (err) {
                    return { chr: e.toString(), error: err.message };
                }
            }),
        };
    },
    forceArt(gem, ms) {
        artForce = { gem, until: Date.now() + ms };
        return artForce;
    },
    // Each living summon's status gauges ([left, resistance] x 7) and running status categories.
    peekStatus() {
        return squadChrs().map((c) => ({ chr: c.toString(), gauges: gauges(c),
            running: Array.from(statusCategories(c)) }));
    },
    peekInventory(stride) {
        if (spawned === null || !isAlive(spawned).alive) return null;
        const inv = spawned.add(PLAYER_GAME_DATA).readPointer().add(0x2b0 + 0x158);
        const head = inv.add(0x10).readPointer();
        const len = inv.add(0x18).readU32();
        const out = [];
        for (let i = 0; i < Math.min(len, 64); i++) {
            const e = head.add(i * stride);
            out.push({ at: e.toString(), handle: e.readU32() >>> 0, item: e.add(4).readU32() >>> 0, qty: e.add(8).readU32() });
        }
        return { inv: inv.toString(), capacity: inv.add(0xc).readU32(), len, entries: out };
    },
    // Her lock-on target (PlayerIns +0x6b0 handle, resolved by the engine's GetChrInsFromHandle
    // 0x140508a50, as scripts/frida/target-bars-probe.js does for the player) with its hp, animation
    // and throw module (modules +0x88 -> node +0x10; the node's u32s 0x58..0x7c are dumped because
    // the ThrowNodeState offset is not pinned).
    peekTarget() {
        if (spawned === null || !isAlive(spawned).alive) return null;
        const handlePtr = spawned.add(0x6b0);
        const handle = handlePtr.readU64();
        if ((handle.and(0xffffffff).toNumber() >>> 0) === 0xffffffff) return { handle: null };
        const chr = GET_CHR_FROM_HANDLE(WORLD_CHR_MAN.readPointer(), handlePtr);
        if (chr.isNull()) return { handle: handle.toString(16), chr: null };
        return { handle: handle.toString(16), ...throwState(chr), hp: hpOf(chr), ...attackerState(chr) };
    },
    // Characters within `radius` m of her: npcParam, team, hp, distance, animation, throw state.
    nearby(radius) {
        if (spawned === null || !isAlive(spawned).alive) return null;
        const me = posOf(spawned);
        const out = [];
        let total = 0;
        for (const chr of allChrs()) {
            total++;
            const p = posOf(chr);
            if (p === null) continue;
            const d = Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2]);
            if (d > radius) continue;
            out.push({ chr: chr.toString(), self: chr.equals(spawned), npcParam: chr.add(0x60).readS32(),
                team: chr.add(TEAM_TYPE).readU8(), hp: hpOf(chr), d: Math.round(d * 100) / 100,
                anim: attackerState(chr).last, ...throwState(chr) });
        }
        return { total, near: out };
    },
    // Read-only: `n` qwords at `addr` (hex string), for following pointers out of the Lua state.
    peekQwords(addr, n) {
        const p = ptr(addr);
        return Array.from({ length: n }, (_, i) => p.add(i * 8).readU64().toString(16));
    },
    // The game's own gate on networking a spirit summon (1.17.1 IsSpiritSummmonNetworkingEnabled,
    // static: true only for an Arena main player with QuickmatchManager::SpiritAshesAllowed). A
    // peer drops a summon packet unless this is true on its side, so Seamless must force it.
    summonNetCheck() {
        return { enabled: SUMMON_NET_ENABLED() };
    },
    // Set the spawn's heal count to cfg.heals now (spawn and grace rest do it on their own).
    heals() {
        if (spawned === null || !isAlive(spawned).alive) return null;
        return setHeals(spawned);
    },
    // Queue `qty` heals through the game's inventory add even though the entry exists, to check
    // what the add does to a stack (it should grow it).
    addHealsTest(qty) {
        if (spawned === null || !isAlive(spawned).alive) return null;
        pendingHealAdd = { chr: spawned, qty };
        return { queued: qty };
    },
    // The spawn's own animation state, the same read the incoming-hit record makes of an attacker.
    peekAnim() {
        if (spawned === null || !isAlive(spawned).alive) return null;
        return attackerState(spawned);
    },
    // A CSGaitem entry by handle: its item id and, for a weapon, its gem handle and the gem's item.
    peekGaitem(handle) {
        const imp = va('0x143d6d900').readPointer();
        const entry = (h) => {
            const ins = imp.add(8 + (h & 0xffff) * 8).readPointer();
            if (ins.isNull()) return null;
            return { ins: ins.toString(), handle: ins.add(8).readU32() >>> 0, item: ins.add(0xc).readS32() };
        };
        const e = entry(handle);
        if (e === null || ((handle >>> 28) & 7) !== 0) return e;
        const ins = ptr(e.ins);
        e.gem = ins.add(0x28).readU32() >>> 0;
        e.gemItem = (e.gem & 0x800000) ? (entry(e.gem) || {}).item : null;
        return e;
    },
    peekHome() {
        if (spawned === null || !isAlive(spawned).alive) return null;
        const f = (p, n) => Array.from({ length: n }, (_, i) => p.add(i * 4).readFloat());
        const physics = physicsOf(spawned);
        return { chunk: f(spawned.add(0x80), 4), initial: f(spawned.add(0x90), 4),
            orient: f(spawned.add(0xa0), 4), pos: physics === null ? null : f(physics.add(0x70), 4) };
    },
    // Pointers in the spawn's PlayerIns +0x580..+0x740 whose +0xac (ChrAsm equipment_param_ids[12],
    // the head) reads `head`, and the same search one level down through PlayerGameData (+0x580).
    probeChrAsm(head) {
        if (spawned === null || !isAlive(spawned).alive) return null;
        const hits = [];
        const scan = (base, from, to, label) => {
            for (let o = from; o < to; o += 8) {
                try {
                    const p = base.add(o).readPointer();
                    if (p.compare(ptr('0x10000')) < 0) continue;
                    for (const at of [0xac]) {
                        if (p.add(at).readS32() === head) hits.push({ in: label, off: o, ptr: p.toString() });
                    }
                } catch (e) { /* not a pointer */ }
            }
        };
        scan(spawned, 0x580, 0x740, 'PlayerIns');
        const pgd = spawned.add(0x580).readPointer();
        const inline = [];
        for (let o = 0; o < 0x1000; o += 4) {
            try { if (pgd.add(o).readS32() === head) inline.push(o); } catch (e) { break; }
        }
        return { chr: spawned.toString(), pgd: pgd.toString(), hits, pgdInline: inline };
    },
    // Hex of the main player's CSChrPhysicsModule (ChrIns +0x190 -> +0x68), 0x420 bytes, for
    // diffing the state on a lift against off it.
    dumpPlayerPhysics() {
        const pose = playerPose();
        if (!pose.player) return null;
        const physics = physicsOf(pose.player);
        if (physics === null) return null;
        const bytes = new Uint8Array(physics.readByteArray(0x420));
        return { at: physics.toString(), hex: Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('') };
    },
    // The lift detector's state, for checking it against what the player sees.
    liftState() {
        return { onLift: onLift, liftError: liftError, handles: Object.fromEntries(liftHandles),
            refreshedMsAgo: Date.now() - liftRefreshedAt };
    },
    // Replace the remembered spawn home with [x, y, z] ('spawn' then means this point).
    setHome(xyz) {
        if (spawned === null || !isAlive(spawned).alive) return null;
        homeSpawned = xyz;
        return homeSpawned;
    },
    // The team applyTeam pins a summon-path spawn to (null: the player's), without respawning; a
    // red-sign join (scripts/frida/signs-to-player.js) puts the spawn on 16 and needs it kept there.
    setSummonTeam(t) {
        cfg.summonTeam = t === null || t === undefined || t < 0 ? null : t;
        return cfg.summonTeam;
    },
    // What the AI mods asked for (_lab.lua lab_team / lab_home), applied on the next heartbeat.
    // team null or negative restores the team it spawned with; home is 'spawn', 'self' or 'player'.
    request(r) {
        cfg.team = r.team === null || r.team === undefined || r.team < 0 ? null : r.team;
        cfg.home = ['self', 'player'].includes(r.home) ? r.home : 'spawn';
        // "think:slot=id;think:slot=id/gem", as _lab.lua logs LAB_EQUIP; no gem is -1. The think
        // may carry a summon index, "think.N" (see forSpawn).
        // A face rides the same string as "think:face=<hex>" (_lab.lua lab_face).
        cfg.equip = {};
        cfg.face = {};
        cfg.name = {};
        cfg.arts = {};
        cfg.spells = {};
        for (const item of String(r.equip || '').split(';')) {
            // Memorized spells, "think:spells=id,id,..." (_lab.lua lab_spells), for applySpells.
            const s = /^(\d+(?:\.\d+)?):spells=(\d+(?:,\d+)*)$/.exec(item);
            if (s !== null) {
                cfg.spells[s[1]] = s[2].split(',').map(Number);
                continue;
            }
            const f = /^(\d+(?:\.\d+)?):face=([0-9A-Fa-f]+)$/.exec(item);
            if (f !== null) {
                cfg.face[f[1]] = f[2];
                continue;
            }
            // The Ashes of War its right-hand weapon can switch between, "think:arts=g,g,..."
            // (_lab.lua lab_arts; -1 is the weapon's own skill), for artPlanner.
            const a = /^(\d+(?:\.\d+)?):arts=(-?\d+(?:,-?\d+)*)$/.exec(item);
            if (a !== null) {
                cfg.arts[a[1]] = a[2].split(',').map(Number);
                continue;
            }
            // A name rides it too, as "think:name=<text>" (_lab.lua lab_name).
            const n = /^(\d+(?:\.\d+)?):name=([A-Za-z0-9 ]{1,16})$/.exec(item);
            if (n !== null) {
                cfg.name[n[1]] = n[2];
                continue;
            }
            const m = /^(\d+(?:\.\d+)?):(\w+)=(-?\d+)(?:\/(-?\d+))?$/.exec(item);
            if (m === null) continue;
            (cfg.equip[m[1]] = cfg.equip[m[1]] || {})[m[2]] =
                { id: Number(m[3]), gem: m[4] === undefined ? -1 : Number(m[4]) };
        }
        return { team: cfg.team, home: cfg.home, equip: cfg.equip, name: cfg.name,
            face: Object.fromEntries(Object.entries(cfg.face).map(([k, v]) => [k, v.length / 2])) };
    },
    dispose() {
        hook.detach();
        removeHook.detach();
        damageHook.detach();
        nameHook.detach();
        panelHook.detach();
        for (const h of tagHooks) h.detach();
        Interceptor.revert(REQUEST_WARP);
        Interceptor.revert(DESPAWN_ALL);
    },
};
