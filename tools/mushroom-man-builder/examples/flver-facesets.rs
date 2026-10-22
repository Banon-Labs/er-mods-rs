//! Print each mesh of a decompressed `.flver`: its raw table entry's first word (the dynamic
//! byte and padding), material, and every face set's flags and index count. Asset FLVERs carry
//! a second run of meshes that look empty in a vertex listing; this shows what their face sets
//! hold and are flagged as.
//!
//! `cargo run -p mushroom-man-builder --example flver-facesets -- <file.flver>`

use std::{env, fs};

use mushroom_man_builder::flver::{Flver, parse_materials, parse_u32_list};

fn main() {
    let path = env::args()
        .nth(1)
        .expect("usage: flver-facesets <file.flver>");
    let bytes = fs::read(&path).unwrap_or_else(|e| panic!("{path}: {e}"));
    let flver = Flver::parse(&bytes).unwrap_or_else(|e| panic!("{path}: {e:?}"));
    let materials = parse_materials(&bytes, &flver).unwrap();
    for (index, mesh) in flver.meshes.iter().enumerate() {
        let at = flver.mesh_table_offset(index);
        let head = &bytes[at..at + 4];
        let material = &materials[mesh.material_index as usize];
        print!(
            "mesh {index:2} head {head:02x?} {:20} mtd {}",
            material.name, material.mtd
        );
        let sets = parse_u32_list(&bytes, mesh.face_set_offset, mesh.face_set_count).unwrap();
        for set in sets {
            let f = &flver.face_sets[set as usize];
            print!(
                " | {:08x} n{} cull{}",
                f.flags, f.index_count, f.cull_backfaces as u8
            );
        }
        println!();
    }
}
