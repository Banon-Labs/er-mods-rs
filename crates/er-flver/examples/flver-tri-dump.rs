//! Dump the main-faceset triangles of many FLVERs, in the model's own game frame (no axis flip),
//! as one small binary file each, for numpy-side geometry (`scripts/er-rock-eruption-scan.py`).
//!
//! Layout of `<out-dir>/<FILE STEM>.tri`, little-endian:
//! `u32 vertex_count, u32 triangle_count, u32 material_count`, then
//! `f32[vertex_count * 3]` positions (all meshes concatenated),
//! `u32[triangle_count * 3]` indices into that array,
//! `u16[triangle_count]` material index of each triangle,
//! then `material_count` lines of `name\tmtd\n` in UTF-8.
//!
//! A FLVER that fails to parse is reported on stderr and skipped, so one bad file does not stop
//! a batch.
//!
//! `cargo run -p er-flver --example flver-tri-dump -- <out-dir> <a.flver> [<b.flver> ...]`

use std::env;
use std::fs;
use std::path::Path;

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let (out_dir, inputs) = args
        .split_first()
        .expect("usage: flver-tri-dump <out-dir> <flver>...");
    fs::create_dir_all(out_dir).expect("create out dir");
    for input in inputs {
        let bytes = match fs::read(input) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("{input}: {e}");
                continue;
            }
        };
        let model = match er_flver::parse(&bytes) {
            Ok(m) => m,
            Err(e) => {
                eprintln!("{input}: {e:?}");
                continue;
            }
        };
        let mut positions: Vec<f32> = Vec::new();
        let mut indices: Vec<u32> = Vec::new();
        let mut tri_material: Vec<u16> = Vec::new();
        for mesh in &model.meshes {
            let base = (positions.len() / 3) as u32;
            for p in &mesh.positions {
                positions.extend_from_slice(p);
            }
            for tri in mesh.indices.as_chunks::<3>().0 {
                indices.extend(tri.iter().map(|i| base + i));
                tri_material.push(mesh.material_index as u16);
            }
        }
        let mut out = Vec::new();
        out.extend_from_slice(&((positions.len() / 3) as u32).to_le_bytes());
        out.extend_from_slice(&(tri_material.len() as u32).to_le_bytes());
        out.extend_from_slice(&(model.materials.len() as u32).to_le_bytes());
        for v in &positions {
            out.extend_from_slice(&v.to_le_bytes());
        }
        for i in &indices {
            out.extend_from_slice(&i.to_le_bytes());
        }
        for m in &tri_material {
            out.extend_from_slice(&m.to_le_bytes());
        }
        for m in &model.materials {
            out.extend_from_slice(format!("{}\t{}\n", m.name, m.mtd).as_bytes());
        }
        let stem = Path::new(input)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("model");
        let path = Path::new(out_dir).join(format!("{stem}.tri"));
        fs::write(&path, out).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    }
}
