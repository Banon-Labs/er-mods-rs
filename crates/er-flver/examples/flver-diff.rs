//! The triangles one model has that another lacks, both in their own frame: a triangle of `<b>`
//! counts as extra when any of its corners is a position (rounded to a centimetre) that `<a>`
//! does not have. Damage-ladder twins in the asset set share their unbroken vertices exactly, so
//! this separates what one rung adds over another. Prints the extra triangle count and bounds per
//! material, and writes the extra triangles as OBJ (x negated, like flver-obj) when given a path.
//!
//! `cargo run -p er-flver --example flver-diff -- <a.flver> <b.flver> [out.obj]`

use std::collections::{BTreeMap, HashSet};
use std::env;
use std::fmt::Write as _;
use std::fs;

fn key(p: &[f32; 3]) -> [i32; 3] {
    [
        (p[0] * 100.0).round() as i32,
        (p[1] * 100.0).round() as i32,
        (p[2] * 100.0).round() as i32,
    ]
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let read = |path: &str| er_flver::parse(&fs::read(path).expect(path)).expect(path);
    let a = read(&args[0]);
    let b = read(&args[1]);
    let have: HashSet<[i32; 3]> = a
        .meshes
        .iter()
        .flat_map(|m| m.positions.iter().map(key))
        .collect();
    let mut obj = String::new();
    let mut base = 1usize;
    let mut per: BTreeMap<String, (usize, usize, [f32; 3], [f32; 3])> = BTreeMap::new();
    for (index, mesh) in b.meshes.iter().enumerate() {
        let material = b.materials[mesh.material_index].name.clone();
        let entry = per
            .entry(material.clone())
            .or_insert((0, 0, [f32::MAX; 3], [f32::MIN; 3]));
        let mut faces = String::new();
        for tri in mesh.indices.chunks_exact(3) {
            entry.1 += 1;
            if tri
                .iter()
                .all(|&i| have.contains(&key(&mesh.positions[i as usize])))
            {
                continue;
            }
            entry.0 += 1;
            for &i in tri {
                let p = mesh.positions[i as usize];
                for k in 0..3 {
                    entry.2[k] = entry.2[k].min(p[k]);
                    entry.3[k] = entry.3[k].max(p[k]);
                }
            }
            let _ = writeln!(
                faces,
                "f {} {} {}",
                base + tri[0] as usize,
                base + tri[1] as usize,
                base + tri[2] as usize
            );
        }
        if !faces.is_empty() {
            let _ = writeln!(obj, "o mesh{index}\nusemtl {material}");
            for p in &mesh.positions {
                let _ = writeln!(obj, "v {} {} {}", -p[0], p[1], p[2]);
            }
            obj.push_str(&faces);
            base += mesh.positions.len();
        }
    }
    // A material `<a>` does not have is a different look, not more of the same piece: the
    // scorched twins AEG004_423..429 add only overlay meshes on AEG004_423_ID* materials, which
    // read as "extra" by vertices alone (seen in game 2026-10-17 as scorch marks).
    let known: HashSet<&str> = a.materials.iter().map(|m| m.name.as_str()).collect();
    for (material, (extra, total, lo, hi)) in &per {
        if *extra > 0 {
            let kind = if known.contains(material.as_str()) {
                "same-material"
            } else {
                "NEW-MATERIAL"
            };
            println!(
                "{material}\t{kind}\textra {extra}/{total}\tx {:.2}..{:.2}\ty {:.2}..{:.2}\tz {:.2}..{:.2}",
                lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]
            );
        }
    }
    if let Some(out) = args.get(2) {
        fs::write(out, obj).expect(out);
    }
}
