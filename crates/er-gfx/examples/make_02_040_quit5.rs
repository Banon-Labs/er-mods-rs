//! Derive the 5-row System->Quit `02_040_optionsetting` movie from a vanilla one, and report the
//! fingerprint the DLL pins.
//!
//! The DLL performs this same transform in memory against the game's own Scaleform MemoryFile;
//! this example exists so the derived bytes can be inspected offline and so
//! `QUIT5_WIN_LEN`/`QUIT5_WIN_FNV1A64` can be RE-DERIVED rather than hand-copied when the edit
//! set changes. It deliberately calls `apply_edits` directly instead of
//! [`er_gfx::options_02_040::quit5`], because that wrapper refuses to return bytes whose
//! fingerprint disagrees with the pinned constants -- which is exactly the situation you are in
//! while working out what the new constants should be.
//!
//! ```text
//! cargo run -p er-gfx --example make_02_040_quit5 -- <vanilla.gfx> [out.gfx]
//! ```

use er_game_base::fnv1a::fnv1a64;
use er_gfx::Movie;
use er_gfx::edit::apply_edits;
use er_gfx::options_02_040::{
    OPTIONS_02_040_QUIT5_EDITS, QUIT5_GRID_CELL_NAMES, QUIT5_WIN_FNV1A64, QUIT5_WIN_LEN,
    is_known_vanilla_win,
};

fn main() {
    let mut args = std::env::args().skip(1);
    let Some(input) = args.next() else {
        eprintln!("usage: make_02_040_quit5 <vanilla.gfx> [out.gfx]");
        std::process::exit(2);
    };
    let vanilla = std::fs::read(&input).expect("read vanilla movie");
    println!(
        "in   len={} fnv1a64=0x{:016x} known_vanilla={}",
        vanilla.len(),
        fnv1a64(&vanilla),
        is_known_vanilla_win(&vanilla)
    );

    let mut movie = Movie::parse(&vanilla).expect("parse vanilla movie");
    let applied = apply_edits(&mut movie, OPTIONS_02_040_QUIT5_EDITS).expect("apply quit5 edits");
    let out = movie.write().expect("write derived movie");
    println!(
        "out  len={} fnv1a64=0x{:016x} edits_applied={applied}",
        out.len(),
        fnv1a64(&out)
    );
    println!(
        "pinned QUIT5_WIN_LEN={QUIT5_WIN_LEN} QUIT5_WIN_FNV1A64=0x{QUIT5_WIN_FNV1A64:016x} -> {}",
        if out.len() == QUIT5_WIN_LEN && fnv1a64(&out) == QUIT5_WIN_FNV1A64 {
            "MATCH"
        } else {
            "DRIFT (update the constants in options_02_040.rs)"
        }
    );
    println!("grid cells the Quit tab measures: {QUIT5_GRID_CELL_NAMES:?}");

    if let Some(path) = args.next() {
        std::fs::write(&path, &out).expect("write output movie");
        println!("wrote {path}");
    }
}
