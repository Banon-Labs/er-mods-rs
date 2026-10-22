//! Print one line per mesh of a decompressed `.flver`: material, vertex and
//! triangle counts, position bounds and UV bounds.
//!
//! `cargo run -p er-flver --example flver-meshes -- <file.flver>...`

use std::env;
use std::fs;

fn bounds<const N: usize>(values: &[[f32; N]]) -> ([f32; N], [f32; N]) {
    let mut lo = [f32::INFINITY; N];
    let mut hi = [f32::NEG_INFINITY; N];
    for value in values {
        for axis in 0..N {
            lo[axis] = lo[axis].min(value[axis]);
            hi[axis] = hi[axis].max(value[axis]);
        }
    }
    (lo, hi)
}

fn main() {
    for path in env::args().skip(1) {
        let bytes = fs::read(&path).unwrap_or_else(|e| panic!("{path}: {e}"));
        let model = er_flver::parse(&bytes).unwrap_or_else(|e| panic!("{path}: {e:?}"));
        let (lo, hi) = model.bounding_box;
        println!(
            "{path} size {:.2} x {:.2} x {:.2} (y {:.2}..{:.2})",
            hi[0] - lo[0],
            hi[1] - lo[1],
            hi[2] - lo[2],
            lo[1],
            hi[1]
        );
        for (index, mesh) in model.meshes.iter().enumerate() {
            let material = &model.materials[mesh.material_index];
            let (plo, phi) = bounds(&mesh.positions);
            let (ulo, uhi) = bounds(&mesh.uvs);
            println!(
                "  mesh {index:2} mat {:2} {:<34} verts {:6} tris {:6} y {:6.2}..{:6.2} xz {:5.2}x{:5.2} uv {:.3},{:.3}..{:.3},{:.3}",
                mesh.material_index,
                material.name,
                mesh.positions.len(),
                mesh.indices.len() / 3,
                plo[1],
                phi[1],
                phi[0] - plo[0],
                phi[2] - plo[2],
                ulo[0],
                ulo[1],
                uhi[0],
                uhi[1],
            );
        }
    }
}
