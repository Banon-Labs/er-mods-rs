//! Which shaders are swapped, how a game shader is recognised, and how a pipeline state stream is
//! walked to its pixel shader. Nothing here touches D3D12, so all of it is tested on the host.

/// One pass this crate replaces.
pub struct Replacement {
    /// The original's debug name, for logs.
    pub name: &'static str,
    /// Length of the original DXIL container, checked before hashing so the other few thousand
    /// shaders the game creates cost one comparison each.
    pub original_len: usize,
    /// FNV-1 64 of the original container. It is vkd3d-proton's shader hash too, which is how
    /// these values were measured: a dump named `f6f6f959abff547f.dxil` hashed to that value.
    pub original_fnv1: u64,
    /// The signed replacement, compiled by `scripts/build-cel-shaders.py`.
    pub dxil: &'static [u8],
}

/// The passes swapped today.
///
/// The two AO passes multiply ambient occlusion into the finished diffuse light buffer, which by
/// then holds the sun, the tiled point and spot lights and the ambient term, so posterizing and
/// inking there reaches every light at once. Which of the two runs depends on the game's
/// screen-space reflection setting, so both are replaced.
///
/// The albedo pass supplies the base colour the lit buffer is multiplied by; flattening it is what
/// reaches surfaces whose look is texture detail under flat light, which banding cannot.
///
/// The two tonemap passes produce the final picture. Forward-lit materials and emissive light
/// sources bypass every deferred pass above, so the final posterize is what reaches them.
pub static REPLACEMENTS: &[Replacement] = &[
    Replacement {
        name: "GXLightDeferredAO",
        original_len: 9264,
        original_fnv1: 0xf6f6_f959_abff_547f,
        dxil: include_bytes!("../shaders/compiled/light_deferred_ao.dxil"),
    },
    Replacement {
        name: "GXLightDeferredAOWithSsr",
        original_len: 9684,
        original_fnv1: 0x2f89_244e_8be2_18a4,
        dxil: include_bytes!("../shaders/compiled/light_deferred_ao_ssr.dxil"),
    },
    Replacement {
        name: "GXLightCmbAlbedo",
        original_len: 5860,
        original_fnv1: 0x1c86_07a4_4475_d4f0,
        dxil: include_bytes!("../shaders/compiled/light_cmb_albedo.dxil"),
    },
    Replacement {
        name: "ToneMap_TabledToneMapPS",
        original_len: 9591,
        original_fnv1: 0xaab9_d49c_8bb5_3daf,
        dxil: include_bytes!("../shaders/compiled/tonemap.dxil"),
    },
    Replacement {
        name: "ToneMap_TabledToneMapLUTPS",
        original_len: 10219,
        original_fnv1: 0x2fb4_c417_54c0_7c18,
        dxil: include_bytes!("../shaders/compiled/tonemap_lut.dxil"),
    },
];

pub use er_game_base::fnv1a::fnv1_64;

/// The replacement for this shader, if it is one of ours.
pub fn find(bytecode: &[u8]) -> Option<&'static Replacement> {
    REPLACEMENTS
        .iter()
        .find(|r| r.original_len == bytecode.len() && fnv1_64(bytecode) == r.original_fnv1)
}

/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE`.
pub const SUBOBJECT_ROOT_SIGNATURE: u32 = 0;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS`.
pub const SUBOBJECT_PS: u32 = 2;

/// Size and alignment of one subobject's payload.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Payload {
    pub size: usize,
    pub align: usize,
}

/// What a walk of a pipeline state stream found.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Walk {
    /// Byte offset of the wanted subobject's payload from the start of the stream.
    Found(usize),
    Absent,
    /// A subobject type the layout table does not know. The walk stops rather than guess its
    /// size, because a wrong size would misread every subobject after it.
    UnknownType(u32),
    /// The stream ended inside a subobject.
    Truncated,
}

fn round_up(value: usize, align: usize) -> usize {
    value.div_ceil(align) * align
}

/// One subobject of a pipeline state stream: its type and the byte offset of its payload.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Subobject {
    pub ty: u32,
    pub offset: usize,
}

/// Walk a whole `D3D12_PIPELINE_STATE_STREAM_DESC` stream.
///
/// Each subobject is the d3dx12 `alignas(void*) struct { TYPE type; T payload; }`: a 4-byte type,
/// the payload at its own alignment, and the whole padded to pointer alignment.
///
/// # Errors
///
/// [`Walk::UnknownType`] or [`Walk::Truncated`]: the stream cannot be read past that point, so
/// nothing about it is reported rather than half of it.
pub fn subobjects(
    stream: &[u8],
    layout: &dyn Fn(u32) -> Option<Payload>,
) -> Result<Vec<Subobject>, Walk> {
    let pointer_align = core::mem::align_of::<usize>();
    let mut found = Vec::new();
    let mut start = 0;
    while start + 4 <= stream.len() {
        let mut word = [0_u8; 4];
        word.copy_from_slice(&stream[start..start + 4]);
        let ty = u32::from_le_bytes(word);
        let Some(payload) = layout(ty) else {
            return Err(Walk::UnknownType(ty));
        };
        let offset = start + round_up(4, payload.align);
        let end = start
            + round_up(
                round_up(4, payload.align) + payload.size,
                pointer_align.max(payload.align),
            );
        if offset + payload.size > stream.len() {
            return Err(Walk::Truncated);
        }
        found.push(Subobject { ty, offset });
        start = end;
    }
    Ok(found)
}

/// Walk a `D3D12_PIPELINE_STATE_STREAM_DESC` stream to the payload of subobject `wanted`.
pub fn find_subobject(stream: &[u8], wanted: u32, layout: &dyn Fn(u32) -> Option<Payload>) -> Walk {
    match subobjects(stream, layout) {
        Ok(all) => all
            .iter()
            .find(|s| s.ty == wanted)
            .map_or(Walk::Absent, |s| Walk::Found(s.offset)),
        Err(walk) => walk,
    }
}

/// The bytes of one part of a DXBC container, by four-character code.
///
/// A container is `DXBC`, a 16-byte digest, a version, the total size, a part count at offset 28
/// and that many part offsets after it; each part is its code, its size, then its bytes. Shader
/// bytecode and serialized root signatures are both containers, which is how an `RTS0` part can be
/// found in either.
#[must_use]
pub fn container_part<'a>(container: &'a [u8], code: &[u8; 4]) -> Option<&'a [u8]> {
    let read = |at: usize| -> Option<usize> {
        let bytes = container.get(at..at + 4)?;
        Some(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) as usize)
    };
    if container.get(..4)? != b"DXBC" {
        return None;
    }
    let count = read(28)?;
    for index in 0..count {
        let part = read(32 + index * 4)?;
        if container.get(part..part + 4)? == code {
            let size = read(part + 4)?;
            return container.get(part + 8..part + 8 + size);
        }
    }
    None
}

/// The root signature part's code.
pub const ROOT_SIGNATURE_PART: &[u8; 4] = b"RTS0";

/// How much of the 64-`DWORD` root signature budget a parameter uses.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RootParameterCost {
    /// A descriptor table: one `DWORD`.
    Table,
    /// Root constants: one `DWORD` each.
    Constants(u32),
    /// A root CBV, SRV or UAV: a 64-bit address, two `DWORD`s.
    Descriptor,
}

/// The size limit of a root signature, in `DWORD`s.
pub const MAX_ROOT_SIGNATURE_DWORDS: u32 = 64;

/// What appending the look's root CBV costs.
pub const LOOK_PARAMETER_DWORDS: u32 = 2;

/// Total `DWORD`s a root signature's parameters use.
pub fn root_signature_dwords(params: impl IntoIterator<Item = RootParameterCost>) -> u32 {
    params
        .into_iter()
        .map(|p| match p {
            RootParameterCost::Table => 1,
            RootParameterCost::Constants(count) => count,
            RootParameterCost::Descriptor => 2,
        })
        .sum()
}

/// Does a root signature of this size still have room for the look's parameter?
#[must_use]
pub fn has_room_for_look(dwords: u32) -> bool {
    dwords + LOOK_PARAMETER_DWORDS <= MAX_ROOT_SIGNATURE_DWORDS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_layout(ty: u32) -> Option<Payload> {
        match ty {
            // root signature pointer
            0 => Some(Payload { size: 8, align: 8 }),
            // shader bytecode: pointer + length
            1 | 2 => Some(Payload { size: 16, align: 8 }),
            // a 4-byte enum, like the primitive topology
            14 => Some(Payload { size: 4, align: 4 }),
            _ => None,
        }
    }

    fn push(stream: &mut Vec<u8>, ty: u32, payload: &[u8], align: usize) {
        let start = stream.len();
        stream.extend_from_slice(&ty.to_le_bytes());
        stream.resize(start + round_up(4, align), 0);
        stream.extend_from_slice(payload);
        stream.resize(start + round_up(round_up(4, align) + payload.len(), 8), 0);
    }

    #[test]
    fn find_rejects_a_shader_of_the_right_length_but_other_bytes() {
        let decoy = vec![0_u8; REPLACEMENTS[0].original_len];
        assert!(find(&decoy).is_none());
    }

    #[test]
    fn replacements_are_signed_dxil_containers() {
        for replacement in REPLACEMENTS {
            assert_eq!(&replacement.dxil[..4], b"DXBC", "{}", replacement.name);
            assert_ne!(
                &replacement.dxil[4..20],
                &[0_u8; 16],
                "{} is unsigned",
                replacement.name
            );
        }
    }

    #[test]
    fn walk_finds_the_pixel_shader_after_mixed_alignments() {
        let mut stream = Vec::new();
        push(&mut stream, 14, &3_u32.to_le_bytes(), 4);
        // The 4-byte topology took 8 bytes, so the root signature starts at 8.
        let root_start = stream.len();
        push(&mut stream, 0, &[0xaa; 8], 8);
        push(&mut stream, 1, &[0xbb; 16], 8);
        let ps_start = stream.len();
        push(&mut stream, 2, &[0xcc; 16], 8);
        assert_eq!(root_start, 8);
        assert_eq!(
            find_subobject(&stream, SUBOBJECT_PS, &test_layout),
            Walk::Found(ps_start + 8)
        );
        assert_eq!(
            find_subobject(&stream, SUBOBJECT_ROOT_SIGNATURE, &test_layout),
            Walk::Found(root_start + 8)
        );
    }

    #[test]
    fn walk_stops_at_an_unknown_type_instead_of_guessing() {
        let mut stream = Vec::new();
        push(&mut stream, 99, &[0; 4], 4);
        push(&mut stream, 2, &[0xcc; 16], 8);
        assert_eq!(
            find_subobject(&stream, SUBOBJECT_PS, &test_layout),
            Walk::UnknownType(99)
        );
    }

    #[test]
    fn walk_reports_a_truncated_stream() {
        let mut stream = Vec::new();
        push(&mut stream, 2, &[0xcc; 16], 8);
        stream.truncate(12);
        assert_eq!(
            find_subobject(&stream, SUBOBJECT_PS, &test_layout),
            Walk::Truncated
        );
    }

    /// A container with the given parts, in the layout `container_part` reads.
    fn container(parts: &[(&[u8; 4], &[u8])]) -> Vec<u8> {
        let header = 32 + 4 * parts.len();
        let mut out = b"DXBC".to_vec();
        out.extend_from_slice(&[0; 16]);
        out.extend_from_slice(&1_u32.to_le_bytes());
        out.extend_from_slice(&0_u32.to_le_bytes());
        out.extend_from_slice(&(parts.len() as u32).to_le_bytes());
        let mut at = header;
        for (_, data) in parts {
            out.extend_from_slice(&(at as u32).to_le_bytes());
            at += 8 + data.len();
        }
        for (code, data) in parts {
            out.extend_from_slice(*code);
            out.extend_from_slice(&(data.len() as u32).to_le_bytes());
            out.extend_from_slice(data);
        }
        out
    }

    #[test]
    fn container_part_finds_the_root_signature_among_others() {
        let blob = container(&[(b"DXIL", &[1, 2, 3]), (b"RTS0", &[9, 8, 7, 6])]);
        assert_eq!(
            container_part(&blob, ROOT_SIGNATURE_PART),
            Some(&[9_u8, 8, 7, 6][..])
        );
        assert_eq!(container_part(&blob, b"PSV0"), None);
    }

    #[test]
    fn container_part_refuses_what_is_not_a_container() {
        assert_eq!(container_part(b"nope", ROOT_SIGNATURE_PART), None);
        let mut blob = container(&[(b"RTS0", &[1, 2, 3, 4])]);
        blob.truncate(blob.len() - 2);
        assert_eq!(container_part(&blob, ROOT_SIGNATURE_PART), None);
    }

    #[test]
    fn the_look_fits_until_the_budget_is_spent() {
        let tables = std::iter::repeat_n(RootParameterCost::Table, 10);
        let used = root_signature_dwords(tables.chain([
            RootParameterCost::Constants(4),
            RootParameterCost::Descriptor,
        ]));
        assert_eq!(used, 16);
        assert!(has_room_for_look(used));
        assert!(has_room_for_look(62));
        assert!(!has_room_for_look(63));
    }

    #[test]
    fn subobjects_lists_every_payload_in_order() {
        let mut stream = Vec::new();
        push(&mut stream, 14, &3_u32.to_le_bytes(), 4);
        push(&mut stream, 2, &[0xcc; 16], 8);
        assert_eq!(
            subobjects(&stream, &test_layout),
            Ok(vec![
                Subobject { ty: 14, offset: 4 },
                Subobject { ty: 2, offset: 16 }
            ])
        );
    }

    #[test]
    fn walk_reports_absence() {
        let mut stream = Vec::new();
        push(&mut stream, 14, &3_u32.to_le_bytes(), 4);
        assert_eq!(
            find_subobject(&stream, SUBOBJECT_PS, &test_layout),
            Walk::Absent
        );
    }
}
