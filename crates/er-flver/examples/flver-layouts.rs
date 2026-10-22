//! Print each mesh's vertex buffers of a decompressed `.flver`: stride, members (semantic,
//! index, format code, offset) and the first vertex's raw bytes, which is what an encoder
//! writing new vertices into that layout has to reproduce.
//!
//! `cargo run -p er-flver --example flver-layouts -- <file.flver>`

use std::env;
use std::fs;

fn main() {
    let path = env::args()
        .nth(1)
        .expect("usage: flver-layouts <file.flver>");
    let bytes = fs::read(&path).unwrap_or_else(|e| panic!("{path}: {e}"));
    let raw = er_flver::parse_raw(&bytes).unwrap_or_else(|e| panic!("{path}: {e:?}"));
    for (index, mesh) in raw.meshes.iter().enumerate() {
        let material = &raw.materials[mesh.material_index];
        println!(
            "mesh {index} material {} tris {}",
            material.name,
            mesh.indices.len() / 3
        );
        for &b in &mesh.buffer_indices {
            let buffer = &raw.buffers[b];
            println!(
                "  buffer {b} slot {} stride {} verts {}",
                buffer.input_slot, buffer.array_stride, buffer.vertex_count
            );
            for m in &buffer.members {
                println!(
                    "    {:?}[{}] format 0x{:02x} offset {} unk0 {}",
                    m.semantic, m.semantic_index, m.format_code, m.struct_offset, m.unk0
                );
            }
            let first: Vec<String> = buffer.data[..buffer.array_stride as usize]
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            println!("    first vertex {}", first.join(" "));
        }
    }
}
