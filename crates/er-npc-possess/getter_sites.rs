// The `LockCamParam` getter this DLL hooks, declared exactly once.
//
// `include!`d by both `build.rs`, which assembles each pinned window against the 1.17.1 image at
// `GAME_IMAGE_BASE + rva + offset`, and `src/camera/mod.rs`, whose `getter` module carries the
// rva to the running build and compares the same windows at the same offsets before hooking.
// Neither spells an address of its own, so a corrected entry moves every window with it.
//
// Every constant carries `#[allow(dead_code)]` because each includer uses only part of the file.

/// Elden Ring's preferred image base. The offline images are flat, so `VA = base + RVA` for
/// every section.
#[allow(dead_code)]
const GAME_IMAGE_BASE: u64 = 0x1_4000_0000;

/// `LookupLockCamParam(out, id)` on 1.17.1 (2.7.2.0 `0x140d40ba0`, 1.16.2 `0x140d3dcf0`, the name
/// the 1.16.2 dump gives it). `out = {row* @+0, id @+8}`, the dump's `LockCamParamLookupResult`.
///
/// A `.pdata` entry of length `0x168` in all three images. `CS::ChrExFollowCam::ApplyZoomLerp`
/// calls it every frame at `0x1403b76ed` (1.17.1 and 2.7.2.0) with the id it resolved from
/// `ChrExFollowCam+0x468`; it is the only function that hands table index `0x1f` to
/// `GetParamResCap`.
#[allow(dead_code)]
const LOOKUP_LOCK_CAM_PARAM_1171_RVA: usize = 0xd3_f4b0;

/// The getter opens `push rdi; sub rsp,0x40; mov qword [rsp+0x20],-2`. The window starts at the
/// third instruction, because the first five bytes are what MinHook (or an Arxan stub) replaces.
#[allow(dead_code)]
const LOCK_CAM_FRAME_WINDOW_OFFSET: usize = 0x6;

/// `xor r8d,r8d; lea edx,[r8+0x1f]` ahead of `call GetParamResCap`: table 0x1f is `LockCamParam`.
#[allow(dead_code)]
const LOCK_CAM_TABLE_WINDOW_OFFSET: usize = 0x67;

/// `mov [rsi+8],edi; mov [rsi],rbx`: the id into `out+8` and the row pointer into `out+0`, on the
/// one path every non-negative id takes, hit or miss. A miss leaves `rbx` at the zero it was
/// cleared to at `+0x23`, so a miss writes a null row.
#[allow(dead_code)]
const LOCK_CAM_OUT_STORE_WINDOW_OFFSET: usize = 0x145;

/// Where the getter keeps the row pointer in its out struct.
#[allow(dead_code)]
const LOCK_CAM_OUT_ROW_SLOT: usize = 0;

/// Where the getter keeps the id in its out struct.
#[allow(dead_code)]
const LOCK_CAM_OUT_ID_SLOT: usize = 8;
