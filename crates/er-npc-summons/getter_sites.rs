// The two typed param getters this DLL serves row copies through, declared exactly once.
//
// `include!`d by both `build.rs`, which assembles each pinned window against the 1.17.1 image at
// `GAME_IMAGE_BASE + rva + offset`, and `src/mimic_hooks.rs`, which compares the same windows at
// the same offsets on the running build before it trusts a getter's out struct. Neither spells an
// offset of its own, so a corrected one moves every window with it.
//
// Both getters are the same template instantiated for two tables: `(out, id)`, a binary search of
// the table `GetParamResCap` returns, then `out = {id @+0, row* @+8}` with a null row on a miss.
// Every byte at these offsets is identical in eldenring-deobf-1.17.1.bin and
// eldenring-deobf-1.17.2.bin (2.7.2.0).
//
// Every constant carries `#[allow(dead_code)]` because each includer uses only part of the file.

/// ELDEN RING's preferred image base. The offline images are flat, so `VA = base + RVA` for
/// every section.
#[allow(dead_code)]
const GAME_IMAGE_BASE: u64 = 0x1_4000_0000;

/// `GetBuddyParam(out, id)` on 1.17.1 (2.7.2.0 `0x140d29700`, 1.16.2 `0x140d26890`). The DLL
/// detours it through the 1.16.2 ledger (`addr::GET_BUDDY_PARAM`) and checks these windows at the
/// address that ledger resolves.
#[allow(dead_code)]
const GET_BUDDY_PARAM_1171_RVA: usize = 0xd2_8010;

/// `GetCharaInitParam(out, id)` on 1.17.1 (2.7.2.0 `0x140d37660`, 1.16.2 `0x140d347f0`). No
/// ledger row carries it, so it is hooked at this address carried by `carry_1171_rva`.
#[allow(dead_code)]
const GET_CHARA_INIT_PARAM_1171_RVA: usize = 0xd3_5f70;

/// Both getters open `push rdi; sub rsp,0x40; mov qword [rsp+0x20],-2`. The window starts at the
/// third instruction, past the five bytes an Arxan stub may have replaced at the entry.
#[allow(dead_code)]
const GETTER_FRAME_WINDOW_OFFSET: usize = 0x6;

/// `xor r8d,r8d` and the table index handed to `GetParamResCap`: `mov edx,0x80` (BuddyParam,
/// solo param 128) or `lea edx,[r8+0x17]` (CharaInitParam, 23). This is the window that says which
/// table a getter serves.
#[allow(dead_code)]
const GETTER_TABLE_WINDOW_OFFSET: usize = 0x67;

/// `mov [rsi],edi; mov [rsi+8],rbx`: the id into `out+0` and the row pointer, null on a miss, into
/// `out+8`, for every id `>= 0`.
#[allow(dead_code)]
const GETTER_OUT_STORE_WINDOW_OFFSET: usize = 0x145;

/// `mov [rcx],edi; mov [rcx+8],rbx`: the same two stores for a negative id, whose row is null.
#[allow(dead_code)]
const GETTER_NEGATIVE_ID_STORE_WINDOW_OFFSET: usize = 0x14d;

/// Where both getters put the row pointer in their out struct.
#[allow(dead_code)]
const GETTER_OUT_ROW_SLOT: usize = 8;
