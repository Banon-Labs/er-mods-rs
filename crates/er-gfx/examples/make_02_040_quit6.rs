//! Derive every supported System->Quit `02_040_optionsetting` grid from a vanilla movie, and
//! report the fingerprint each one pins.
//!
//! The DLL performs this same transform in memory against the game's own Scaleform MemoryFile;
//! this example exists so the derived bytes can be inspected offline and so
//! `QUIT_GRID_FINGERPRINTS` can be re-derived rather than hand-copied when the edit tables
//! change. It deliberately calls `apply_edits` directly instead of
//! [`er_gfx::options_02_040::quit_grid`], because that wrapper refuses a size whose fingerprint
//! is still an unpinned zero -- which is exactly the situation you are in while working out what
//! the number should be.
//!
//! ```text
//! cargo run -p er-gfx --example make_02_040_quit6 -- <vanilla.gfx> [out.gfx]
//! ```
//!
//! `out.gfx`, when given, receives the six-cell movie: the one this repo's own row set uses.

use er_game_base::fnv1a::fnv1a64;
use er_gfx::Movie;
use er_gfx::edit::apply_edits;
use er_gfx::options_02_040::{
    MAX_GRID_ITEMS, MIN_GRID_ITEMS, OPTIONS_02_040_CELL_EDITS, OPTIONS_02_040_SHAPE_EDIT,
    QUIT_GRID_CELL_NAMES, QUIT_GRID_FINGERPRINTS, is_known_vanilla_win,
};

/// Apply the shape edit and the first `items - MIN_GRID_ITEMS` cells, as `quit_grid` does.
fn derive(vanilla: &[u8], items: usize) -> Vec<u8> {
    let cells = &OPTIONS_02_040_CELL_EDITS[..items - MIN_GRID_ITEMS];
    let mut movie = Movie::parse(vanilla).expect("parse vanilla movie");
    if !cells.is_empty() {
        apply_edits(&mut movie, OPTIONS_02_040_SHAPE_EDIT).expect("apply the shape edit");
        apply_edits(&mut movie, cells).expect("apply the cell edits");
    }
    movie.write().expect("write the derived movie")
}

fn main() {
    let mut args = std::env::args().skip(1);
    let Some(input) = args.next() else {
        eprintln!("usage: make_02_040_quit6 <vanilla.gfx> [out.gfx]");
        std::process::exit(2);
    };
    let vanilla = std::fs::read(&input).expect("read vanilla movie");
    println!(
        "in    len={} fnv1a64=0x{:016x} known_vanilla={}",
        vanilla.len(),
        fnv1a64(&vanilla),
        is_known_vanilla_win(&vanilla)
    );

    for items in MIN_GRID_ITEMS..=MAX_GRID_ITEMS {
        let out = derive(&vanilla, items);
        let (want_len, want_fnv) = QUIT_GRID_FINGERPRINTS[items - MIN_GRID_ITEMS];
        let verdict = if want_len == 0 {
            "UNPINNED (paste these into QUIT_GRID_FINGERPRINTS)"
        } else if out.len() == want_len && fnv1a64(&out) == want_fnv {
            "MATCH"
        } else {
            "DRIFT (update the constants in options_02_040.rs)"
        };
        println!(
            "items={items} len={} fnv1a64=0x{:016x} cells={:?} -> {verdict}",
            out.len(),
            fnv1a64(&out),
            &QUIT_GRID_CELL_NAMES[..items]
        );
    }

    if let Some(path) = args.next() {
        let out = derive(&vanilla, 6);
        std::fs::write(&path, &out).expect("write output movie");
        println!("wrote {path}");
    }
}
