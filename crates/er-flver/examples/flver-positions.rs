//! Every vertex position of a FLVER, in the model's own game frame (no axis flip), one
//! `x y z` line per vertex, all meshes concatenated. Feeds `scripts/er-asset-join-check.py`,
//! which places them at their MSB transforms.
//!
//! `cargo run -p er-flver --example flver-positions -- <model.flver>`

use std::env;
use std::fs;
use std::io::{self, BufWriter, Write};

fn main() {
    let path = env::args()
        .nth(1)
        .expect("usage: flver-positions <model.flver>");
    let bytes = fs::read(&path).expect("read flver");
    let model = er_flver::parse(&bytes).expect("parse flver");
    let mut out = BufWriter::new(io::stdout().lock());
    for mesh in &model.meshes {
        for p in &mesh.positions {
            writeln!(out, "{} {} {}", p[0], p[1], p[2]).expect("write");
        }
    }
}
