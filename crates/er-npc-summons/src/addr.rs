//! Every game address this DLL uses, as a 1.16.2 RVA.
//!
//! The repo convention: an RVA in Rust is 1.16.2 and is translated for the running build at use,
//! through `docs/recon/rva-map-1162-to-1170.verified.tsv` (calls and detours) or
//! `docs/recon/rva-map-1162-to-1170.data.tsv` (globals). Every row below was verified on
//! 2026-10-06; the 1.17.1 runtime address, the verdict and how each pair was found are in
//! `docs/recon/npc-summons-addresses.tsv`. An address with no verified row is refused at runtime,
//! and the feature that needed it logs that it is off.

#![cfg_attr(not(windows), allow(dead_code))]

/// `SummonBuddyManager::CreateSummonChr`, 18 arguments, returns `ChrIns*` (1.17.1 `0x1404baea0`).
pub(crate) const CREATE_SUMMON_CHR: u32 = 0x004b_a980;
/// `ChangeCharacterDisableState(ChrIns*, bool disable)`: the body of EMEVD
/// `ChangeCharacterEnableState`. Writes `ChrSetEntry+0xa` bit 0 and the collision flag; does not
/// touch the load state (1.17.1 `0x1403f6500`, bd `chr-enable-setter-and-join-enable-1171-2026-10-06`).
pub(crate) const CHANGE_DISABLE_STATE: u32 = 0x003f_62d0;
/// `PlaceNPCSummonSign` body: `(SosSignMan*, u8 type, u32* entity, u32* region, i32 summonFlag,
/// BlockId*, i32 dismissFlag, u8 mpRules)` (1.17.1 `0x1406fa7f0`).
pub(crate) const PLACE_NPC_SIGN: u32 = 0x006f_99a0;
/// `CreateSignSfx(SosSignMan*, SosSignData*)`: redraws a sign at its current position (1.17.1
/// `0x1406febe0`).
pub(crate) const CREATE_SIGN_SFX: u32 = 0x006f_dd90;
/// `GetChrInsByEntityId(u32* entity, int, int)` (1.17.1 `0x1405eefd0`).
pub(crate) const GET_CHR_BY_ENTITY: u32 = 0x005e_e180;
/// `GetPhysicsPos(ChrIns*, FloatVector4* out)` (1.17.1 `0x1403f0e20`).
pub(crate) const GET_PHYSICS_POS: u32 = 0x003f_0bf0;
/// `ConvertBlockCoordsToPhysicsCoords(FloatVector4* out, FloatVector4* local, BlockId*)`
/// (1.17.1 `0x14061ef70`).
pub(crate) const BLOCK_TO_PHYSICS: u32 = 0x0061_e120;
/// `IsDrawn(ChrIns*) -> bool` (1.17.1 `0x1403f3930`).
pub(crate) const IS_DRAWN: u32 = 0x003f_3700;
/// `PlayerIns` event id getter used by `BuddyGenerator` for the creator id (1.17.1 `0x140657230`).
pub(crate) const PLAYER_EVENT_ID: u32 = 0x0065_63e0;
/// `PlayerIns` steam id getter used by `BuddyGenerator` (1.17.1 `0x140657160`).
pub(crate) const PLAYER_STEAM_ID: u32 = 0x0065_6310;
/// MSVC `std::list<int>` node insert the buddy list fill uses: `node*(List*, next, prev, int*)`
/// (1.17.1 `0x1404b4fb0`).
pub(crate) const BUDDY_LIST_INSERT: u32 = 0x004b_4a50;

/// Detour: `SummonBuddyManager::BuddyGenerator(SummonBuddyManager*)` (1.17.1 `0x1404bbdd0`).
pub(crate) const BUDDY_GENERATOR: u32 = 0x004b_b8b0;
/// Detour: `GetBuddyListBySpEffectId(mgr, int spEffect, List<int>*)` (1.17.1 `0x1404bce80`).
pub(crate) const GET_BUDDY_LIST: u32 = 0x004b_c960;
/// Detour: `GetBuddyParam(BuddyParamLookup*, int id)`, lookup `{+0 id, +8 row}` (1.17.1
/// `0x140d28010`).
pub(crate) const GET_BUDDY_PARAM: u32 = 0x00d2_6890;
/// Where `BuddyGenerator`'s first-loop `GetBuddyParam` call returns, as an offset into
/// `BuddyGenerator` (1.17.1 `0x1404bbfa3 - 0x1404bbdd0`). The body is `IDENTICAL-WHOLE` between
/// 1.16.2 and 1.17, and the DLL checks the call instruction in front of it before trusting it.
pub(crate) const BUDDY_GENERATOR_LOOP1_RET: usize = 0x1d3;

/// `WorldChrManImp` offsets.
pub(crate) mod world_chr_man {
    /// `PlayerIns*` of the local player.
    pub(crate) const MAIN_PLAYER: usize = 0x1e508;
    /// `SummonBuddyManager*`.
    pub(crate) const SUMMON_BUDDY_MANAGER: usize = 0x1e538;
}

/// `ChrIns` offsets (1.17.1, measured live 2026-10-06).
pub(crate) mod chr_ins {
    /// `FieldInsHandle`, the value `PartyMemberInfo` entries hold.
    pub(crate) const HANDLE: usize = 0x8;
    /// `ChrSetEntry*`.
    pub(crate) const CHR_SET_ENTRY: usize = 0x10;
    /// `BlockId`, with `+0x3c` as the fallback `ChrIns::GetBlockIdOrigin` uses.
    pub(crate) const BLOCK_ID: usize = 0x38;
    pub(crate) const BLOCK_ID_FALLBACK: usize = 0x3c;
    /// Event entity id; the key `GetChrInsByEntityId` and a sign use. Summon-path spawns carry
    /// 35000 (measured).
    pub(crate) const EVENT_ENTITY: usize = 0x1e8;
}

/// `ChrSetEntry` offsets.
pub(crate) mod chr_set_entry {
    pub(crate) const LOAD_STATUS: usize = 0x8;
    /// Bit 0: disabled.
    pub(crate) const CONTROL_FLAGS: usize = 0xa;
}

/// `CSEventMan` -> `+0x60` `CSEventSosSignCtrl*` -> `+0x48` `SosSignMan*`; the sign `std::map`
/// head is at `SosSignMan+0x10`, node `+0x28` is the `SosSignData*`.
pub(crate) mod sign {
    pub(crate) const EVENT_MAN_CTRL: usize = 0x60;
    pub(crate) const CTRL_MAN: usize = 0x48;
    pub(crate) const MAN_MAP_HEAD: usize = 0x10;
    pub(crate) const NODE_DATA: usize = 0x28;
    pub(crate) const NODE_IS_NIL: usize = 0x19;
    pub(crate) const DATA_POS: usize = 0x14;
    pub(crate) const DATA_NPC_ENTITY: usize = 0x234;
    /// `MultiplayType` for a red (hostile) NPC summon sign.
    pub(crate) const TYPE_RED: u8 = 2;
}

/// `GameMan` -> `+0xd90` `PartyMemberInfo*`: six entries at `+0x28`, `0x30` bytes each, `+0`
/// handle, `+0xc` state (4 = joined), `+0x1d` the no-multiplay restriction flag.
pub(crate) mod party {
    pub(crate) const GAME_MAN_PARTY: usize = 0xd90;
    pub(crate) const ENTRIES: usize = 0x28;
    pub(crate) const ENTRY_SIZE: usize = 0x30;
    pub(crate) const ENTRY_COUNT: usize = 6;
    pub(crate) const ENTRY_STATE: usize = 0xc;
}

/// `SummonBuddyManager::RemoveChrIns(SummonBuddyManager*, ChrIns*)`: untracks the warp, then
/// `WorldChrManImp::RemoveChrIns`, then (arena only) packet 79. It reads no summon group, which
/// is why it is the unsummon for a character this DLL has unlinked (1.17.1 `0x1404bbaa0`).
pub(crate) const SUMMON_REMOVE_CHR: u32 = 0x004b_b580;
/// `SummonBuddyWarpManager` untrack, `void(warpManager, FieldInsHandle by value)`; a handle it
/// does not track is a no-op, and it does not check the manager for null (1.17.1 `0x1404c26f0`).
pub(crate) const WARP_UNTRACK: u32 = 0x004c_21d0;
/// `WorldChrManImp::GetChrInsFromHandle(WorldChrManImp*, FieldInsHandle*) -> ChrIns*` (1.17.1
/// `0x140508a50`).
pub(crate) const GET_CHR_FROM_HANDLE: u32 = 0x0050_7c80;
/// Detour: `ChrSet::SpawnChr(ChrSet*, u8, ChrSpawnRequest*, int buddySlot) -> ChrIns*`. Its only
/// caller is `CreateSummonChr`, which hands it a request whose event entity it set to 35000
/// (1.17.1 `0x1404bb43b`, call at `0x1404bb4bf`) (1.17.1 `0x140493380`).
pub(crate) const SPAWN_CHR: u32 = 0x0049_2e20;
/// `ChrSpawnRequest+0x4c`: the event entity id `SpawnChr` registers the new character under in
/// its `ChrSet`'s entity map, which is what `GetChrInsByEntityId` searches.
pub(crate) const SPAWN_REQUEST_EVENT_ENTITY: usize = 0x4c;

/// The summon groups: `SummonBuddyManager+0x70` is an MSVC tree (head pointer `+0x78`) keyed by
/// owner event id; each node holds a `std::list` of 0x40-byte entries whose `+0x10` is the
/// `ChrIns*` (bd `summon-groups-hud-bar-and-removechrins-1171-static-2026-10-06`).
pub(crate) mod summon_group {
    pub(crate) const MANAGER_TREE_HEAD: usize = 0x78;
    pub(crate) const MANAGER_WARP_MANAGER: usize = 0xe8;
    pub(crate) const NODE_LEFT: usize = 0x0;
    pub(crate) const NODE_PARENT: usize = 0x8;
    pub(crate) const NODE_RIGHT: usize = 0x10;
    pub(crate) const NODE_IS_NIL: usize = 0x19;
    pub(crate) const NODE_KEY: usize = 0x20;
    pub(crate) const NODE_LIST_ALLOCATOR: usize = 0x28;
    pub(crate) const NODE_LIST_HEAD: usize = 0x30;
    pub(crate) const NODE_LIST_SIZE: usize = 0x38;
    pub(crate) const ENTRY_NEXT: usize = 0x0;
    pub(crate) const ENTRY_PREV: usize = 0x8;
    pub(crate) const ENTRY_CHR: usize = 0x10;
    /// `DLAllocator::Deallocate(this, void*)`, the slot the game's own sweep frees an entry
    /// through (1.17.1 `0x1404b945f`).
    pub(crate) const ALLOCATOR_DEALLOCATE: usize = 0x68;
}

/// The finger's own red-sign term, `bool(PlayerIns*)`: the player's `+0x2e7` bit 3, then
/// `IsInSafePosRange`, `WorldChrManImp::IsRedSignLimited` and the play region's red-sign event
/// flag. `CanUseGoods` calls it for goods 101 at 1.17.1 `0x14068f9bf` (1.16.2 `FUN_140656f90`,
/// 1.17.1 `0x140657de0`).
pub(crate) const RED_SIGN_TERM: u32 = 0x0065_6f90;
/// `WorldChrManImp::CanStartMultiplay(WorldChrManImp*) -> bool`: the term every multiplayer item
/// shares; false in an area where summoning is barred (1.17.1 `0x14050aa50`).
pub(crate) const CAN_START_MULTIPLAY: u32 = 0x0050_9c80;

/// Detour: `GetChrName(MenuString* out, ChrIns*, bool decorate) -> MenuString*` (1.17.1
/// `0x1407605a0`).
pub(crate) const GET_CHR_NAME: u32 = 0x0075_f750;
/// `MenuString+0`: `wchar_t* rawString`, then a `DLString<wchar_t>`. Readers take `rawString`
/// when it is not null and the `DLString` otherwise (`MenuString::Replace`, 1.16.2
/// `0x140763490`).
pub(crate) const MENU_STRING_RAW: usize = 0x0;

/// Lua 5.0.2 API of the AI state (bd `ai-lua-state-and-loader-1171-static-2026-10-05`).
pub(crate) mod lua {
    /// Detour: `lua_pcall(L, nargs, nresults, errfunc) -> int` (1.17.1 `0x142026970`).
    pub(crate) const PCALL: u32 = 0x0202_4b00;
    /// `lua_gettop(L) -> int` (1.17.1 `0x1420265d0`).
    pub(crate) const GETTOP: u32 = 0x0202_4760;
    /// `lua_settop(L, int)` (1.17.1 `0x142026fd0`).
    pub(crate) const SETTOP: u32 = 0x0202_5160;
    /// `luaL_loadbuffer(L, const char*, size_t, const char* name) -> int` (1.17.1
    /// `0x142027f30`).
    pub(crate) const LOADBUFFER: u32 = 0x0202_60c0;
    /// `lua_tostring(L, int) -> const char*` (1.17.1 `0x142027200`).
    pub(crate) const TOSTRING: u32 = 0x0202_5390;
}

/// Void tech for brains ([`crate::void_tech`]), all measured with `scripts/frida/void-trace.js`
/// on 1.17.1 (bd `void-tech-landing-frame-spawn-double-1171-2026-10-07`).
pub(crate) mod void_tech {
    /// Detour: `CSChrActionRequestModule::UpdateFromManipulator(module, FD4Time*)` (1.17.1
    /// `0x140408190`). Request bits written to `module+0x10` on entry reach the action; written
    /// from anywhere else they never did.
    pub(crate) const UPDATE_FROM_MANIPULATOR: u32 = 0x0040_7c60;
    /// Detour: the damage module's attack create-or-reuse, `FUN_1404428f0(CSChrDamageModule*,
    /// out, behaviorId, dmgType, slot, ...)`, 10 integer arguments (1.17.1 `0x140442e50`).
    /// Behavior 550 is created at a jump's takeoff and refreshed every airborne frame; the last
    /// refresh is the landing frame.
    pub(crate) const ATTACK_CREATE_OR_REUSE: u32 = 0x0044_28f0;
    /// Detour: `CSBulletManager::SpawnBullet(manager, int* out, BulletSpawnData*, param4)` (1.17.1
    /// `0x1403a2cb0`). `BulletSpawnData`: owner `FieldInsHandle` +0x0, behavior id +0x8.
    pub(crate) const SPAWN_BULLET: u32 = 0x003a_2ca0;
    /// The jump's airborne attack behavior.
    pub(crate) const JUMP_BEHAVIOR: i32 = 550;
    /// `CSChrModuleBase` +0x8: the owning `ChrIns*`.
    pub(crate) const MODULE_OWNER: usize = 0x8;
    /// `CSChrActionRequestModule` +0x10: this frame's action request bits.
    pub(crate) const ACTION_REQUESTS: usize = 0x10;
    /// `FD4Time` +0x8: the frame's time step in seconds.
    pub(crate) const FD4_TIME: usize = 0x8;
    /// `ChrIns` +0x8: its `FieldInsHandle`, what a bullet's owner field holds.
    pub(crate) const CHR_HANDLE: usize = 0x8;
    /// `ChrIns` vtable slot `GetComManipulator` (+0x1d0), then `ComManipulator` +0xc0 `AiIns*`,
    /// `AiIns` +0x28 the running NpcThinkParam id (bd `npc-think-swap-live-1171-2026-10-06`).
    pub(crate) const VT_GET_COM_MANIPULATOR: usize = 0x1d0;
    pub(crate) const COM_AI_INS: usize = 0xc0;
    pub(crate) const AI_INS_THINK: usize = 0x28;
    /// `PlayerIns` +0x638 `ChrAsm*`: `+0x8` arm style (3 is the right weapon in both hands),
    /// `+0x10` the active right weapon slot (0..2), `+0x7c` one param id per `ChrAsmSlot`, where
    /// right weapon slot n is index 1 + 2n (fromsoftware-rs `ChrAsm`; spawn-npc.js writes it).
    pub(crate) const PLAYER_CHR_ASM: usize = 0x638;
    pub(crate) const ASM_ARM_STYLE: usize = 0x8;
    pub(crate) const ASM_RIGHT_SLOT: usize = 0x10;
    pub(crate) const ASM_PARAM_IDS: usize = 0x7c;
    pub(crate) const ARM_STYLE_RIGHT_BOTH: u32 = 3;
    /// `PlayerIns` +0x580 `PlayerGameData`, +0x2b0 `EquipGameData`, +0x280 `EquipMagicData*`
    /// (back-pointer +0x8 to the `EquipGameData`), entries `{id, charges}` at +0x10, selected slot
    /// +0x80 (spawn-npc.js `applySpells`, measured).
    pub(crate) const PLAYER_GAME_DATA: usize = 0x580;
    pub(crate) const PGD_EQUIP_GAME_DATA: usize = 0x2b0;
    pub(crate) const EGD_MAGIC: usize = 0x280;
    pub(crate) const MAGIC_BACK: usize = 0x8;
    pub(crate) const MAGIC_ENTRIES: usize = 0x10;
    pub(crate) const MAGIC_SELECTED: usize = 0x80;
    pub(crate) const MAGIC_SLOTS: i32 = 14;
    /// `ChrIns` +0x190 module table: +0x0 `CSChrDataModule` (FP at +0x148), +0x18
    /// `CSChrTimeActModule` (anim queue of 16-byte entries at +0x20, `anim_id` first, read index at
    /// +0xc4; fromsoftware-rs `time_act.rs`, read live by `void-trace.js`).
    pub(crate) const CHR_MODULES: usize = 0x190;
    pub(crate) const MODULE_DATA: usize = 0x0;
    pub(crate) const DATA_FP: usize = 0x148;
    pub(crate) const MODULE_TIME_ACT: usize = 0x18;
    pub(crate) const TAE_QUEUE: usize = 0x20;
    pub(crate) const TAE_ENTRY: usize = 16;
    pub(crate) const TAE_READ: usize = 0xc4;
    pub(crate) const TAE_QUEUE_LEN: u32 = 10;
}

/// `CSWorldAiManager*` global (1.17.1 `0x143d66548`), carried by the data map.
pub(crate) const CS_WORLD_AI_MAN_GLOBAL_RVA: usize = 0x3d6_24e8;

/// `CSWorldAiManager` -> `+0x6938` `CSAiLua*` (getter `mov rax,[rcx+0x6938]; ret` at 1.17.1
/// `0x14037c140`) -> `+0xb8` `DLLuaDetail*` -> `+0x28` `lua_State*` (the detail's load wrapper at
/// 1.17.1 `0x142020900` passes `[this+0x28]`). Live-confirmed 2026-10-06: `lua_pcall` was called
/// with the state this chain reads.
pub(crate) mod ai_lua {
    pub(crate) const MAN_CS_AI_LUA: usize = 0x6938;
    pub(crate) const CS_AI_LUA_DETAIL: usize = 0xb8;
    pub(crate) const DETAIL_STATE: usize = 0x28;
}

/// "A menu has the mouse", `bool(CSMenuManImp*)`, 1.16.2 `FUN_140765800`. Its callers are the
/// cursor gate (1.17.1 `0x140e20490`: show and free the cursor) and the mouse axis readers
/// (`0x140e2b360`, `0x140e2b450`: return 0 while it is true). 1.17 added a clause to its body, so
/// it has no verified ledger row; it is an rva into 1.17.1 only, checked by its opening bytes
/// before it is hooked.
pub(crate) const MENU_HAS_MOUSE_1171_RVA: usize = 0x0076_6650;
// The 1.17.1 opening of `MENU_HAS_MOUSE_1171_RVA`, `MENU_HAS_MOUSE_1171_PROLOGUE`, is generated
// by build.rs from named instructions and checked against the 1.17 image.
include!(concat!(env!("OUT_DIR"), "/generated_prologues.rs"));

/// `ChrIns+0x190` modules -> `+0x68` physics module (`scripts/frida/spawn-npc.js` `physicsOf`,
/// live 1.17.1).
pub(crate) mod physics {
    pub(crate) const CHR_MODULES: usize = 0x190;
    pub(crate) const MODULES_PHYSICS: usize = 0x68;
    /// Position, three f32 (`spawn-npc.js` `posOf`).
    pub(crate) const POS: usize = 0x70;
    /// Standing on solid ground, one byte.
    pub(crate) const STANDING: usize = 0x92;
    /// The field-ins handle of what the character stands on: low dword, then high dword. The low
    /// dword's top nibble is the kind (0 map collision, 6 asset, 8 asset collision part). Measured
    /// 2026-10-05 in Raya Lucaria (bd `lift-detection-ground-handle-1171-2026-10-05`).
    pub(crate) const GROUND_HANDLE: usize = 0x240;
}

/// `CSWorldGeomMan*` global (1.17.1 `0x143d6dc18`), carried by the data map.
pub(crate) const CS_WORLD_GEOM_MAN_GLOBAL_RVA: usize =
    er_game_base::rva::CS_WORLD_GEOM_MAN_GLOBAL_RVA;

/// The rest of the character layout the lab's producers read (`scripts/frida/spawn-npc.js` on
/// `feat/ai-lab-turtles`, each read live on 1.17.1; the lab names the source of every one).
pub(crate) mod lab {
    /// `ChrIns+0x60`: the `NpcParam` id, 0 or below for a real player.
    pub(crate) const NPC_PARAM: usize = 0x60;
    /// `ChrIns+0x178` -> `+0x08` first live SpEffect entry: id `+0x08`, next `+0x30`.
    pub(crate) const SPEFFECTS: usize = 0x178;
    pub(crate) const SPEFFECTS_FIRST: usize = 0x08;
    pub(crate) const SPEFFECT_ID: usize = 0x08;
    pub(crate) const SPEFFECT_NEXT: usize = 0x30;
    /// `ChrIns+0x6b0`: the lock-on target's `FieldInsHandle`, low dword `0xffffffff` for none.
    pub(crate) const LOCK_TARGET: usize = 0x6b0;
    /// `ChrIns+0x580`: `PlayerGameData*`, a `PlayerIns`'s own.
    pub(crate) const PLAYER_GAME_DATA: usize = 0x580;
    /// `ChrIns+0x638`: `ChrAsm*`, param ids `+0x7c`, one `i32` per slot; slot 1 is right hand 1.
    pub(crate) const CHR_ASM: usize = 0x638;
    pub(crate) const CHR_ASM_PARAM_IDS: usize = 0x7c;
    pub(crate) const CHR_ASM_RIGHT1: usize = 1;
    /// Modules (`ChrIns+0x190`): `+0x0` data (HP `+0x138`, max `+0x13c`), `+0x18` TimeAct,
    /// `+0x20` resist.
    pub(crate) const MODULE_DATA: usize = 0x0;
    pub(crate) const DATA_HP: usize = 0x138;
    pub(crate) const MODULE_TIME_ACT: usize = 0x18;
    pub(crate) const MODULE_RESIST: usize = 0x20;
    /// TimeAct: ten `{anim i32, time f32, time f32, length f32}` at `+0x20`, write index `+0xc0`.
    pub(crate) const TA_QUEUE: usize = 0x20;
    pub(crate) const TA_SLOT: usize = 16;
    pub(crate) const TA_SLOTS: usize = 10;
    pub(crate) const TA_WRITE: usize = 0xc0;
    /// Resist: gauges `+0x10`, resistances `+0x2c`, seven `i32` each (poison, rot, bleed, ...).
    pub(crate) const RESIST_GAUGE: usize = 0x10;
    pub(crate) const RESIST_MAX: usize = 0x2c;
    /// `WorldChrMan` chr sets: 196 pointers ending 0x10 below `main_player`; set capacity
    /// `+0x10`, entries `+0x18`, 16 bytes each with the `ChrIns*` first.
    pub(crate) const CHR_SETS: usize = 0x1e508 - 0x10 - 196 * 8;
    pub(crate) const CHR_SET_COUNT: usize = 196;
    pub(crate) const SET_CAPACITY: usize = 0x10;
    pub(crate) const SET_ENTRIES: usize = 0x18;
    pub(crate) const SET_ENTRY: usize = 16;
    /// The AI's enemy search list (bd `ai-target-ene0-chain-1171-static-2026-10-06`): manipulator
    /// at `EnemyIns+0x580` / `PlayerIns+0x588` -> `+0xc0` `AiIns` -> `+0xc950` targeting system ->
    /// `+0x20` list head; node next `+0x10`, kind `+0x34` (1 a character), score `+0x38`, handle
    /// `+0x68`.
    pub(crate) const AI_MAN_ENEMY: usize = 0x580;
    pub(crate) const AI_MAN_PLAYER: usize = 0x588;
    pub(crate) const AI_INS: usize = 0xc0;
    pub(crate) const AI_TARGETING: usize = 0xc950;
    pub(crate) const AI_LIST: usize = 0x20;
    pub(crate) const AI_NODE_NEXT: usize = 0x10;
    pub(crate) const AI_NODE_KIND: usize = 0x34;
    pub(crate) const AI_NODE_SCORE: usize = 0x38;
    pub(crate) const AI_NODE_HANDLE: usize = 0x68;
    /// `PlayerGameData+0x2b0`: `EquipGameData`; its inventory at `+0x158` (entries `+0x10`, count
    /// `+0x18`, 24 bytes `{handle, item id, quantity}`), key item capacity `+0x158 + 0x1c`, quick
    /// slots `{handle u32, index i32}` at `+0x290 + 8N` mirrored as item ids at `+0x3a0 + 4N`.
    pub(crate) const EQUIP_GAME_DATA: usize = 0x2b0;
    pub(crate) const INVENTORY: usize = 0x158;
    pub(crate) const INV_ENTRIES: usize = 0x10;
    pub(crate) const INV_COUNT: usize = 0x18;
    pub(crate) const INV_ENTRY: usize = 24;
    pub(crate) const INV_KEY_CAPACITY: usize = 0x158 + 0x1c;
    pub(crate) const QUICK_SLOTS: usize = 0x290;
    pub(crate) const QUICK_ITEM_IDS: usize = 0x3a0;
}

/// Detour: `CalculateDamage2(CSChrDamageModule* victim, ChrIns* attacker, AttackDamageInfo*,
/// u64, bool)`, once per landed hit; the victim is `[victim + 0x8]` (1.17.1 `0x140448910`,
/// `IDENTICAL-WHOLE`; `scripts/frida/spawn-npc.js` `damageHook`).
pub(crate) const CALCULATE_DAMAGE2: u32 = 0x0044_83b0;
pub(crate) const DAMAGE_MODULE_OWNER: usize = 0x8;
/// `EquipGameData::AddInventoryEquip(egd, u32* handle, u32 quantity, bool, bool) -> i32`, the
/// grant the lab's food and heal restock used (1.17.1 `0x140246480`, unmoved). The build importer
/// declares it for its own grants.
#[cfg(windows)]
pub(crate) const ADD_INVENTORY_EQUIP: u32 =
    er_build_import_runtime::grant::ADD_INVENTORY_EQUIP_RVA as u32;

/// `CSWorldGeomMan` -> `+0x20` an MSVC `std::map` of loaded blocks (node `+0x28` block data), and
/// in each block data `+0x340` a `std::map` of entity id (node `+0x20`) -> field-ins handle (node
/// `+0x28` low, `+0x2c` high). 1.17.1 `0x1406d38f0`, `0x1406a76d0`; read live by
/// `spawn-npc.js` `refreshLiftHandles`.
pub(crate) mod geom {
    pub(crate) const MAN_BLOCKS: usize = 0x20;
    pub(crate) const BLOCK_NODE_DATA: usize = 0x28;
    pub(crate) const BLOCK_ENTITIES: usize = 0x340;
    pub(crate) const ENTITY_NODE_ID: usize = 0x20;
    pub(crate) const ENTITY_NODE_HANDLE: usize = 0x28;
}

/// MSVC `std::map` node: left, parent (the head's parent is the root), right, nil flag.
pub(crate) mod tree {
    pub(crate) const LEFT: usize = 0x0;
    pub(crate) const PARENT: usize = 0x8;
    pub(crate) const RIGHT: usize = 0x10;
    pub(crate) const IS_NIL: usize = 0x19;
}

/// `SummonBuddyManager+0x20`: the requested summon SpEffect, `207000 + level` for the Mimic Tear.
pub(crate) const BUDDY_MANAGER_REQUEST: usize = 0x20;

/// `BUDDY_PARAM_ST` field offsets (paramdef; no drift on the 1.17.1 regulation).
pub(crate) mod buddy_param {
    pub(crate) const NPC: usize = 0x08;
    pub(crate) const THINK: usize = 0x0c;
    pub(crate) const X: usize = 0x18;
    pub(crate) const Z: usize = 0x1c;
    pub(crate) const YAW: usize = 0x20;
    pub(crate) const CHARA_INIT: usize = 0x54;
    /// Bytes saved and restored around a hijacked `BuddyGenerator` call.
    pub(crate) const SAVE: usize = 0x58;
}
