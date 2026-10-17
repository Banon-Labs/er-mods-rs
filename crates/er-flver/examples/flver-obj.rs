//! Write a decompressed `.flver` as a Wavefront OBJ: positions, normals, UVs, one `usemtl`
//! group per mesh named after its material. FLVER is left-handed (+Z forward), OBJ and Blender
//! are right-handed, so X is negated. Triangle order is kept: the mirror alone already turns
//! From's clockwise front faces into OBJ's counter-clockwise ones (measured on AEG004_506: with
//! the order also reversed, all 6082 triangles faced against the FLVER's own normals).
//!
//! `cargo run -p er-flver --example flver-obj -- <in.flver> <out.obj>`

use std::env;
use std::fmt::Write as _;
use std::fs;

fn main() {
    let args: Vec<String> = env::args().collect();
    let [_, input, output] = args.as_slice() else {
        panic!("usage: flver-obj <in.flver> <out.obj>");
    };
    let bytes = fs::read(input).unwrap_or_else(|e| panic!("{input}: {e}"));
    let model = er_flver::parse(&bytes).unwrap_or_else(|e| panic!("{input}: {e:?}"));
    let mut obj = String::new();
    let mut base = 1usize;
    for (index, mesh) in model.meshes.iter().enumerate() {
        if mesh.indices.is_empty() {
            continue;
        }
        let material = &model.materials[mesh.material_index].name;
        let _ = writeln!(obj, "o mesh{index}\nusemtl {material}");
        for p in &mesh.positions {
            let _ = writeln!(obj, "v {} {} {}", -p[0], p[1], p[2]);
        }
        for n in &mesh.normals {
            let _ = writeln!(obj, "vn {} {} {}", -n[0], n[1], n[2]);
        }
        let has_uv = mesh.uvs.len() == mesh.positions.len();
        if has_uv {
            for t in &mesh.uvs {
                let _ = writeln!(obj, "vt {} {}", t[0], 1.0 - t[1]);
            }
        }
        let has_normal = mesh.normals.len() == mesh.positions.len();
        for tri in mesh.indices.chunks_exact(3) {
            let corner = |i: u32| {
                let v = base + i as usize;
                match (has_uv, has_normal) {
                    (true, true) => format!("{v}/{v}/{v}"),
                    (true, false) => format!("{v}/{v}"),
                    (false, true) => format!("{v}//{v}"),
                    (false, false) => format!("{v}"),
                }
            };
            let _ = writeln!(
                obj,
                "f {} {} {}",
                corner(tri[0]),
                corner(tri[1]),
                corner(tri[2])
            );
        }
        base += mesh.positions.len();
    }
    fs::write(output, obj).unwrap_or_else(|e| panic!("{output}: {e}"));
}
