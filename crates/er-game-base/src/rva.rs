//! Tier A: stable singleton RVA / offset table (game 1.16.x, image base
//! 0x140000000). These are version-anchored facts shared by all three DLLs;
//! they were previously re-declared under ~4 different aliases in the product
//! `constants/*` tree and hand-copied verbatim into the two mini-DLLs. This is
//! the single source of truth.
//!
//! Feature-specific / experiment-local offsets do NOT belong here — only the
//! cross-cutting singleton globals + their generic field offsets.

/// `GameDataMan` singleton global (aliased as GAME_DATA_MAN_GLOBAL_RVA /
/// CONTINUE_MANAGER_GLOBAL_RVA in the product tree).
pub const GAME_DATA_MAN_GLOBAL_RVA: usize = 0x3d5df38;
/// `CSMenuMan` singleton global (aliased GLOBAL_CSMENUMAN_RVA /
/// CS_MENU_MAN_GLOBAL_RVA / SELECTBOT_INPUT_MANAGER_GLOBAL_RVA /
/// TITLE_INPUT_MANAGER_RVA).
pub const CS_MENU_MAN_GLOBAL_RVA: usize = 0x3d6b7b0;
/// `GameMan` singleton global (save-slot owner).
pub const GAME_MAN_SINGLETON_RVA: usize = 0x3d69918;
/// `CS::FieldArea**` singleton global -- 1.16.2 runtime VA `0x143d691d8`.
///
/// The 1.16.2 Ghidra dump has 264 reads of this global. `ConvertBlockCoordsToPhysicsCoords`
/// (`0x14061e120`) reads it as `GLOBAL_FieldArea`, then passes `FieldArea+0x18` to the typed
/// `WorldInfoOwner` methods.
pub const FIELD_AREA_PTR_RVA: usize = 0x3d691d8;
/// `FUN_14067b570` -- system-slot-only save dispatcher (GameMan b73 set, b72 clear).
pub const SAVE_DISPATCH_SYSTEM_RVA: usize = 0x67b570;
/// `FUN_14067b940` -- combined character+system save dispatcher (GameMan b72 and b73 set).
pub const SAVE_DISPATCH_COMBINED_RVA: usize = 0x67b940;
/// `InGameStep::STEP_MoveMap_LoadlistInit` -- builds the world-res loadlist.
pub const STEP_MOVEMAP_LOADLIST_INIT_RVA: usize = 0xaec570;
/// SaveLoad IO device request teardown/release function.
pub const SL_RELEASE_REQUEST_RVA: usize = 0xe6f200;
/// `EzChildStepBase` child-step reset/teardown function.
pub const EZ_CHILDSTEP_RESET_RVA: usize = 0xeb54c0;
/// Scaleform LoaderImpl file-open wrapper.
pub const TITLE_SCALEFORM_FILE_OPEN_RVA: usize = 0x11ced80;
/// CS::TitleTopDialog vtable.
pub const TITLE_TOP_DIALOG_VTABLE_RVA: usize = 0x2b26468;
/// Scaleform::MemoryFile vtable.
pub const SCALEFORM_MEMORY_FILE_VTABLE_RVA: usize = 0x2ba4c80;
/// `CSSystemStep` singleton global.
pub const CS_SYSTEM_STEP_GLOBAL_RVA: usize = 0x3d85680;
/// Native `MenuWindowJob::Run` close-with-Failed helper. It calls `SetResult(..., Failed=3, 0)`
/// and then invokes the receiver's own vtable slot +0x60.
pub const MENU_WINDOW_CLOSE_WITH_FAILED_RVA: usize = 0x7ac890;
/// Save-data subsystem gate global (submit path guard). Ghidra names it `GLOBAL_CSEventState`;
/// the guard role is the reason it is here. It is a 3-byte HeapAlloc read as a byte at
/// offset 0, not the 0x270-byte object allocated beside it (that one is 0x3d68448).
pub const SAVE_DATA_SUBSYSTEM_GATE_RVA: usize = 0x3d68078;
/// Main heap allocator singleton global (`GLOBAL_MainHeapAllocator`). Identified from the
/// 1.16.2 Ghidra dump: 1821 xrefs, readers spanning `CSTaskImp` / `CSWindowImp` / `CSEzWork`
/// / `BloodMessageInsMan`, and `GameMan::WriteSaveToSlot` (0x14067b750) derefs
/// `GLOBAL_MainHeapAllocator->_vfptr->AllocateAligned` while referencing this address.
/// Aliased in the tree as GLOBAL_MAIN_HEAP_ALLOCATOR_RVA / SLLOAD_SRC2_RVA (a wrong name) /
/// SAVE_BUFFER_ALLOCATOR_GLOBAL_RVA (a role name in er-save-loader; that crate now derives
/// from here, so the misnomer is documented at its declaration rather than duplicated).
pub const GLOBAL_MAIN_HEAP_ALLOCATOR_RVA: usize = 0x3d872e0;
/// Menu heap allocator singleton global (`GLOBAL_MenuHeapAllocator`, `DLAllocator*`), the
/// allocator every menu-owned allocation goes through. Sits 0x70 past the main heap allocator
/// above; the two are easy to transpose, which is the second reason it is pinned here.
/// Identified from the 1.16.2 Ghidra dump: 401 xrefs, and the accessor
/// `DLAllocator *GetMenuHeapAllocator(void)` (0x1407a72a0) is nothing but
/// `return GLOBAL_MenuHeapAllocator;` reading this address.
/// Aliased in the tree as GLOBAL_MENU_HEAP_ALLOCATOR_RVA (er-effects-rs save-picker path editor,
/// which passes it as the allocator argument of the SoftwareKeyboard job alloc) /
/// MENU_HEAP_ALLOCATOR_POINTER_RVA (er-player-name-filter, which passes it to
/// `DLString<wchar_t>::FromU16Array` exactly as `GetPlayerChrName` does).
pub const GLOBAL_MENU_HEAP_ALLOCATOR_RVA: usize = 0x3d87350;
/// SaveLoad IO device singleton global. Its lazy getter is 0x140e6e060, and
/// `GameMan::WriteSaveToSlot` fetches the device through that getter before submitting a save.
/// Aliased as IODEV_GLOBAL_RVA (er-title-flow) / SL_IODEV_GLOBAL_RVA (er-save-suppress); both
/// now derive from here (2026-08-01). "iodev" is the role the callers use -- the dump does not
/// name the class, so treat the name as descriptive rather than authoritative.
pub const SL_IODEV_GLOBAL_RVA: usize = 0x4589390;
/// `FUN_140e05fb0(CSDlcImp*, bool)` -- the DLC virtual-root REFILL: re-queries Steam DLC ownership
/// and calls `CSDlcImp::AddVirtualFileRoots`.
pub const DLC_ROOTS_REFILL_RVA: usize = 0x00e0_5fb0;
/// `GLOBAL_CSDlc` -- the `CSDlcImp` singleton.
pub const CSDLC_SINGLETON_RVA: usize = 0x03d8_6bd8;

/// `CS::ItemReplenishStateTracker::ShouldReplenishItem(tracker, int *itemId)` -- whether an item is
/// currently flagged to be topped back up from the storage box. Honours the per-type DEFAULT for an
/// item with no entry (`AUTO_REPLENISH_TYPE` 2 / Consumable defaults ON, type 1 defaults OFF), which
/// is why it, and not a read of the entry vector, is the right question to ask about current state.
pub const SHOULD_REPLENISH_ITEM_RVA: usize = 0x0023_d990;
/// `ReplanishItemsFromChest()` -- the native storage -> personal inventory transfer loop. Vanilla
/// runs it from `OnEvent_BonfireRespawn` and `MoveMapStep::UpdatePlayerInfo`. Marking replenish
/// state alone moves no items; this is what moves them.
pub const REPLANISH_ITEMS_FROM_CHEST_RVA: usize = 0x0024_dff0;

/// `GameDataMan` -> `PlayerGameData` pointer field offset.
/// `CS::GameMan::SetMoveMapStepBlockId(BlockId *out, BlockId *in)` -- writes
/// `GameMan.moveMapStepBlockId`, i.e. picks the destination block of the next map transition.
/// Byte-checked against `eldenring-deobf.bin` at shift 0 for 1.16.2 (`0x14067abd0`).
///
/// NOTE for callers: `param_1` is the OUT slot, and it is not always equal to `param_2` --
/// for areas 50..=88 the id is rewritten through `CalcGetReplaceMapIdByDisaster`. Read the
/// out slot back rather than assuming the requested block is the effective one.
pub const SET_MOVE_MAP_STEP_BLOCK_ID_RVA: usize = 0x67abd0;

pub const GAME_DATA_MAN_PLAYER_GAME_DATA_08_OFFSET: usize = 0x08;
/// `CSMenuMan` -> `menuData` pointer field offset.
pub const CS_MENU_MAN_MENU_DATA_OFFSET: usize = 0x8;

/// `CS::GetGR_System_Message(MenuString* rcx, int edx)` -- resolves a `GR_System_Message` FMG id
/// into a `MenuString`.
///
/// Cross-cutting because two DLLs reach it for opposite reasons: `er-effects-rs` hooks it READ-ONLY
/// to detect corrupted-save popups by message id, and `er-invasion-warp` calls it to build a
/// `MenuString` for the system-message banner. Declared twice with different names until 2026-08-06,
/// which is exactly the alias drift this module exists to stop.
///
/// CORRECTED HISTORY worth keeping: 0x762e30 is `GetTextEmbedImageName`, NOT this. Hooking that one
/// is why a corrupted-save oracle once stayed at zero forever.
pub const GR_SYSTEM_MESSAGE_RVA: usize = 0x762d50;

/// Shared `CS::MessageBoxDialog` function/vtable RVAs.
///
/// These are cross-cutting game identities consumed by title flow, telemetry, and the product's
/// startup/quit paths. Keep the values here so a version correction cannot drift between crates.
#[repr(usize)]
pub enum MsgBoxRva {
    ForceStop = 0x78dfd0,
    OkHandler = 0x78e030,
    MultiChoiceGetter = 0x7b0cf0,
    Builder = 0x9275b0,
    OnDecide = 0x927ba0,
    Update = 0x927d30,
    DialogVtable = 0x2b03550,
}

pub const MSGBOX_DIALOG_VTABLE_RVA: usize = MsgBoxRva::DialogVtable as usize;

// ---- inventory functions shared by more than one DLL ----
//
// These moved here when a second crate needed them: `er-better-refills` reads the player's
// inventory to decide what to replenish, and `er-build-import` reads it to confirm granted
// items and to resolve the inventory INDEX that the equip path requires. Two literal copies of
// one address is exactly the drift `scripts/check-rva-alias-drift.py` exists to stop.

/// `CS::EquipGameData::GetEquipInventoryData(equipGameData) -> EquipInventoryData*`.
pub const GET_EQUIP_INVENTORY_DATA_RVA: usize = 0x247b30;
/// `EquipInventoryData::GetQuantityByItemId(inventory, int *itemId) -> int`.
pub const GET_QUANTITY_BY_ITEM_ID_RVA: usize = 0x24c1b0;
// ---- the message repository singleton ----
//
// Moved here for the same reason as the inventory functions above: two crates now read it.
// `er-invasion-warp` resolves a `PlaceName`, and `er-build-import` builds its whole
// name -> item-id catalog out of the game's own strings.
//
// IT IS READ FROM THE GAME'S OWN GLOBAL, NOT from a typed upstream singleton. `fromsoftware-rs`
// at the revision CI pins (`FROMSOFTWARE_RS_REV` in .github/workflows/check.yml) has no
// `MsgRepositoryImp`; only a local fork does. Depending on that type builds on a developer's
// machine and fails in CI with `unresolved import`, which is exactly what happened. Reading the
// global keeps the address in this repo, where `scripts/check-rva-alias-drift.py` can see it.

/// `GLOBAL_MsgRepository` -- the `CS::MsgRepositoryImp*` singleton slot, read from
/// `MOV RCX, qword ptr [0x143d7d4f8]` inside `MsgRepository::GetAndFormat`.
///
/// The slot is null until the repository is constructed, so every caller must treat a zero as
/// "not up yet" rather than dereferencing it -- the engine itself DLPanics on that path.
pub const MSG_REPOSITORY_GLOBAL_RVA: usize = 0x3d7_d4f8;

/// `GLOBAL_SoloParamRepository` -- the `SoloParamRepositoryImp*` singleton slot, read as
/// `MOV RCX, qword ptr [0x143d81ee8]` at the head of every param-row lookup.
///
/// Null on a quit teardown, and the lookups do not tolerate that: `FUN_140d3d5f0`
/// (`LoadBalancerParam`) and `LookupMenuOffscrRendParam` both go straight to
/// `DLPanic("...FD4Singleton.h", 0xb4, "<uninitialised singleton accessed>")`, which then dies
/// writing through a null pointer. Treat a zero here as "already gone" rather than dereferencing
/// it. Observed killing a tester's game twice in two days, 2026-08-22 and 2026-08-23.
pub const SOLO_PARAM_REPOSITORY_GLOBAL_RVA: usize = 0x3d8_1ee8;

/// `EquipInventoryData::GetItemInventoryIdx(inventory, int *itemId) -> int`.
///
/// Returns the index the equip path needs; negative means the item is not held.
pub const GET_ITEM_INVENTORY_IDX_RVA: usize = 0x24c560;

// ---- the equipped loadout: read by the importer, the exporter and the HUD badge ----
//
// These moved here when the Generate Build Link row arrived and gave every one of them a THIRD
// declaration. Each address was already written out twice -- once in `er-build-import-runtime`
// where the importer verifies its own writes, once in `er-armament-icons` where the HUD badge
// resolves the equipped gem -- and an exporter that reads the same slots would have made three.
//
// `check-rva-alias-drift.py` puts the reason better than a comment can: divergent names for one
// address are divergent CLAIMS about what that address IS, and at least one of them is then a
// wrong reverse-engineering fact shipping in the DLL. Declaring the value once and deriving the
// aliases keeps the names -- which are useful, they say what the caller wants -- while leaving
// exactly one place a 1.16.x address correction has to land.

/// `CS::EquipGameData::GetParamIdInSlot(egd, ChrAsmSlot) -> int`.
///
/// The read-back oracle for equipment, in both directions. The importer needs it because
/// `EquipItemToChrAsmSlot` returns void and declines silently, so only the slot's contents
/// afterwards are evidence; the exporter needs it because those contents ARE the build.
pub const GET_PARAM_ID_IN_SLOT_RVA: usize = 0x2470e0;

/// `CS::EquipGameData::GetEquippedGreatrune(egd, int *out, int slot) -> int*`.
///
/// THREE arguments. The outer wrapper at `0x140247900` is only
/// `ADD RCX,0x288 / MOV RBX,RDX / CALL 0x14024f390 / MOV RAX,RBX` -- it never writes R8, so the
/// slot argument passes straight through to the inner function, whose body begins
/// `*out = -1; if (slot == 0 && ...)`. Calling it with two arguments leaves R8 holding whatever
/// the call site happened to have, the `slot == 0` test fails, and it reports -1 no matter what is
/// equipped. That produced three runs of "the rune will not equip" when the rune was fine.
pub const GET_EQUIPPED_GREATRUNE_RVA: usize = 0x247900;

/// `CS::EquipGameData::GetPhysicTearBySlot(egd, int *out, uint slot) -> int*`.
///
/// Another OUT-PARAMETER getter, like the great rune above: the second argument is a pointer the
/// callee writes through, not a scalar. Treating it as a scalar getter stores through whatever the
/// caller passed, which took the game down once.
pub const GET_PHYSIC_TEAR_BY_SLOT_RVA: usize = 0x247a20;

/// `CS::EquipMagicData::GetMagicSlotsCount(emd, SpecialEffect*) -> uint`.
///
/// A null `SpecialEffect` means "derive it from the player", which accounts for Memory Stones and
/// talismans; the engine clamps the result to 14.
pub const GET_MAGIC_SLOTS_COUNT_RVA: usize = 0x250580;

/// `CS::EquipMagicData::GetEquipMagicId(emd, slot) -> int` -- the memorised spell in a slot.
pub const GET_EQUIP_MAGIC_ID_RVA: usize = 0x2506d0;

/// `GetWeaponGaitemHandleBySlot(PlayerIns*, u32 *out, ChrAsmSlot) -> u32*`.
///
/// First hop of the ash-of-war read: a `ChrAsmSlot` names a gaitem handle.
pub const GET_WEAPON_GAITEM_HANDLE_BY_SLOT_RVA: usize = 0x656920;

/// `GetGaitemInsByHandle(GaitemLookupResult *inout, GaitemLookupResult *handleSource)`.
///
/// Second hop. BOTH arguments are the SAME pointer in every native caller; passing two different
/// buffers reads a handle that was never written into the second one, and the failure is a silent
/// nothing rather than a fault.
pub const GET_GAITEM_INS_BY_HANDLE_RVA: usize = 0x672e40;

/// `GetSwordArtsParamForWeapon(GaitemLookupResult*, SwordArtsParamLookupResult *out)`.
///
/// Third hop, and the reason the chain is worth walking: it resolves through
/// `GetGemGaitemHandleFromWeapon` -> `GetGaitemInsGem`, i.e. the ACTUAL equipped gem. The menu
/// path's `arts_id * 100` heuristic misses every weapon whose gem id is not derived that way
/// (Igon's Drake Hunt: arts 4210 -> gem 548000).
pub const GET_SWORD_ARTS_PARAM_FOR_WEAPON_RVA: usize = 0x673f30;

/// `GLOBAL_CSGaitem`, the process-lifetime `CSGaitemImp` singleton pointer.
///
/// Constructed once at boot and never rebuilt per world-load. Every gaitem primitive in the game
/// reads it and `DLPanic`s on null, so a caller that mints or releases a handle must read it from
/// here rather than keep its own copy.
pub const GLOBAL_CSGAITEM_RVA: usize = 0x3d69890;

/// `WorldChrMan` singleton global; `+0x1e508` is the local `PlayerIns`.
///
/// Both dereferences are null-checked in the game's own code, so both must be null-checked here.
pub const WORLD_CHR_MAN_GLOBAL_RVA: usize = 0x3d65f88;

/// `WorldChrMan::mainPlayerIns`, the local player inside the singleton above.
pub const WORLD_CHR_MAN_PLAYER_INS_OFFSET: usize = 0x1e508;
