//! `CS::ChrAsm` member offsets the loading-portrait pipeline reads out of live game memory.
//!
//! Moved verbatim from er-quickload `constants/player_correctness.rs` with the loading-cover
//! extraction; the root re-exports every name through its `constants.rs` glob, so the flat
//! namespace is unchanged. Every offset is derived from the typed `fromsoftware-rs` layout
//! rather than hard-coded, so struct drift upstream fails the build instead of reading a
//! neighbouring field at runtime.

use eldenring::cs::ChrAsm;

pub const CHR_ASM_SIZE: usize = core::mem::size_of::<ChrAsm>();
/// Runtime `ChrAsm` member offsets, for assembling a runtime-layout image from the SERIALIZED save
/// sections (which store the same blocks in a different order; see
/// `SerializedSaveSlot::runtime_chr_asm_image`).
pub const CHR_ASM_EQUIPMENT_OFFSET: usize = core::mem::offset_of!(ChrAsm, equipment);
pub const CHR_ASM_GAITEM_HANDLES_OFFSET: usize = core::mem::offset_of!(ChrAsm, gaitem_handles);
pub const CHR_ASM_EQUIPMENT_PARAM_IDS_OFFSET: usize =
    core::mem::offset_of!(ChrAsm, equipment_param_ids);
/// Index of `ProtectorHead` within `ChrAsm::gaitem_handles` / `ChrAsm::equipment_param_ids`; the four
/// armor slots are head/chest/hands/legs at `+0..+3`. Grounded in the disassembly, not assumed:
/// `CS::ChrAsm::EquipProtectorOrAccessory` (deobf 0x1403bf490) is literally `add $0xc,%edx; jmp
/// EquipItem`, and `CS::ChrAsm::GetProtectorParamIdBySlot` (deobf 0x1403be950) is `lea 0xc(%rdx),%eax
/// ; movslq %eax,%rdx ; mov 0x7c(%rcx,%rdx,4),%eax ; ret`.
pub const CHR_ASM_PROTECTOR_HEAD_INDEX: usize = 12;
/// Number of protector (armor) slots the portrait resolution oracle covers: head, chest, hands, legs.
pub const CHR_ASM_PROTECTOR_SLOT_COUNT: usize =
    crate::portrait_equip::PORTRAIT_EQUIP_PROTECTOR_SLOT_COUNT;
/// Entries in `ChrAsm::gaitem_handles` and in `ChrAsm::equipment_param_ids`. The ctor pins it: after
/// `lea 0x7c(%rsi),%rdi` it runs `mov $0x16,%ecx ; rep stos %eax,(%rdi)` with `eax = -1`
/// (deobf 0x1403be213..0x1403be222), i.e. 0x16 = 22 dwords.
pub const CHR_ASM_EQUIPMENT_ENTRY_COUNT: usize = 22;
/// `ChrAsm::unk0` (+0x00). Together with `unkd4`/`unkd8` this is a WHOLE-OUTFIT OVERRIDE input, not
/// padding: see `CHR_ASM_OVERRIDE_ABSENT`. Its typed field is private in `fromsoftware-rs`, so the
/// offset is spelled out here rather than taken from `offset_of!`; the ctor writes it first
/// (`movl $0xffffffff,(%rcx)` at deobf 0x1403be1d0), which is what pins it to +0.
pub const CHR_ASM_UNK0_OFFSET: usize = 0x00;
/// `ChrAsm::unkd4` (+0xd4) -- the HEAD whole-outfit override. Derived from the typed layout rather
/// than hard-coded: the param-id array is the last public field, so `unkd4` is exactly one array past
/// it. The ctor's `movq $-1,0xd4(%rsi)` (deobf 0x1403be208) is the independent confirmation, and it
/// writes EIGHT bytes -- so that one instruction sets both `unkd4` and `unkd8`.
pub const CHR_ASM_UNKD4_OFFSET: usize = CHR_ASM_EQUIPMENT_PARAM_IDS_OFFSET
    + CHR_ASM_EQUIPMENT_ENTRY_COUNT * core::mem::size_of::<i32>();
/// `ChrAsm::unkd8` (+0xd8) -- the CHEST/HANDS/LEGS whole-outfit override.
pub const CHR_ASM_UNKD8_OFFSET: usize = CHR_ASM_UNKD4_OFFSET + core::mem::size_of::<i32>();
/// The value `unk0`/`unkd4`/`unkd8` must hold for the renderer to dress a character from its per-slot
/// `equipment_param_ids`. These three fields are read with SIGNED tests by the model-resource request
/// `FUN_1409e6fb0` (deobf 0x1409e7553..0x1409e75b6, every branch a `js`), and a NON-NEGATIVE value in
/// any of them FORCES the whole outfit from arithmetic on that value instead:
///
/// ```text
///   head  (category 1): unkd4 >= 0 -> unkd4
///   chest (category 2): unkd8 >= 0 -> unkd8 + 100
///   hands (category 3): unkd8 >= 0 -> unkd8 + 200 ; else unk0 >= 0 -> unk0 + 200
///   legs  (category 4): unkd8 >= 0 -> unkd8 + 300
/// ```
///
/// A zero-initialised image therefore renders param ids 0/100/200/300 -- rows that do not exist -- so
/// NOTHING resolves, not even the bare-body defaults the native profile feed equips into hands and
/// legs. That is the entirely-nude portrait (bd er-effects-rs-wncc). The ctor
/// `CS::ChrAsm::ChrAsm` (deobf 0x1403be1b0) sets all three to -1, which is why a ctor-built ChrAsm
/// never hits the override path and our hand-built image must match it.
pub const CHR_ASM_OVERRIDE_ABSENT: i32 = crate::portrait_equip::PORTRAIT_EQUIP_OVERRIDE_ABSENT;
/// `CSMenuProfModelRend` -> its LIVE stage-0 `ChrAsm`, the one the model build actually reads.
///
/// DO NOT SUBSTITUTE +0x548. The renderer holds THREE `ChrAsm` stages: +0x548 is the INBOX that
/// `set_model_source` writes, +0x33c is the staged copy, and +0x130 is live. `STEP_Init_Setup`
/// (0x140bb9ca0) snapshots the inbox into +0x33c exactly ONCE and never dereferences it again, while
/// `STEP_Wait_Play` (0x140bba080) re-runs the model-resource request `FUN_1409e6fb0` against +0x130
/// EVERY frame a model instance exists. An oracle reading +0x548 therefore reports what we handed the
/// renderer, not what it is rendering -- which is precisely how the PR #128 oracle passed a run the
/// user saw as nude (bd er-effects-rs-91l5).
pub const PROFILE_RENDERER_CHR_ASM_LIVE_OFFSET: usize = 0x130;
