// The two param getters this DLL hooks, declared exactly once.
//
// `include!`d by both `build.rs`, which assembles each pinned window against the 2.7.2.0 image at
// `GAME_IMAGE_BASE + rva + offset`, and `src/lib.rs`, which uses the rva on the running 2.7.2.0
// build and compares the same windows at the same offsets before hooking. Neither spells an
// address of its own, so a corrected entry moves every window with it.
//
// Every constant carries `#[allow(dead_code)]` because each includer uses only part of the file.

/// ELDEN RING's preferred image base. The offline images are flat, so `VA = base + RVA` for
/// every section.
#[allow(dead_code)]
const GAME_IMAGE_BASE: u64 = 0x1_4000_0000;

/// `GetSpEffectParam(out, id)`, 2.7.2.0 rva.
/// `out = {row* @+0, id @+8, u8 @+0xc}`.
#[allow(dead_code)]
const GET_SP_EFFECT_PARAM_RVA: usize = 0xd5_3a90;

/// The SpEffectVfxParam getter `(out, id)`, 2.7.2.0 rva (1.16.2 `FUN_140d50bd0`).
/// `out = {id @+0, row* @+8, u16 @+0x10}`.
#[allow(dead_code)]
const GET_SP_EFFECT_VFX_PARAM_RVA: usize = 0xd5_4070;

/// Both getters open `push <reg>; sub rsp,0x40; mov qword [rsp+0x20],-2`. The window starts at
/// the third instruction because the running `GetSpEffectParam` has an Arxan `jmp rel32` stub over
/// its first five bytes.
#[allow(dead_code)]
const GETTER_FRAME_WINDOW_OFFSET: usize = 0x6;

/// `xor r8d,r8d; lea edx,[r8+0xf]` ahead of `call GetParamResCap` in `GetSpEffectParam`: table
/// 0xf is SpEffectParam.
#[allow(dead_code)]
const SP_EFFECT_TABLE_WINDOW_OFFSET: usize = 0x6f;
/// `mov [r14],rbx`: the store of the row pointer into `out+0`.
#[allow(dead_code)]
const SP_EFFECT_ROW_STORE_WINDOW_OFFSET: usize = 0x15d;
/// Where `GetSpEffectParam` keeps the row pointer in its out struct.
#[allow(dead_code)]
const SP_EFFECT_OUT_ROW_SLOT: usize = 0;

/// `xor r8d,r8d; lea edx,[r8+0x10]` ahead of `call GetParamResCap` in the SpEffectVfxParam getter:
/// table 0x10 is SpEffectVfxParam.
#[allow(dead_code)]
const SP_EFFECT_VFX_TABLE_WINDOW_OFFSET: usize = 0x67;
/// `mov [rbx+8],rax`: the row pointer into `out+8` on a hit.
#[allow(dead_code)]
const SP_EFFECT_VFX_HIT_STORE_WINDOW_OFFSET: usize = 0xbd;
/// `mov [rbx+8],rbp`: the same slot on a miss.
#[allow(dead_code)]
const SP_EFFECT_VFX_MISS_STORE_WINDOW_OFFSET: usize = 0xc5;
/// Where the SpEffectVfxParam getter keeps the row pointer in its out struct.
#[allow(dead_code)]
const SP_EFFECT_VFX_OUT_ROW_SLOT: usize = 8;
