//! How much of one model's surface another model already has: the share of the reference's
//! vertex positions (rounded to a centimetre, in each model's own frame) that also occur in each
//! candidate, and the reverse. A ruin cut from an intact piece keeps the intact piece's
//! unbroken vertices where they were, so the pair shows up as a large shared fraction.
//!
//! `cargo run -p er-flver --example flver-vertex-overlap -- <ref.flver> <candidate.flver>...`

use std::collections::HashSet;
use std::env;
use std::fs;

fn keys(path: &str) -> Option<HashSet<[i32; 3]>> {
    let bytes = fs::read(path).ok()?;
    let model = er_flver::parse(&bytes).ok()?;
    let mut out = HashSet::new();
    for mesh in &model.meshes {
        for p in &mesh.positions {
            out.insert([
                (p[0] * 100.0).round() as i32,
                (p[1] * 100.0).round() as i32,
                (p[2] * 100.0).round() as i32,
            ]);
        }
    }
    Some(out)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let reference = keys(&args[0]).expect("reference");
    for path in &args[1..] {
        let Some(other) = keys(path) else { continue };
        let shared = reference.intersection(&other).count();
        let name = path.rsplit('/').next().unwrap_or(path);
        println!(
            "{name}\tref {}\tother {}\tshared {shared}\tof-ref {:.3}\tof-other {:.3}",
            reference.len(),
            other.len(),
            shared as f64 / reference.len().max(1) as f64,
            shared as f64 / other.len().max(1) as f64
        );
    }
}
