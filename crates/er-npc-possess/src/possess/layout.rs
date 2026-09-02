//! EVERY GAME STRUCT OFFSET THE ENGINE TOUCHES, IN ONE PLACE, AND NOTHING ELSE.
//!
//! # Why a module of constants rather than the `eldenring` crate's structs
//!
//! `fromsoftware-rs` models most of these fields already, and the engine could reach half of them
//! through a typed reference. It deliberately does not, for one reason that is worth the
//! duplication: **one of these offsets MOVED between the two builds this repo supports** --
//! `ChrIns.debugFlags` is `+0x530` on 1.16.2 and `+0x538` on 1.17 (byte-proven: the same three
//! sites test `[reg+0x538]` in 1.17). A typed field access compiles to one offset and cannot say
//! which build it is for, so on the wrong build it writes into `stamina_recovery` and reports
//! success. [`debug_flags_offset`] answers `None` on a build nobody has measured, which is the
//! only honest third answer and the one a struct field cannot give.
//!
//! Having decided that for one field, the rest follow it: an offset table that is HALF typed
//! fields and half constants is a table where nobody can tell which half was checked.
//!
//! # What checks them
//!
//! * On Windows, [`crate::possess::game`] carries `const` assertions comparing each constant here
//!   against `core::mem::offset_of!` on the `eldenring` struct that models the same field. That
//!   is a compile-time cross-check of two independently derived layouts, and it costs nothing at
//!   runtime. Only the crate's `pub` fields can be asserted; the ones it spells `unkNNN` are
//!   marked RE-ONLY below.
//! * On the host, the tests at the foot of this file check the invariants that are arithmetic --
//!   the build gate, the flag bits, and that the override slot is nobody else's field.
//!
//! Every value is from the settled reverse engineering recorded in `bd`
//! (`chrmanipulator-vtable-55-slots-thunk-design-2026-09-01`,
//! `possess-body-neuter-levers-debugflags-invincible-2026-09-01`,
//! `per-frame-chrins-move-without-fall-damage-2026-09-01`,
//! `possess-release-drop-in-place-physics-path-2026-09-01`) unless a comment says otherwise.

// This module is pure arithmetic and stays ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use er_game_base::game_build::{FileVersion, SUPPORTED_FILE_VERSION};

/// ELDEN RING 1.17, PE `FileVersion` 2.7.0.0 -- the build installed on this machine since
/// 2026-08-27. [`SUPPORTED_FILE_VERSION`] is its 1.16.2 counterpart, 2.6.2.0.
pub(crate) const FILE_VERSION_1170: FileVersion = FileVersion {
    major: 2,
    minor: 7,
    build: 0,
    revision: 0,
};

/// `ChrIns` field offsets.
pub(crate) mod chr_ins {
    /// `ChrIns.chrSetEntry` -- back to the `ChrSetEntry` this character occupies. Cross-checked.
    ///
    /// THE SLOT, WITHOUT SEARCHING FOR IT. `ChrInsFactory::CreateCharacter` is handed the entry and
    /// the `ChrIns` constructor keeps it here, so a spawn's slot index is
    /// `(chrSetEntry - chrSet->entries) / `[`super::chr_set::ENTRY_STRIDE`] rather than a scan --
    /// and the division having no remainder, plus the quotient landing inside
    /// [`super::chr_set::BAND_FIRST`]`..capacity`, is the bounds proof for every later read of that
    /// slot.
    pub(crate) const CHR_SET_ENTRY: usize = 0x10;
    /// `ChrIns.chrRes` -- the per-character asset step machine.
    ///
    /// RE-ONLY: the `eldenring` crate models this field but keeps it PRIVATE, so there is no
    /// `offset_of!` cross-check to be had. The evidence is byte-level instead, and is stronger
    /// than a cross-check would have been: `ChrIns::GetEneDat` is BYTE-IDENTICAL between 1.16.2
    /// `0x1403ef830` and 1.17 `0x1403efa60`, and it opens `48 8b 43 28` -- `MOV RAX,[RBX+0x28]`.
    /// See [`super::chr_res`].
    pub(crate) const CHR_RES: usize = 0x28;
    /// `ChrIns.chrCtrl`. Cross-checked against `offset_of!(ChrIns, chr_ctrl)`.
    pub(crate) const CHR_CTRL: usize = 0x58;
    /// `ChrIns.modules` -- the `ChrInsModuleContainer`. Cross-checked.
    pub(crate) const MODULES: usize = 0x190;
    /// `ChrIns.chrFlags1c5`. Bit `0x10` is INVINCIBILITY and nothing else -- not visibility, not
    /// targeting (exhaustive byte scan: 2 readers, 2 setters, 3 clearers). Cross-checked.
    ///
    /// Writing it directly is exactly what `CS::ChrIns::EnableInvincible` does; its whole body is
    /// `chrFlags1c5 = (chrFlags1c5 & 0xef) | (state << 4)`. See [`INVINCIBLE`].
    pub(crate) const CHR_FLAGS_1C5: usize = 0x1c5;
    /// The invincibility bit inside [`CHR_FLAGS_1C5`].
    ///
    /// **IT IS ALSO THE GRAB GATE, and that is settled rather than suspected.**
    /// `ChrIns::IsImmuneToAttack` (ChrIns vtable `+0x1D8`; 1.16.2 `0x1403f3b90`, 1.17
    /// `0x1403f3dc0` -- both read `+0x1c5 & 0x10` verbatim) answers "immune" on this bit unless
    /// the landed `AtkParam` row sets `isDisableNoDamage`. Every route into a throw is behind it:
    /// `ApplyDamage` is the only caller of `InitThrow`, `HitChr` is the only caller of
    /// `ApplyDamage`, and the two hit-resolution routines that reach `HitChr` with a real
    /// attacker (`FUN_14044a910` melee, `FUN_14038b2f0` bullet; 1.17 `0x14044ae70` /
    /// `0x14038b300`) both call it only after `FUN_1404443e0` -> `IsImmuneToAttack` has cleared
    /// the victim. So while this bit is set the possessing player's own body -- which
    /// `ThrowParam.DefChrId` makes the only legal victim for 189 of the game's 190 creature rows
    /// -- refuses the grab, and the initiator plays as a swing that misses. There is no
    /// per-attacker exemption to find: the predicate's only attacker-dependent term
    /// (`actionModifiersFlags` bit 5) makes it MORE immune to a non-player attacker, never less.
    pub(crate) const INVINCIBLE: u8 = 0x10;
    /// `ChrIns.tintAlphaMultiplier`. 0.0 is invisible, 1.0 is opaque. Cross-checked.
    pub(crate) const TINT_ALPHA_MULTIPLIER: usize = 0x240;
    /// `ChrIns.tintAlphaMultiplierModifier` -- a per-frame DECAY toward 0 (negative) or 1
    /// (positive). `SetFadeInOut` writes this, which is why calling it FADES rather than holds:
    /// we write both fields directly and keep the modifier at zero so the alpha stays put.
    /// Cross-checked.
    pub(crate) const TINT_ALPHA_MULTIPLIER_MODIFIER: usize = 0x244;

    /// `ChrIns.chrFlags1c8` -- the byte that carries the SOUND MUTE. Cross-checked, and unchanged
    /// on 1.17 (it is below the `+0x3b8` insertion point).
    pub(crate) const CHR_FLAGS_1C8: usize = 0x1c8;
    /// `chrFlags1c8` bit `0x40`: SET means SILENT.
    ///
    /// All four TAE sound handlers (event ids 129/132/133/134) open with
    /// `if (FUN_1403f4680(chrIns)) return;`, and that gate's first instruction is
    /// `TEST byte [rcx+0x1c8],0x40` -- set means `MOV AL,1; RET`, i.e. drop the sound.
    ///
    /// The POLARITY is not inferred from the name: the engine's own writer is a distance
    /// hysteresis (`FUN_140401d80`) that SETS this bit once a character is further from the player
    /// than `NpcParam.enableSoundObjDist`, or while it is ragdolling. Distant NPCs going quiet is
    /// shipped behaviour using this exact bit.
    ///
    /// It also does not fight us per frame, which is what makes it usable: that whole recompute
    /// sits inside `if (!IsPlayerIns(chrIns))`, so nothing recalculates it on the player's body.
    /// The only writers that can reach us are four CLEARS at (re)spawn/teleport -- in
    /// `PlayAnimation`, `Respawn`, `FUN_1403fc9a0` and `SummonHorse` -- which is why the
    /// per-frame block re-asserts it rather than setting it once.
    pub(crate) const MUTE_SOUND: u8 = 0x40;
    /// `chrFlags1c8` bit `0x80`: a ONE-SHOT that stops sounds already playing.
    ///
    /// `PostPhysicsSafe` sees `flags1c8 >= 0x80`, clears the bit, and walks
    /// `CSChrDataModule+0x98` stopping every sound in flight on that character. The engine writes
    /// `|= 0xC0` for exactly this pairing, and so do we: [`MUTE_SOUND`] silences what has not
    /// started, this silences what already has.
    pub(crate) const STOP_PLAYING_SOUNDS: u8 = 0x80;

    /// `ChrIns.debugFlags` on ELDEN RING 1.16.2.
    pub(crate) const DEBUG_FLAGS_1162: usize = 0x530;
    /// `ChrIns.debugFlags` on ELDEN RING 1.17. IT MOVED; see the module docs.
    pub(crate) const DEBUG_FLAGS_1170: usize = 0x538;
    /// `debugFlags` bit `0x10`: the character issues no attack requests.
    ///
    /// In `PadManipulator::Tick` this is tested BEFORE `IsMainPlayerIns`, so it works on the main
    /// player -- which is the whole reason it is the lever for neutering the possessing player's
    /// own body. It routes to `padManip+0x1e4` and skips the entire 16-entry `ChrActionType`
    /// request loop: R1/R2/L1/L2, guard, the action button, USE_ITEM and the quick slots.
    ///
    /// UNPROVEN and worth knowing: whether roll/jump/sprint are inside those 16 entries. Only
    /// index 4 (action button) and index 7 (USE_ITEM) are named, so this may cost rolls too.
    pub(crate) const DEBUG_FLAG_NO_ATTACK: u32 = 0x10;
    /// `debugFlags` bit `0x20`: the move vector is forced to zero.
    ///
    /// DELIBERATELY LEFT CLEAR by the possession engine. The player's body is co-located with the
    /// creature every frame by writing the proxy request directly, and freezing its move vector
    /// buys nothing while making the body fight that write.
    pub(crate) const DEBUG_FLAG_NO_MOVE: u32 = 0x20;
}

/// `ChrSet`, and the band of it the game's own dynamic spawner claims.
///
/// # The bound, and which array it is actually on
///
/// `FUN_140492a90` -- the function `CSTalkDynamicChrCtrl` reaches through the spawn entry
/// [`crate::spawn`] calls -- opens `index = 6` and loops `while (index < 0x14)`, taking the first
/// entry whose `chrIns` is null. It reads `entries[6..20)` **without consulting `chrInsCapacity`
/// at all**, so the bound that matters is not on our index (we never form one) but on the ARRAY: a
/// `ChrSet` smaller than twenty entries would have the GAME read past its own allocation.
/// [`CAPACITY`] is therefore read at runtime and the spawn refused unless it covers [`BAND_END`].
///
/// The buddy `ChrSet` allocates `0x500` bytes and sets `chrInsCapacity = 0x50` (`FUN_140494f50`,
/// whose init loop also confirms [`ENTRY_STRIDE`]: `0x500 / 0x50 == 0x10`, and it walks the array
/// in `0x10` steps). So the check passes on a stock game and exists for the case where it does not
/// -- another mod having resized it, or a build where the constant moved. It is deliberately NOT
/// the fix for a full roster: fourteen slots is what the game gives its own dynamic spawner, and
/// widening that allocation is the one change that could corrupt a Seamless Co-op session, which
/// lives in this same `ChrSet`.
///
/// # Every value here is byte-identical on 1.17
///
/// The loop is thirty-seven bytes and they are the SAME thirty-seven bytes in both images -- 1.16.2
/// `0x140492abf`, 1.17 `0x14049301f`, reached by decoding the `rel32` in the spawn wrapper:
///
/// ```text
/// bf 06 00 00 00     MOV EDI,0x6            ; BAND_FIRST
/// 48 8b 59 18        MOV RBX,[RCX+0x18]     ; ENTRIES
/// 44 8b c7           MOV R8D,EDI
/// 48 83 c3 60        ADD RBX,0x60           ; 6 * ENTRY_STRIDE
/// 90                 NOP
/// 4c 39 3b           CMP [RBX],R15          ; ENTRY_CHR_INS == 0 ?
/// 74 17              JZ   <take it>
/// ff c7              INC EDI
/// 49 ff c0           INC R8
/// 48 83 c3 10        ADD RBX,0x10           ; ENTRY_STRIDE
/// 49 83 f8 14        CMP R8,0x14            ; BAND_END
/// 7c ec              JL   <loop>
/// ```
pub(crate) mod chr_set {
    /// `ChrSet.chrInsCapacity`, `u32`. Cross-checked against `offset_of!(ChrSet<ChrIns>, capacity)`.
    pub(crate) const CAPACITY: usize = 0x10;
    /// `ChrSet.entries`, a `ChrSetEntry*`. Cross-checked.
    pub(crate) const ENTRIES: usize = 0x18;
    /// `sizeof(ChrSetEntry)` -- `chrIns`, `loadStatus`, `updateType`, padding.
    pub(crate) const ENTRY_STRIDE: usize = 0x10;
    /// `ChrSetEntry.chrIns`, at the very front. NULL means the slot is free, and
    /// `ChrSet::RemoveChrIns` puts it back to NULL -- which is what makes this field double as the
    /// despawn detector.
    pub(crate) const ENTRY_CHR_INS: usize = 0x0;
    /// First slot the game's dynamic spawner will take. Slots 0..5 belong to the real summons and
    /// are never scanned by it.
    pub(crate) const BAND_FIRST: u32 = 6;
    /// One past the last slot it will take: the loop condition is `index < 0x14`.
    pub(crate) const BAND_END: u32 = 0x14;
}

/// `ChrSpawnRequest` -- the 200-byte block the spawn call reads, built by this crate.
///
/// # Every offset here is read out of the retail caller's own stores
///
/// `CSTalkDynamicChrCtrl`'s spawn step (1.16.2 `0x140e9ce30`) builds one of these on its stack and
/// hands it to the same function this crate calls. Its frame resolves to `request = RSP+0x60` and
/// `RBP = RSP+0x100`, and every field below is one of its stores at the matching displacement --
/// so this table is a transcription of working code rather than an inference from a struct
/// definition.
///
/// # What the CREATURE path actually reads, which is less than the struct suggests
///
/// `ChrInsFactory::CreateCharacter` branches on [`CHARA_INIT_PARAM`]: negative takes the
/// `HeapAlloc(0x5e0)` + `EnemyIns` path, non-negative the `HeapAlloc(0x740)` + `PlayerIns` one. On
/// the creature branch it reads exactly [`NPC_PARAM_ID`], [`NPC_THINK_ID`], [`EVENT_ENTITY_ID`],
/// [`TALK_ID`] and the raw pointer at `MODEL +` [`MODEL_BACKING`]. It does **not** read
/// [`POSITION`] or [`ORIENTATION`] -- those are consumed only by `PlayerChrBaseData::Init` on the
/// player branch, and the enemy branch takes its base data from `InitEnemyChrBaseData` instead.
///
/// **So a spawned creature does not appear where the request says.** This crate fills the two
/// vectors anyway, because the retail caller does and a well-formed request costs nothing, and
/// then PLACES the creature itself once it is ready, through the same proxy-drain write
/// co-location already uses. A layer that trusted the position field would have shipped a mod that
/// spawns everything at wherever `InitEnemyChrBaseData` leaves it.
pub(crate) mod chr_spawn_request {
    /// `sizeof(ChrSpawnRequest)`.
    pub(crate) const SIZE: usize = 0xc8;
    /// `position`, a `FloatVector4`. See the module note: NOT read on the creature path.
    pub(crate) const POSITION: usize = 0x00;
    /// `orientation`, a `FloatVector4`. Likewise.
    pub(crate) const ORIENTATION: usize = 0x10;
    /// `scale`, a `FloatVector4`. The retail caller writes the constant at `0x144802470` here and
    /// at [`UNK30`]; it is `{1,1,1,1}`, so this crate writes ones.
    pub(crate) const SCALE: usize = 0x20;
    /// The unnamed fourth vector, written with the same value as [`SCALE`].
    pub(crate) const UNK30: usize = 0x30;
    /// `npcParamId`, `i32`. THE ROW THAT DRIVES THE CREATURE: an id `NpcParam` has no row for falls
    /// back to row 0, and the row is what selects the model, animation and sound resources the
    /// `ChrRes` step machine then goes and acquires.
    pub(crate) const NPC_PARAM_ID: usize = 0x40;
    /// `npcThinkId`, `i32`. Need not be valid: the think-param lookup pre-initialises its result to
    /// `{-1, NULL, -1, -1}`, no `luabnd` is requested, and `LoadWait` treats the NULL `LuaDat` caps
    /// as already satisfied.
    pub(crate) const NPC_THINK_ID: usize = 0x44;
    /// `charaInitParam`, `i32`. **NEGATIVE SELECTS THE CREATURE PATH**; see the module note.
    pub(crate) const CHARA_INIT_PARAM: usize = 0x48;
    /// `eventEntityId`, `u32`. ZERO IS THE SAFE VALUE and the one the retail caller uses:
    /// `FUN_140494240` returns immediately on 0, so a zero id is never inserted into the `ChrSet`'s
    /// `eventEntityIdMap` and cannot shadow a map entity. Any nonzero id we invented WOULD be.
    pub(crate) const EVENT_ENTITY_ID: usize = 0x4c;
    /// `talkId`, `i32`. Zero for a creature nobody talks to.
    pub(crate) const TALK_ID: usize = 0x50;
    /// `model`, a `DLTX::DLInplaceStr`. The `cNNNN` name, and the whole of how a chr id reaches the
    /// asset loader.
    pub(crate) const MODEL: usize = 0x58;
    /// `model.backingString.pointer`, a `wchar_t*`. **THE ONLY FIELD OF `model` THAT ANY CONSUMER
    /// ON THE SPAWN PATH READS** -- `FUN_140492a90` passes it to `Format(L"%s_%04d", ptr, index)`
    /// and `CreateCharacter` passes it straight into `ChrInitData`. Neither reads `len`, and
    /// neither dispatches through the string's vtable, which is why [`MODEL_VFTABLE`] can stay
    /// null.
    pub(crate) const MODEL_BACKING: usize = 0x08;
    /// `model.len`, in WCHARS and not bytes.
    pub(crate) const MODEL_LEN: usize = 0x10;
    /// The `u32` the retail caller zeroes between `len` and `charSize`.
    pub(crate) const MODEL_UNK18: usize = 0x18;
    /// `model.charSize`, `u16`, `2` for UTF-16.
    pub(crate) const MODEL_CHAR_SIZE: usize = 0x1c;
    /// `model.type`, `u8`, `1` for `UTF16`. The retail caller writes `charSize`, `type` and the
    /// flags byte as one `MOV dword ptr [model+0x1c],0x10002`.
    pub(crate) const MODEL_TYPE: usize = 0x1e;
    /// The value [`MODEL_TYPE`] holds for UTF-16.
    pub(crate) const MODEL_TYPE_UTF16: u8 = 1;
    /// `model.flags`, `u8`, zero.
    pub(crate) const MODEL_FLAGS: usize = 0x1f;
    /// The inplace character buffer, which [`MODEL_BACKING`] is made to point at.
    pub(crate) const MODEL_BUFFER: usize = 0x20;
    /// How many `wchar_t` fit in it. The retail caller installs the
    /// `DLInplaceStr<1,32,DLCodedStr<1>>` vtable, so thirty-two is the capacity that vtable's own
    /// grow check would report; `cNNNN` needs six including the terminator.
    pub(crate) const MODEL_BUFFER_WCHARS: usize = 32;
    /// `model`'s vtable slot, LEFT NULL BY THIS CRATE -- and that is the deliberate part.
    ///
    /// The retail caller stores `DLTX::DLInplaceStr<1,32,DLCodedStr<1>>::vftable` (1.16.2
    /// `0x142a425a0`) because it then calls `[vtable+0x30]`, the capacity check, before `memcpy`ing
    /// in a name of unknown length. This crate writes a name of KNOWN length -- six wchars into a
    /// thirty-two wchar buffer -- so it never needs that call, and nothing on the spawn path
    /// dispatches through this pointer: `FUN_140492a90`'s only touch of `model` is
    /// `MOV R8,[R14+0x60]`, the backing pointer, and `CreateCharacter`'s two indirect calls are
    /// both `InitializeCharacter` on the NEW character's vtable. Writing it would cost a third
    /// resolved game address for a pointer nobody follows.
    pub(crate) const MODEL_VFTABLE: usize = 0x00;
}

/// `ChrRes`, the per-character asset step machine at `ChrIns+` [`chr_ins::CHR_RES`].
///
/// # This is `ChrIns::IsInLoadedState`, read rather than called
///
/// `CS::ChrIns::GetEneDat` (1.16.2 `0x1403ef830`) is `IsInLoadedState() ? chrRes->eneDat : NULL`;
/// its gate is `ChrIns` vtable `+0x4f8`, whose whole body is `MOV RCX,[RCX+0x28]; JMP
/// ChrRes::IsInLoadedState`; and `ChrRes::IsInLoadedState` is `3 <= vft[10]() && vft[10]() < 6`
/// with `vft[10]` being `MOV EAX,[RCX+0x40]; RET`. So the entire predicate is one bounds test on
/// one `i32` field, and the readiness oracle reads it rather than making three calls.
///
/// BOTH BUILDS, byte-proven. `GetEneDat` is BYTE-IDENTICAL at 1.16.2 `0x1403ef830` and 1.17
/// `0x1403efa60` -- `48 8b 43 28` is [`chr_ins::CHR_RES`] and `48 8b 80 30 01 00 00` is [`ENE_DAT`]
/// -- and 1.17's `ChrRes` vtable, located by RTTI at `0x142a400c8`, has `MOV EAX,[RCX+0x40]; RET`
/// in slot 10 exactly as 1.16.2 does.
pub(crate) mod chr_res {
    /// `FD4StepTemplateBase<ChrRes,...>::currentState`, `i32`. RE-ONLY.
    ///
    /// NOT `+0x48`: Ghidra's generic `FD4StepTemplateBase` is `0xb0` bytes and puts `currentState`
    /// there, but the `ChrRes` instantiation is `0xa8` and the accessor's own disassembly says
    /// `+0x40`. The disassembly wins.
    pub(crate) const STEP: usize = 0x40;
    /// Lowest [`STEP`] value that counts as loaded.
    pub(crate) const STEP_LOADED_FIRST: i32 = 3;
    /// One past the highest.
    pub(crate) const STEP_LOADED_END: i32 = 6;
    /// `ChrRes.eneDat`, an `EneDat*`. RE-ONLY.
    pub(crate) const ENE_DAT: usize = 0x130;
}

/// `EneDat` and the `FD4FileCap` it hangs the chrbnd off.
///
/// # The second offset in this crate that MOVED between the two builds
///
/// `FUN_1404ca4a0` -- "give me this character's `FlverResCap`, or null" -- is the asset-residency
/// predicate. It is NOT byte-identical across the builds: its 1.17 counterpart is `0x1404caf70`
/// (uniquely shape-matched in both images, and independently confirmed by decoding the `rel32` at
/// its caller `EnemyIns::InitializeCharacterRendering`), and both `EneDat` offsets it loads moved:
///
/// | | 1.16.2 | 1.17 | delta |
/// |---|---|---|---|
/// | primary cap | `+0xa0` | `+0xb0` | `+0x10` |
/// | fallback cap | `+0x88` | `+0x90` | `+0x8` |
///
/// **THE TWO DELTAS ARE NOT THE SAME**, which is the part worth reading twice: an eight-byte field
/// was inserted somewhere below `+0x88` and another between `+0x88` and `+0xa0`. So the pair cannot
/// be carried forward by adding one number to both, and a reader who checked only the primary would
/// get the fallback wrong by eight bytes -- landing on a live pointer rather than on nothing.
///
/// The `FD4FileCap` offsets it then reads -- [`file_cap::LOAD_STATE`] and
/// [`file_cap::FLVER_RES_CAP`] -- ARE unchanged. `EneDat` is a large heap struct, so a stale
/// `+0xa0` on 1.17 reads a live neighbouring field rather than faulting, which is why
/// [`ene_dat_cap_offsets`] answers `None` on a build nobody has measured instead of guessing.
pub(crate) mod ene_dat {
    /// `EneDat`'s primary `ChrbndFileCap*` on 1.16.2.
    pub(crate) const CAP_PRIMARY_1162: usize = 0xa0;
    /// ...and the one it falls back to when that is null.
    pub(crate) const CAP_FALLBACK_1162: usize = 0x88;
    /// `EneDat`'s primary `ChrbndFileCap*` on 1.17. IT MOVED; see the module docs.
    pub(crate) const CAP_PRIMARY_1170: usize = 0xb0;
    /// ...and the 1.17 fallback.
    pub(crate) const CAP_FALLBACK_1170: usize = 0x90;
}

/// `FD4FileCap`, as `FUN_1404ca4a0` reads it. Both offsets are identical on both builds.
pub(crate) mod file_cap {
    /// The load-state byte. `4` means the cap's bytes are in memory.
    pub(crate) const LOAD_STATE: usize = 0x88;
    /// The value [`LOAD_STATE`] holds once loading has finished.
    pub(crate) const LOADED: u8 = 4;
    /// `flverResCap`. Non-null means the chr actually has geometry; the game self-despawns a
    /// character whose caps loaded but yielded no FLVER, inside
    /// `EnemyIns::InitializeCharacterRendering`.
    pub(crate) const FLVER_RES_CAP: usize = 0x90;
}

/// `ChrCtrl` field offsets.
pub(crate) mod chr_ctrl {
    /// `ChrCtrl.owner` -- back to the `ChrIns`. Cross-checked.
    pub(crate) const OWNER: usize = 0x10;
    /// `ChrCtrl.manipulator` -- the REAL manipulator. Never written by this engine: the AI-side
    /// lookups (`ChrIns::GetAiInsFromManipulator`, `EnemyIns::GetChrManipulator`) read this and
    /// must keep seeing the creature's own `ComManipulator`, which is what lets us write AI intent
    /// while the tick path sees our thunk. Cross-checked.
    pub(crate) const MANIPULATOR: usize = 0x18;
    /// `ChrCtrl.modifier` -- `ChrCtrlModifier`. Cross-checked.
    pub(crate) const MODIFIER: usize = 0xc8;
    /// `ChrCtrl.chrProxyFlags`. Cross-checked.
    pub(crate) const CHR_PROXY_FLAGS: usize = 0xfc;
    /// `chrProxyFlags` bit 0: drain `ragdollPosition` through `ForceSetPosition`.
    pub(crate) const CHR_PROXY_FLAG_POSITION: u32 = 1;
    /// `chrProxyFlags` bit 1: drain `ragdollRotation` through `SetOrientation`.
    pub(crate) const CHR_PROXY_FLAG_ROTATION: u32 = 2;
    /// `ChrCtrl.ragdollPosition`, a `FloatVector4`. RE-ONLY (`unk100` in the crate).
    pub(crate) const RAGDOLL_POSITION: usize = 0x100;
    /// `ChrCtrl.ragdollRotation` -- **EULER RADIANS `{0, yaw, 0, 0}`, NOT a quaternion**. The
    /// drain feeds it to `CSChrPhysicsModule::SetOrientation`, which feeds `EulerToQuat`.
    /// RE-ONLY (`unk110` in the crate).
    pub(crate) const RAGDOLL_ROTATION: usize = 0x110;
    /// `ChrCtrl.scaleSizeX`, three consecutive `f32` (`X`, `Y`, `Z` at `+0x2d4/+0x2d8/+0x2dc`).
    ///
    /// **THE LOCK-ON ANCHOR, reached the only way it can be reached.** A character's lock-on
    /// point is a dummy polygon on its model (ids 220..228, chosen by `NpcParam.lockGazePoint0..7`)
    /// and there is no per-character offset field anywhere in that chain -- so the anchor moves
    /// only when the model does. `ChrCtrl::RecalculateChrMatrix` multiplies these three into
    /// `ChrCtrl.modelMatrix` and copies that matrix into `locationMtx44ChrEntity->mtx`, the root
    /// the dummy lookup resolves against. See [`crate::possess::body_size`] for the whole trace.
    ///
    /// This is the RENDER transform and only the render transform: `ChrCtrl::SetScaleSize` never
    /// touches `CSChrPhysicsModule`, so the body's hknp capsule, its hurtbox and its own
    /// `hitHeight` are all unaffected by writing it.
    ///
    /// **BOTH BUILDS, byte-proven, and the pattern matches UNIQUELY in each image.** The whole of
    /// `ChrCtrl::SetScaleSize` is fifty-three bytes, and they are the SAME fifty-three bytes in
    /// both -- 1.16.2 `0x1403c8350`, 1.17 `0x1403c8360`, the `+0x10` shift the rest of the
    /// `ChrCtrl` module has. Every offset this table needs is one of its displacements:
    ///
    /// ```text
    /// f2 0f 10 02              MOVSD XMM0,[RDX]           ; scale.xy
    /// f2 0f 11 81 d4 02 00 00  MOVSD [RCX+0x2d4],XMM0     ; scaleSizeX | scaleSizeY
    /// 8b 42 08                 MOV   EAX,[RDX+0x8]        ; scale.z
    /// 89 81 dc 02 00 00        MOV   [RCX+0x2dc],EAX      ; scaleSizeZ
    /// 48 8b 41 10              MOV   RAX,[RCX+0x10]       ; ChrCtrl.owner   (OWNER)
    /// f2 0f 10 02              MOVSD XMM0,[RDX]
    /// 48 8b 88 90 01 00 00     MOV   RCX,[RAX+0x190]      ; ChrIns.modules  (chr_ins::MODULES)
    /// 4c 8b 01                 MOV   R8,[RCX]             ; modules[0]      (modules::DATA)
    /// f2 41 0f 11 40 54        MOVSD [R8+0x54],XMM0       ; the mirror -- chr_data_module::SCALE
    /// 8b 42 08                 MOV   EAX,[RDX+0x8]
    /// 41 89 40 5c              MOV   [R8+0x5c],EAX
    /// c3                       RET
    /// ```
    pub(crate) const SCALE_SIZE: usize = 0x2d4;
    /// `ChrCtrl+0x3b0` -- THE MANIPULATOR OVERRIDE SLOT, and the whole reason this mod is
    /// possible. RE-ONLY (`unk3b0` in the crate).
    ///
    /// Nothing in retail ever writes or frees it: a byte scan of every `mov [r+0x3b0],r64` form
    /// finds exactly ONE site image-wide, the `ChrCtrl` constructor's zero-init, and no reader
    /// anywhere calls `[manip+8]` (the destructor) on it. So we own the object outright.
    ///
    /// TEARDOWN HAZARD: `ChrCtrl::Unref` compares this against zero and **DLPanics** when it is
    /// non-null. It must be cleared before the character is torn down -- see
    /// [`crate::possess::teardown`].
    pub(crate) const MANIPULATOR_OVERRIDE: usize = 0x3b0;
}

/// `ChrCtrlModifier` / `ChrCtrlModifierData`.
pub(crate) mod chr_ctrl_modifier {
    /// `ChrCtrlModifier.data`.
    pub(crate) const DATA: usize = 0x8;
    /// `ChrCtrlModifierData+0x1c`, Ghidra's `1cFlags`.
    ///
    /// Bit 0 is **isDead**, read straight out of `CS::ChrIns::IsDead`, whose entire body is
    /// `return chrCtrl->ctrlModifier->ChrCtrlModifierData._1cFlags & 1`. A `bd` memory
    /// (`possess-body-neuter-levers-debugflags-invincible-2026-09-01`) calls bit 0 of this field
    /// "the invisible-state flag"; that is wrong, and the decompiled `IsDead` is the correction.
    pub(crate) const FLAGS_1C: usize = 0x1c;
    /// The death bit inside [`FLAGS_1C`].
    pub(crate) const FLAG_IS_DEAD: u32 = 1;
}

/// `ChrInsModuleContainer` slots, by index times eight.
pub(crate) mod modules {
    /// `CSChrDataModule`. Cross-checked.
    pub(crate) const DATA: usize = 0x00;
    /// `CSChrTimeActModule` -- the animation queue, i.e. what is playing right now.
    /// Cross-checked.
    pub(crate) const TIME_ACT: usize = 0x18;
    /// `CSChrBehaviorModule` -- carries `rootMotion`, which is how the watchdog tells a slow
    /// wind-up from a creature that has genuinely stopped. Cross-checked.
    pub(crate) const BEHAVIOR: usize = 0x28;
    /// `CSChrEventModule` -- the animation REQUEST slot. Cross-checked.
    pub(crate) const EVENT: usize = 0x58;
    /// `CSChrPhysicsModule`. Cross-checked.
    pub(crate) const PHYSICS: usize = 0x68;
    /// `CSChrActionRequestModule` -- where the engine writes whether the animation it is
    /// currently playing may be cancelled. See [`chr_action_request_module`]. Cross-checked.
    pub(crate) const ACTION_REQUEST: usize = 0x80;
}

/// `CSChrActionRequestModule` -- the engine's own answer to "may this attack be left yet".
///
/// # Why this is an oracle and not a heuristic
///
/// `CS::CSAiFunc::IsEnableCancelAttack` (1.16.2 `0x140300800`) is what the game's own AI calls
/// to decide whether the attack a creature is in the middle of may be cancelled into another
/// attack. Its whole answer comes from one leaf, 1.16.2 `0x1404075a0` / 1.17 `0x140407ad0`:
///
/// ```text
/// 8b 81 00 01 00 00   mov  eax, [rcx+0x100]      ; taeCancels
/// a8 20               test al, 0x20              ; bit 5
/// 74 09               je   .no
/// 0f ba e0 0b         bt   eax, 0xb              ; bit 11
/// 72 03               jb   .no
/// b0 01               mov  al, 1
/// c3                  ret
/// .no: 32 c0 c3       xor  al, al ; ret
/// ```
///
/// That 22-byte pattern occurs exactly once in `eldenring-deobf.bin` (1.16.2, `0x1404075a0`) and
/// exactly once in `eldenring-deobf-1.17.bin` (`0x140407ad0`), which is what pins the field
/// offset and both bit positions on the build the game is actually running. This crate reads the
/// field and applies the same two tests; it does not call the function, so nothing here is a
/// resolved address.
///
/// # Where the bit comes from
///
/// TAE event type 0 (`ChrActionFlag`) with `FlagType` 86 -- `CANCEL_AI_ATTACK_QUEUED` in
/// `fromsoftware-rs`' naming of the same bitfield -- sets it, and
/// `CSChrActionRequestModule::Update` clears bits 3..9 (`and dword [rcx+0x100], 0xfffffc07`)
/// once per frame from `CS::ChrIns::PreBehaviorSafe`. So the bit is a genuine per-frame window
/// authored per animation, not sticky state: it is true exactly while the playhead is inside the
/// window the animation's own TimeAct declares. 91.1% of the corpus's non-player attack
/// animations author one.
///
/// Bit 11 is `cancel_disable`, a PERSISTENT global veto, and the engine's own predicate requires
/// it clear -- so this crate does too rather than reading bit 5 on its own.
pub(crate) mod chr_action_request_module {
    /// `taeCancels`, `u32`. Cross-checked.
    pub(crate) const TAE_CANCELS: usize = 0x100;
    /// Bit 5: this attack may be cancelled into another attack right now.
    pub(crate) const CANCEL_ATTACK: u32 = 0x20;
    /// Bit 11: global cancel veto. Every one of the engine's cancel predicates requires it clear.
    pub(crate) const CANCEL_DISABLE: u32 = 0x800;
}

/// `CSChrEventModule` -- how an attack is fired, and why it costs no game address.
///
/// `CS::CSChrEventModule::RequestAnimation` (1.16.2 `0x14043aa30`) has a two-line body: it calls
/// `SetRendererVisibility(chr, 5)` and then writes `requestAnimationId`. The visibility call is a
/// min-latch into `ChrIns+0xBC` that the `ChrIns` update RESETS TO -2 every frame, so it has no
/// lasting effect and nothing to undo. Which leaves a single `int` store -- so **writing this
/// field IS calling the function**, and the moveset layer keeps the crate's no-game-addresses
/// property intact.
///
/// `CSChrEventModule::Update` (1.16.2 `0x14043a580`) consumes it once per frame and resets it to
/// -1, routing through `FUN_140c14400`, which formats `DLString::FormatW(L"%s%04d", L"W_Event",
/// animId)` and resolves it against the same behaviour world `PlayAnimationByBehaviorName` uses.
/// So the clip carries its TimeAct binding and the hitbox, VFX, sound and root motion all come
/// along -- none of which this crate has to reimplement.
///
/// FOUR GATES sit in front of it, all of which a possessed creature ordinarily passes:
/// `!ChrIns::IsDead`, `actionFlag->actionAnimationFlags` bit 12 clear, `!CSChrThrowModule::IsInTrow`,
/// and `field_0x70 == 0`. A request that fails them is dropped silently; nothing here can observe
/// that, which is part of why the watchdog exists.
pub(crate) mod chr_event_module {
    /// `requestAnimationId`, `i32`. -1 means "nothing pending". Cross-checked.
    pub(crate) const REQUEST_ANIMATION_ID: usize = 0x18;
}

/// `CSChrTimeActModule` -- the ten-entry ring buffer of animations.
///
/// # The entry is a clock, and that is what the cancel discipline runs on
///
/// The 1.16.2 dump names all four fields of `CSChrTimeActModuleAnim` (`/CS/CSChrTimeActModule`,
/// size 16): `animId` +0x0, `prevLocalTime` +0x4, `localTime` +0x8, `animLength` +0xC. So the
/// entry the read cursor points at says which clip is playing, how far into it the creature is,
/// and how long the clip runs -- in the creature's own seconds. `fromsoftware-rs` spells the same
/// four `anim_id` / `play_time` / `play_time2` / `anim_length`, i.e. its public `play_time` is the
/// dump's `prevLocalTime`, one frame behind; [`crate::moveset::chain`] uses `localTime` and this
/// module names it after the dump, which is the more informed of the two namings.
pub(crate) mod chr_time_act_module {
    /// `animQueue`, ten `CSChrTimeActModuleAnim`. Cross-checked.
    pub(crate) const ANIM_QUEUE: usize = 0x20;
    /// Size of one queue entry: `animId`, `prevLocalTime`, `localTime`, `animLength`.
    pub(crate) const ANIM_STRIDE: usize = 0x10;
    /// `localTime` within a queue entry, `f32` -- seconds into the clip THIS frame.
    ///
    /// `prevLocalTime` at +0x4 is the same measurement one frame earlier, which is what the pair
    /// is for: an event whose time falls between the two fired during this frame. Reading the
    /// later of the two is what makes a chain window open on the frame it is reached rather than
    /// the frame after.
    pub(crate) const ANIM_LOCAL_TIME: usize = 0x08;
    /// Entries in the ring.
    pub(crate) const ANIM_QUEUE_LEN: u32 = 10;
    /// `readIdx`, `u32` -- the index of the animation last played or updated, i.e. the current
    /// one. Cross-checked.
    pub(crate) const READ_IDX: usize = 0xc4;
}

/// `CSChrBehaviorModule`.
///
/// # Reaching the `hkbCharacter`, without calling anything
///
/// `PlayAnimationByBehaviorName` wants a pointer to a slot holding the creature's `hkbCharacter`.
/// `CSChrEventModule::Update` gets one by calling `FUN_14041aef0(behaviorModule, &slot)`, which
/// calls `FUN_140c07a40(behaviorModule->field_0x10, &slot)`, whose entire body is
/// `slot = *(behaviorModule->field_0x10 + 0x30)`. Two loads and a null check -- so this crate does
/// the two loads and skips both calls, and the only address the moveset layer resolves is the one
/// it genuinely cannot avoid.
pub(crate) mod chr_behavior_module {
    /// The `hkbCharacter` owner this module hangs off. RE-ONLY (`unk10` in the crate).
    pub(crate) const HKB_OWNER: usize = 0x10;
    /// ...and the `hkbCharacter` itself, inside that. RE-ONLY.
    pub(crate) const HKB_CHARACTER: usize = 0x30;
    /// `rootMotion`, a `FloatVector4`. The engine's own per-frame displacement for this
    /// character, which is exactly the "is it actually going anywhere" question the watchdog
    /// needs and cannot get from position deltas (co-location moves the player every frame
    /// regardless). Cross-checked.
    ///
    /// **Byte-verified on 1.17**, because the watchdog decides on this number whether a move is
    /// marked permanently unusable, and a wrong offset would mark good moves bad. The
    /// `CSChrBehaviorModule` constructor zero-inits it, and its zero-init window is unique
    /// (`hits=1`) in both images and byte-for-byte IDENTICAL between them:
    ///
    /// ```text
    /// 48890333ed 48896b?? 48896b?? 48896b?? 48896b?? 0f57c0 0f2943?? 0f2943?? 0f2943?? 488d4b?? e8
    ///     1.16.2 @0x1404189ea, 1.17 @0x140418f1a, and every wildcarded displacement reads back
    ///     the same: the four pointers at +0x10..+0x28 and the three vectors at +0x30/+0x40/+0x50.
    /// ```
    pub(crate) const ROOT_MOTION: usize = 0x30;
}

/// `CSChrDataModule`.
pub(crate) mod chr_data_module {
    /// `hp`, `i32`. Proven by `CSChrDataModule::GetHpRate` being `[+0x138] / [+0x13c]`.
    /// Cross-checked.
    pub(crate) const HP: usize = 0x138;
    /// THE MIRROR of [`super::chr_ctrl::SCALE_SIZE`] -- three `f32` at `+0x54/+0x58/+0x5c`.
    ///
    /// Written by `ChrCtrl::SetScaleSize` in the same breath as the `ChrCtrl` copy (the byte proof
    /// is on that constant), and this crate writes both for the same reason the game does: a
    /// consumer that reads the mirror instead of the source must not see a body of a different
    /// size from the one the renderer is using. Which of the two any given consumer reads was not
    /// enumerated, and writing both is cheaper than finding out.
    pub(crate) const SCALE_SIZE: usize = 0x54;
}

/// `CSChrPhysicsModule`.
pub(crate) mod chr_physics_module {
    /// The live physics-space position, a `FloatVector4`. This is the field
    /// `ChrIns::GetPhysicsPosition` returns and the one `ForceSetPosition` writes -- read the
    /// creature's, write the player's, ZERO conversion, same space. Cross-checked.
    pub(crate) const POSITION: usize = 0x70;
    /// `standingOnSolidGround`, a `bool`. This is what `ChrIns::IsStandingOnSolidGround` reads,
    /// so the question "does the release point already qualify as ground" is a field read rather
    /// than a sphere cast. Cross-checked.
    pub(crate) const STANDING_ON_SOLID_GROUND: usize = 0x92;
    /// `lastGroundedPosition`. RE-ONLY (`unk150` in the crate), and the one field in this table
    /// that is both READ and WRITTEN.
    ///
    /// Read, it is a point the character demonstrably stood on -- the free fallback for the release
    /// point when the creature dies airborne.
    ///
    /// **Written, it is the possession's fall-death safety.** `CSChrFallModule`'s landing handler
    /// charges for a fall of `lastGroundedPosition.y - GetPosition().y`, and dispatches the
    /// fall-death call through the character's manipulator vtable when that exceeds a global
    /// threshold. That path never calls `ChrIns::IsImmuneToAttack`, so
    /// [`super::chr_ins::INVINCIBLE`] does not cover it -- which is why the body neuter has to
    /// write this field rather than rely on the bit.
    /// `CSChrPhysicsModule::ForceSetPosition`, which is how co-location moves the body, writes
    /// `position` and `prevUpdatePosition` and leaves this one alone.
    ///
    /// BOTH BUILDS, byte-proven, and the pattern matches UNIQUELY in each image -- 1.16.2
    /// `0x14044dd1f`, 1.17 `0x14044e27f`. `48 8b 88 90 01 00 00` is [`super::chr_ins::MODULES`],
    /// `48 8b 59 68` is [`super::modules::PHYSICS`], and `0f 10 b3 50 01 00 00` is this offset:
    ///
    /// ```text
    /// MOV RCX,[RAX+0x190] ; MOV RBX,[RCX+0x68] ; CALL GetPosition
    /// MOVUPS XMM6,[RBX+0x150] ; SHUFPS XMM6,XMM6,0x55 ; SUBSS XMM6,[RAX+0x4]
    /// ```
    pub(crate) const LAST_GROUNDED_POSITION: usize = 0x150;
    /// `orientationEuler`, a `FloatVector4` whose `.y` is yaw in radians -- the same convention
    /// `ChrCtrl.ragdollRotation` expects, so co-location can copy it across without touching a
    /// quaternion. Cross-checked.
    pub(crate) const ORIENTATION_EULER: usize = 0x2d0;
}

/// `ComManipulator`, and the `AiIns` reached through it.
pub(crate) mod manipulator {
    /// `operator delete(this, 0x170)` in `ComManipulator`'s scalar deleting destructor. The thunk
    /// mirrors exactly this many bytes so that a field read the engine performs on what it
    /// believes is a `ComManipulator` lands on our zeroed memory rather than past the end of it.
    pub(crate) const COM_SIZE: usize = 0x170;
    /// `ComManipulator.aiIns`.
    pub(crate) const AI_INS: usize = 0xc0;
}

/// `AiIns`, and the `AiPathData` it points at -- THE FOUR FIELDS `CSAiFunc::MoveTo` WRITES.
///
/// # The move request is three fields, and this crate used to write one
///
/// `CSAiFunc::MoveTo` (`0x140301f70`) is the engine's own front door for "walk to this point",
/// and its whole body is:
///
/// ```text
/// aiIns->walkType = 2 - (walk != 0);          // 2 run, 1 walk
/// CS::AiIns::SetWantToMoveTo(aiIns, &point);  // a bare 16-byte store, nothing else
/// FUN_1402c65e0(aiIns, targetType, angles);   // build the follow path
/// ```
///
/// `CS::AiIns::ClearMoveRequest` (`0x1402bf9a0`) is the inverse: `walkType = 0` and
/// `wantToMoveTo = own physics position`. So both halves of "go" and "stop" are plain field
/// writes, and `walkType` is the one that decides whether anything happens at all --
/// `[vt+0x50]` (`FUN_1403d0250`) reads it at `0x1403d0307`
/// (1.17: `0x1403d0317`) and computes the frame's move vector **only** inside
/// `if (walkType != 0 && ChrIns::GetMoveType(chr) != 0)`; otherwise the vector is
/// `DL_ZERO_VECTOR` and the body stands still however good `wantToMoveTo` is.
///
/// Nothing on the per-frame path writes `walkType`: `CS::AiIns::UpdateMovement` runs first and
/// never touches it, and every writer in the AI region (`MoveTo`, `MoveToEventPoint`,
/// `FollowPath`, `ClearMoveRequest`, and the goal `Activate`/`Interrupt`/`Update` bodies) is
/// reached from GOAL SELECTION -- which possession deliberately no-ops at `[vt+0x48]`. That is
/// what makes writing these fields the product mechanism rather than a diagnostic: the native
/// owner is not merely idle, it is the thing this mod switched off on purpose.
///
/// # `turnTarget` steers after all, in exactly one of its values
///
/// A previous note here said writing `turnTarget` steers nothing because the named points it
/// selects are not refreshed once goal selection is dead. That is true of every value except
/// `TARGET_SELF`, which refreshes from a field we DO write. `UpdateMovement` ends with
/// `FUN_1402c9410(aiIns, aiIns->turnTarget)`, and that function's `TARGET_SELF` branch
/// (`0x1402ca0c4`) reads `walkType`, returns early if it is zero, and otherwise takes
/// `wantToMoveTo - GetPhysicsPosition()` as the direction to face, converts it to angles and
/// stores them in `aiIns+0xc3f0` -- which `[vt+0x50]` then differences against the body's live
/// orientation to produce the frame's turn request. So `turnTarget = TARGET_SELF` means "face
/// wherever you have been told to walk", which is precisely the steering this crate needs, and
/// it costs one `int`.
///
/// With `walkType == 0` that same branch falls to `FUN_1402c68f0`, which writes the body's
/// CURRENT orientation into `+0xc3f0` -- i.e. releasing the stick stops the turn too, with no
/// extra write.
///
/// # 1.17
///
/// Every offset below was read out of the 1.16.2 named dump's own curated `AiIns` structure and
/// then **byte-verified on 1.17**, because `AiIns` is `0xf0d0` bytes and one inserted field would
/// move all of them silently. Two windows carry all four, and each is unique (`hits=1`) in BOTH
/// `eldenring-deobf.bin` and `eldenring-deobf-1.17.bin`:
///
/// ```text
/// 488b8fc0000000 448bb1????0000 e8???????? 8bf0
///     1.16.2 @0x1403d0300, 1.17 @0x1403d0310 -> AI_INS 0xc0, WALK_TYPE 0xc424 on both
/// 488b83????0000 0f1000 0f1183????0000 8b93????0000 488bcb e8???????? ...
///     1.16.2 @0x1402c751e, 1.17 @0x1402c752e -> PATH_DATA 0xd9c8, target +0x0,
///                                               WANT_TO_MOVE_TO 0xc3e0, TURN_TARGET 0xdab0
/// ```
///
/// `motionMult` (`+0xc410`, `FloatVector4`) is the remaining gait lever `[vt+0x50]` consumes.
/// Untouched by this layer: `MoveTo` does not write it either, and `walkType` already carries the
/// walk/run distinction the engine scales the move vector by.
///
/// The canary in `crate::possess::game::ai_path_target` stays, and stays load-bearing: these
/// offsets are proven for the two builds that were measured, and a third build gets no writes.
pub(crate) mod ai_ins {
    /// `wantToMoveTo`, a `FloatVector4` in physics space. Byte-verified on 1.16.2 and 1.17.
    pub(crate) const WANT_TO_MOVE_TO: usize = 0xc3e0;
    /// `walkType`, an `int`. **THE GATE** -- see the module note. Byte-verified on both builds.
    pub(crate) const WALK_TYPE: usize = 0xc424;
    /// `pathData`, an `AiPathData*`. Doubles as the layout canary; see the module note above.
    pub(crate) const PATH_DATA: usize = 0xd9c8;
    /// `turnTarget`, an `AiTargetPointType` -- a 4-byte SIGNED enum. Byte-verified on both builds.
    pub(crate) const TURN_TARGET: usize = 0xdab0;

    /// `walkType = 0`, exactly as `CS::AiIns::ClearMoveRequest` writes it. No move vector.
    pub(crate) const WALK_TYPE_STOP: i32 = 0;
    /// `walkType = 1`. `[vt+0x50]` scales the move vector down by `DAT_14329e980` for this value
    /// and this value only, which is what makes it the WALK of the pair.
    pub(crate) const WALK_TYPE_WALK: i32 = 1;
    /// `walkType = 2`, the unscaled gait. `MoveTo` writes `2 - walk`, so these two are the only
    /// values the engine's own front door produces.
    pub(crate) const WALK_TYPE_RUN: i32 = 2;

    /// `AiTargetPointType::TARGET_SELF`. Proven from the dispatch in `FUN_1402c9410`:
    /// `CMP ESI,-0x1` at `0x1402c94ca` jumps to the branch at `0x1402ca0c4` that reads
    /// `[RDI+0xc424]` and then `[RDI+0xc3e0]`.
    pub(crate) const TURN_TARGET_SELF: i32 = -1;
}

/// `AiPathData`.
pub(crate) mod ai_path_data {
    /// `target`, a `FloatVector4`, at the very front of the struct.
    pub(crate) const TARGET: usize = 0x0;
}

/// `WorldChrManDbg`.
pub(crate) mod world_chr_man_dbg {
    /// `camOverrideChrIns`. Cross-checked.
    ///
    /// Only five sites read it, and the one that matters is `WorldChrManImp::GetMainPlayerIns`,
    /// which prefers it over the raw `mainPlayerIns` field. That moves the ~40 `GetMainPlayerIns`
    /// consumers -- camera and lock-on -- and leaves the ~670 identity/damage/save consumers
    /// (which go through `IsMainPlayerIns`, comparing against the RAW field) pointing at the real
    /// `PlayerIns`. That split IS the safety property.
    pub(crate) const CAM_OVERRIDE_CHR_INS: usize = 0xb8;
}

/// The `ChrIns.debugFlags` offset FOR THE RUNNING BUILD, or `None` when the build is one nobody
/// has measured.
///
/// # Why this refuses rather than guessing
///
/// The two known answers are eight bytes apart, and eight bytes past 1.16.2's `debugFlags` is
/// 1.17's -- so a wrong guess in either direction lands on a real, live field
/// (`stamina_recovery` one way, `debugFlags`'s neighbour the other). There is no crash to notice
/// and no log line to read; the body simply keeps attacking, or a counter starts drifting.
/// A third build gets `None`, the neuter is skipped, and the log says so.
#[must_use]
pub(crate) fn debug_flags_offset(version: Option<FileVersion>) -> Option<usize> {
    match version? {
        v if v == SUPPORTED_FILE_VERSION => Some(chr_ins::DEBUG_FLAGS_1162),
        v if v == FILE_VERSION_1170 => Some(chr_ins::DEBUG_FLAGS_1170),
        _ => None,
    }
}

/// The `(primary, fallback)` `EneDat` cap offsets FOR THE RUNNING BUILD, or `None` when the build
/// is one nobody has measured.
///
/// # Why this refuses rather than guessing, and what refusing costs
///
/// The two answers are `0x10` apart in both fields, and `EneDat` is a large heap struct, so a wrong
/// choice reads a live neighbouring pointer and follows it -- the residency gate would then be
/// deciding on whatever that field happens to hold. There is no crash to notice.
///
/// Refusing is cheap here in a way it is not for `debugFlags`: the asset-residency gate is one
/// conjunct of a readiness predicate whose other three are byte-proven identical on both builds, so
/// `None` SKIPS that conjunct and readiness still rests on the registration, the `ChrRes` step and
/// the `ChrCtrl` chain. What is lost is the early detection of a chr whose caps loaded but yielded
/// no FLVER -- and the game self-despawns that case anyway, which the registration gate sees.
#[must_use]
pub(crate) fn ene_dat_cap_offsets(version: Option<FileVersion>) -> Option<(usize, usize)> {
    match version? {
        v if v == SUPPORTED_FILE_VERSION => {
            Some((ene_dat::CAP_PRIMARY_1162, ene_dat::CAP_FALLBACK_1162))
        }
        v if v == FILE_VERSION_1170 => {
            Some((ene_dat::CAP_PRIMARY_1170, ene_dat::CAP_FALLBACK_1170))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_ene_dat_cap_offsets_are_known_for_both_supported_builds_and_nothing_else() {
        assert_eq!(
            ene_dat_cap_offsets(Some(SUPPORTED_FILE_VERSION)),
            Some((0xa0, 0x88)),
            "1.16.2"
        );
        assert_eq!(
            ene_dat_cap_offsets(Some(FILE_VERSION_1170)),
            Some((0xb0, 0x90)),
            "1.17 -- BOTH MOVED, by +0x10"
        );
        assert_eq!(
            ene_dat_cap_offsets(Some(FileVersion {
                major: 2,
                minor: 8,
                build: 0,
                revision: 0,
            })),
            None
        );
        assert_eq!(ene_dat_cap_offsets(None), None, "the host, with no game");
    }

    /// THE TWO FIELDS DID NOT MOVE BY THE SAME AMOUNT, and this is what stops anyone "simplifying"
    /// the table into one delta. The primary went `+0x10` and the fallback `+0x8`; deriving the
    /// fallback from the primary's delta lands eight bytes off, on a live pointer rather than on
    /// nothing, and the residency gate would then be following it.
    ///
    /// The primary must also stay ABOVE the fallback in both builds -- reading them in the wrong
    /// order would prefer a cap the game only consults second.
    #[test]
    fn the_two_ene_dat_offsets_moved_by_different_amounts_and_keep_their_order() {
        assert_eq!(ene_dat::CAP_PRIMARY_1170 - ene_dat::CAP_PRIMARY_1162, 0x10);
        assert_eq!(ene_dat::CAP_FALLBACK_1170 - ene_dat::CAP_FALLBACK_1162, 0x8);
        assert_ne!(
            ene_dat::CAP_PRIMARY_1170 - ene_dat::CAP_PRIMARY_1162,
            ene_dat::CAP_FALLBACK_1170 - ene_dat::CAP_FALLBACK_1162,
            "one delta cannot carry both fields forward"
        );
        // `const` blocks rather than plain asserts: both sides are constants, so this is decidable
        // at compile time and a violation should be a BUILD error rather than a test that has to be
        // run to notice. (clippy::assertions_on_constants says the same thing.)
        const { assert!(ene_dat::CAP_PRIMARY_1162 > ene_dat::CAP_FALLBACK_1162) };
        const { assert!(ene_dat::CAP_PRIMARY_1170 > ene_dat::CAP_FALLBACK_1170) };
    }

    /// The band the game scans must sit inside the array it scans, and the request must be big
    /// enough to hold the string it ends with.
    #[test]
    fn the_spawn_band_and_the_request_block_are_self_consistent() {
        const { assert!(chr_set::BAND_FIRST < chr_set::BAND_END) };
        // The entries pointer and the capacity must be different fields of the same struct, and
        // both below the first entry stride -- a `ChrSet` header, not an entry.
        const { assert!(chr_set::CAPACITY < chr_set::ENTRIES) };
        assert_eq!(chr_spawn_request::MODEL_BACKING, 0x08);
        // The inplace buffer must fit inside the request block after `model`.
        let buffer_end = chr_spawn_request::MODEL
            + chr_spawn_request::MODEL_BUFFER
            + chr_spawn_request::MODEL_BUFFER_WCHARS * 2;
        assert!(
            buffer_end <= chr_spawn_request::SIZE,
            "{buffer_end:#x} past the end of a {:#x}-byte request",
            chr_spawn_request::SIZE
        );
        // ...and the string header must not overlap the buffer it points at.
        const { assert!(chr_spawn_request::MODEL_FLAGS < chr_spawn_request::MODEL_BUFFER) };
    }

    /// `3..6` and nothing else, because that is what `ChrRes::IsInLoadedState` compares against.
    #[test]
    fn the_loaded_step_window_is_the_one_the_game_tests() {
        assert_eq!(chr_res::STEP_LOADED_FIRST, 3);
        assert_eq!(chr_res::STEP_LOADED_END, 6);
        for step in [0, 1, 2, 6, 7, 10] {
            assert!(
                !(chr_res::STEP_LOADED_FIRST..chr_res::STEP_LOADED_END).contains(&step),
                "{step}"
            );
        }
        for step in [3, 4, 5] {
            assert!(
                (chr_res::STEP_LOADED_FIRST..chr_res::STEP_LOADED_END).contains(&step),
                "{step}"
            );
        }
    }

    #[test]
    fn the_debug_flags_offset_is_known_for_both_supported_builds_and_nothing_else() {
        assert_eq!(
            debug_flags_offset(Some(SUPPORTED_FILE_VERSION)),
            Some(0x530),
            "1.16.2"
        );
        assert_eq!(
            debug_flags_offset(Some(FILE_VERSION_1170)),
            Some(0x538),
            "1.17 -- IT MOVED"
        );
        // A build nobody measured, and the host, where there is no game image at all.
        assert_eq!(
            debug_flags_offset(Some(FileVersion {
                major: 2,
                minor: 8,
                build: 0,
                revision: 0,
            })),
            None
        );
        assert_eq!(debug_flags_offset(None), None);
    }

    /// The two answers must stay eight bytes apart and in that order; a transposition here is the
    /// shape of the bug the gate exists to prevent.
    #[test]
    fn the_two_debug_flags_offsets_differ_by_exactly_one_pointer() {
        assert_eq!(
            chr_ins::DEBUG_FLAGS_1170 - chr_ins::DEBUG_FLAGS_1162,
            core::mem::size_of::<usize>()
        );
    }

    /// The neuter must not set the no-move bit: the body is driven by the co-location write, and
    /// freezing its move vector would make it fight that write every frame.
    #[test]
    fn the_two_debug_flag_bits_are_distinct_and_only_no_attack_is_used() {
        assert_ne!(chr_ins::DEBUG_FLAG_NO_ATTACK, chr_ins::DEBUG_FLAG_NO_MOVE);
        assert_eq!(chr_ins::DEBUG_FLAG_NO_ATTACK, 0x10);
        assert_eq!(chr_ins::DEBUG_FLAG_NO_MOVE, 0x20);
    }

    /// The mute and the stop-in-flight one-shot are separate bits of one byte, and the engine's
    /// own writer sets them together as `0xC0` -- which is what possession start writes.
    #[test]
    fn the_two_sound_bits_are_distinct_and_pair_into_0xc0() {
        assert_eq!(chr_ins::MUTE_SOUND, 0x40);
        assert_eq!(chr_ins::STOP_PLAYING_SOUNDS, 0x80);
        assert_eq!(chr_ins::MUTE_SOUND & chr_ins::STOP_PLAYING_SOUNDS, 0);
        assert_eq!(chr_ins::MUTE_SOUND | chr_ins::STOP_PLAYING_SOUNDS, 0xc0);
        // Unmuting must clear the mute and leave the rest of the byte alone.
        assert_eq!(!chr_ins::MUTE_SOUND, 0xbf);
    }

    /// The proxy drain is two independent requests and co-location needs both.
    #[test]
    fn the_proxy_flags_are_one_bit_each() {
        assert_eq!(
            chr_ctrl::CHR_PROXY_FLAG_POSITION | chr_ctrl::CHR_PROXY_FLAG_ROTATION,
            3
        );
        assert_eq!(
            chr_ctrl::CHR_PROXY_FLAG_POSITION & chr_ctrl::CHR_PROXY_FLAG_ROTATION,
            0
        );
    }

    /// The override slot must sit past everything else we touch on a `ChrCtrl`, which is the
    /// cheap arithmetic proof that it is a field of its own and not an alias of one of them.
    #[test]
    fn the_manipulator_override_is_not_an_alias_of_any_field_we_write() {
        for other in [
            chr_ctrl::OWNER,
            chr_ctrl::MANIPULATOR,
            chr_ctrl::MODIFIER,
            chr_ctrl::CHR_PROXY_FLAGS,
            chr_ctrl::RAGDOLL_POSITION,
            chr_ctrl::RAGDOLL_ROTATION,
        ] {
            assert!(
                chr_ctrl::MANIPULATOR_OVERRIDE > other,
                "{other:#x} is not below the override slot"
            );
        }
        // ...and the two 16-byte vectors do not overlap each other.
        assert_eq!(
            chr_ctrl::RAGDOLL_ROTATION - chr_ctrl::RAGDOLL_POSITION,
            0x10
        );
    }

    /// The four `AiIns` fields one frame of movement writes must be four DIFFERENT fields, and
    /// the 16-byte `wantToMoveTo` must not run over the `int` that follows it in the struct.
    ///
    /// `wantToMoveTo` is at `+0xc3e0` and the desired-orientation vector the engine derives from
    /// it is at `+0xc3f0` -- adjacent, which is exactly the arrangement where a `write_vec4` one
    /// slot wide too far silently overwrites the heading every frame.
    #[test]
    fn the_movement_fields_are_four_distinct_non_overlapping_fields() {
        let fields = [
            ai_ins::WANT_TO_MOVE_TO,
            ai_ins::WALK_TYPE,
            ai_ins::PATH_DATA,
            ai_ins::TURN_TARGET,
        ];
        for (i, a) in fields.iter().enumerate() {
            for b in &fields[i + 1..] {
                assert_ne!(a, b, "two movement writes share one offset");
            }
        }
        // The 16 bytes of `wantToMoveTo` end before `walkType` begins...
        const { assert!(ai_ins::WANT_TO_MOVE_TO + 0x10 <= ai_ins::WALK_TYPE) };
        // ...and stop short of `+0xc3f0`, the orientation the engine writes there itself.
        assert_eq!(ai_ins::WANT_TO_MOVE_TO + 0x10, 0xc3f0);
    }

    /// `walkType`'s three values are distinct, and the two moving ones are what `CSAiFunc::MoveTo`
    /// produces: it writes `2 - (walk != 0)`, so the pair is exactly `{1, 2}` and nothing else.
    #[test]
    fn the_walk_types_are_the_pair_moveto_writes_plus_the_stop_clearmoverequest_writes() {
        assert_eq!(ai_ins::WALK_TYPE_STOP, 0, "ClearMoveRequest writes zero");
        assert_eq!(
            ai_ins::WALK_TYPE_RUN - i32::from(true),
            ai_ins::WALK_TYPE_WALK
        );
        assert_eq!(
            ai_ins::WALK_TYPE_RUN - i32::from(false),
            ai_ins::WALK_TYPE_RUN
        );
        // ...and neither moving value can be mistaken for the stop.
        assert_ne!(ai_ins::WALK_TYPE_WALK, ai_ins::WALK_TYPE_STOP);
        assert_ne!(ai_ins::WALK_TYPE_RUN, ai_ins::WALK_TYPE_STOP);
    }

    /// `TARGET_SELF` is NEGATIVE, and that is the whole reason the field is written through an
    /// `i32` rather than the `u32` every other write in this crate uses. A `usize`/`u32` spelling
    /// of `-1` that lost its sign would write `TARGET_NONE`'s neighbour, not `TARGET_SELF`.
    #[test]
    fn the_turn_target_is_a_signed_enum_and_self_is_negative() {
        const { assert!(ai_ins::TURN_TARGET_SELF < 0) };
        assert_eq!(ai_ins::TURN_TARGET_SELF as u32, 0xffff_ffff);
    }
}
