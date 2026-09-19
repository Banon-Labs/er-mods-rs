//! Reading `WorldMapPieceParam` out of the live process, without calling anything.
//!
//! # Why a read and not a call
//!
//! `SoloParamRepositoryImp::GetParamResCap` is fifteen instructions and has no side effects:
//!
//! ```text
//! cmp    $0xc2,%edx                      ; paramType < 194
//! movslq %edx,%rdx
//! lea    (%rdx,%rdx,8),%rax              ; index * 9
//! cmp    0x80(%rcx,%rax,8),%r8d          ; capIndex < holder.res_cap_count
//! ...
//! mov    0x88(%rcx,%rdx,8),%rax          ; holder.paramResCaps[capIndex]
//! ```
//!
//! So the holder array starts at `repo+0x80`, each holder is `9 * 8 = 72` bytes, the count sits
//! first and the cap pointers follow at `+0x88`. Every one of those is a load. Reproducing the
//! arithmetic here costs nothing and buys the thing that matters in a game task: no address is
//! ever jumped to, so a wrong offset yields a wrong number rather than a call into the middle of
//! an instruction. That failure mode is not hypothetical -- carrying this function's own address
//! forward from 1.16.2 by the documented `+0x70` landed mid-instruction at `0x140d4ccc0`.
//!
//! # The chain
//!
//! | step | where |
//! |---|---|
//! | holder count | `repo + 0x80 + index * 72` |
//! | `ParamResCap*` | `repo + 0x88 + (index * 9) * 8` |
//! | `FD4ParamResCap*` | `ParamResCap + 0x80` |
//! | param blob length | `FD4ParamResCap + 0x78` |
//! | param blob | `FD4ParamResCap + 0x80` |
//!
//! Every read goes through `safe_read_*`, so a repository that is not up yet -- or a build whose
//! layout moved -- produces `None` and the caller simply has no names this frame.

#[cfg(windows)]
use er_invasion_warp_core::map_piece::{MapPiece, WORLD_MAP_PIECE_PARAM_INDEX, parse_param_file};

/// First holder in `SoloParamRepositoryImp`'s array.
#[cfg(windows)]
const HOLDER_ARRAY_OFFSET: usize = 0x80;
/// Bytes per holder -- the `index * 9` of the `lea`, times eight.
#[cfg(windows)]
const HOLDER_STRIDE: usize = 72;
/// Where a holder's `ParamResCap*` array begins, relative to the repository.
#[cfg(windows)]
const HOLDER_CAPS_OFFSET: usize = 0x88;
/// `ParamResCap + 0x80` -> `FD4ParamResCap*`.
#[cfg(windows)]
const PARAM_RES_CAP_FD4_OFFSET: usize = 0x80;
/// `FD4ParamResCap + 0x78` -> the blob's length in bytes.
#[cfg(windows)]
const FD4_PARAM_RES_CAP_SIZE_OFFSET: usize = 0x78;
/// `FD4ParamResCap + 0x80` -> the blob.
#[cfg(windows)]
const FD4_PARAM_RES_CAP_FILE_OFFSET: usize = 0x80;

/// A blob larger than this is not a param, it is a bad read.
///
/// `WorldMapPieceParam` is 34 rows of 64 bytes plus a header; four megabytes is orders of
/// magnitude of headroom and still small enough that a garbage length cannot ask for a copy that
/// stalls a game task.
#[cfg(windows)]
const MAX_PARAM_BLOB: usize = 4 * 1024 * 1024;

/// Every map piece the live regulation defines, or `None` before the param tables are up.
///
/// # Safety
///
/// Game task thread. Reads only; nothing here is called and nothing is written.
#[cfg(windows)]
#[must_use]
pub unsafe fn map_pieces() -> Option<Vec<MapPiece>> {
    let base = er_game_base::mem::game_module_base().ok()?;
    let repo = er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::SOLO_PARAM_REPOSITORY_GLOBAL_RVA,
        "SOLO_PARAM_REPOSITORY_GLOBAL_RVA",
    );
    if repo == 0 {
        return None;
    }

    let index = WORLD_MAP_PIECE_PARAM_INDEX;
    // The engine's own bound. A param table shorter than this index is a build we do not know.
    // Signed, because that is how the engine compares it -- and a negative count is a wrong
    // offset rather than an empty table, so it must refuse rather than read on.
    let count = unsafe {
        er_game_base::mem::safe_read_i32(repo + HOLDER_ARRAY_OFFSET + index * HOLDER_STRIDE)
    }?;
    if count <= 0 {
        return None;
    }
    let cap =
        unsafe { er_game_base::mem::safe_read_usize(repo + HOLDER_CAPS_OFFSET + (index * 9) * 8) }?;
    if cap == 0 {
        return None;
    }
    let fd4 = unsafe { er_game_base::mem::safe_read_usize(cap + PARAM_RES_CAP_FD4_OFFSET) }?;
    if fd4 == 0 {
        return None;
    }
    let size = unsafe { er_game_base::mem::safe_read_usize(fd4 + FD4_PARAM_RES_CAP_SIZE_OFFSET) }?;
    let blob = unsafe { er_game_base::mem::safe_read_usize(fd4 + FD4_PARAM_RES_CAP_FILE_OFFSET) }?;
    if blob == 0 || size == 0 || size > MAX_PARAM_BLOB {
        return None;
    }

    // Copied byte by byte through the fault-tolerant reader rather than with a slice over game
    // memory: the length came out of that memory too, and a page boundary inside it must end the
    // copy rather than the process.
    let mut bytes = Vec::with_capacity(size);
    for offset in 0..size {
        match unsafe { er_game_base::mem::safe_read_u8(blob + offset) } {
            Some(byte) => bytes.push(byte),
            None => break,
        }
    }

    let pieces = parse_param_file(&bytes);
    (!pieces.is_empty()).then_some(pieces)
}

/// The `PlaceName` text id covering a map-space position, or `-1` when none does.
///
/// `-1` is the same "no name" sentinel the pin path uses, so this drops in where that path gives
/// up. It is never a fabricated id: an id that resolves in no FMG renders as the literal
/// `?PlaceName?`, which is worse on screen than an unnamed pin.
///
/// The pieces are re-read on each call rather than cached. This runs where the pin path has
/// already failed, which is rare, and a cache would have to be invalidated on a regulation
/// hot-reload that this module has no way to observe.
///
/// # Safety
///
/// Game task thread. Reads only.
#[cfg(windows)]
#[must_use]
pub unsafe fn place_name_text_id_at(x: f32, z: f32) -> i32 {
    let Some(pieces) = (unsafe { map_pieces() }) else {
        return -1;
    };
    er_invasion_warp_core::map_piece::place_name_at(&pieces, x, z).unwrap_or(-1)
}

/// Host build: no game memory, so no name.
#[cfg(not(windows))]
#[must_use]
pub fn place_name_text_id_at(_x: f32, _z: f32) -> i32 {
    -1
}

#[cfg(not(windows))]
#[must_use]
pub fn map_pieces() -> Option<Vec<er_invasion_warp_core::map_piece::MapPiece>> {
    None
}

#[cfg(test)]
mod tests {
    /// The holder arithmetic is the decompiled function's, not a guess.
    ///
    /// `GetParamResCap` computes `index * 9` and indexes eight-byte slots from `repo+0x80` for the
    /// count and `repo+0x88` for the caps. Spelled out for `WorldMapPieceParam` so a future edit
    /// that "simplifies" the stride has something to fail against.
    #[test]
    fn the_holder_offsets_match_the_decompiled_lookup() {
        // Not named `INDEX`. `scripts/rva_symbols.py` resolves an upstream `Type::INDEX` by
        // pooling this tree's declarations of the last path segment first, so a bare `INDEX`
        // anywhere in `crates/` answers for every one of them -- this constant made
        // `SpEffectParam::INDEX` evaluate to 88 instead of upstream's 15 and failed that
        // script's selftest from a file with nothing to do with `SpEffectParam`.
        const PIECE_INDEX: usize = er_invasion_warp_core::map_piece::WORLD_MAP_PIECE_PARAM_INDEX;
        const {
            assert!(
                PIECE_INDEX < 0xc2,
                "the engine refuses an index at or above its own bound"
            )
        };
        // repo + 0x80 + 0x58 * 72
        assert_eq!(0x80 + PIECE_INDEX * 72, 0x1940);
        // repo + 0x88 + (0x58 * 9) * 8 -- the same holder, eight bytes on from its count
        assert_eq!(0x88 + (PIECE_INDEX * 9) * 8, 0x1948);
        assert_eq!(0x88 + (PIECE_INDEX * 9) * 8, 0x80 + PIECE_INDEX * 72 + 8);
    }
}
