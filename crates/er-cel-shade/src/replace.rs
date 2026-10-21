//! Which game passes have a cel port, how a game shader is recognised, and how a pipeline state
//! stream is walked and written. Nothing here touches D3D12, so all of it is tested on the host.

/// The stage a port's pass belongs to: the cel knobs and editor layers of a stage are what decide
/// whether its ports are redirected.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Stage {
    /// The deferred light buffer, after the game's lighting and ambient occlusion.
    Lighting = 0,
    /// The base colour the light buffer is multiplied by.
    Albedo = 1,
    /// The finished picture, around the tone curve.
    Tonemap = 2,
}

impl Stage {
    pub const ALL: [Self; 3] = [Self::Lighting, Self::Albedo, Self::Tonemap];

    /// This stage's bit in a set of stages.
    #[must_use]
    pub const fn bit(self) -> u32 {
        1 << self as u32
    }

    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Lighting => "lighting",
            Self::Albedo => "albedo",
            Self::Tonemap => "tonemap",
        }
    }
}

/// One cel port, numbered by its place in [`REPLACEMENTS`]. The number is also the top byte of
/// the `ER_VARIANT_ID` the port is compiled with (`scripts/build-cel-shaders.py`), which the probe
/// header carries back, and the [`crate::table::Entry::port`] of its redirect.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum PortKind {
    Ao = 0,
    AoSsr = 1,
    CmbAlbedo = 2,
    Tonemap = 3,
    TonemapLut = 4,
}

impl PortKind {
    pub const COUNT: usize = 5;
    pub const ALL: [Self; Self::COUNT] = [
        Self::Ao,
        Self::AoSsr,
        Self::CmbAlbedo,
        Self::Tonemap,
        Self::TonemapLut,
    ];

    /// The kind numbered `value`, as a table entry's `port` byte stores it.
    #[must_use]
    pub const fn from_u8(value: u8) -> Option<Self> {
        match value {
            0 => Some(Self::Ao),
            1 => Some(Self::AoSsr),
            2 => Some(Self::CmbAlbedo),
            3 => Some(Self::Tonemap),
            4 => Some(Self::TonemapLut),
            _ => None,
        }
    }

    #[must_use]
    pub const fn stage(self) -> Stage {
        match self {
            Self::Ao | Self::AoSsr => Stage::Lighting,
            Self::CmbAlbedo => Stage::Albedo,
            Self::Tonemap | Self::TonemapLut => Stage::Tonemap,
        }
    }

    /// This kind's entry in [`REPLACEMENTS`].
    #[must_use]
    pub fn replacement(self) -> &'static Replacement {
        &REPLACEMENTS[self as usize]
    }

    /// The kind's bit in a set of kinds: the unfaithful ports, the kinds a fidelity test
    /// redirects, the kinds a command list bound.
    #[must_use]
    pub const fn bit(self) -> u8 {
        1 << self as u8
    }

    /// Every kind's [`PortKind::bit`].
    pub const ALL_BITS: u8 = (1 << Self::COUNT) - 1;

    /// The kind's name where a person writes one: `unfaithful_ports` in the settings file, and
    /// `--kinds` in `scripts/check-shader-editor-probe.py` and `scripts/er-shader-ab-proof.py`,
    /// whose selftests read these names.
    #[must_use]
    pub const fn key(self) -> &'static str {
        match self {
            Self::Ao => "ao",
            Self::AoSsr => "ao_ssr",
            Self::CmbAlbedo => "cmb_albedo",
            Self::Tonemap => "tonemap",
            Self::TonemapLut => "tonemap_lut",
        }
    }

    /// The kind whose [`PortKind::key`] is `key`.
    #[must_use]
    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|kind| kind.key() == key)
    }

    /// The kinds a set of [`PortKind::bit`]s names, in order.
    pub fn in_set(bits: u8) -> impl Iterator<Item = Self> {
        Self::ALL
            .into_iter()
            .filter(move |kind| bits & kind.bit() != 0)
    }
}

const _: () = assert!(PortKind::COUNT < u8::BITS as usize);

/// One game pass with a cel port.
pub struct Replacement {
    /// The original's debug name, for logs.
    pub name: &'static str,
    /// Length of the original DXIL container, checked before hashing so the other few thousand
    /// shaders the game creates cost one comparison each.
    pub original_len: usize,
    /// FNV-1 64 of the original container. It is vkd3d-proton's shader hash too, which is how
    /// these values were measured: a dump named `f6f6f959abff547f.dxil` hashed to that value.
    pub original_fnv1: u64,
    /// Equal to this entry's index; `scripts/build-cel-shaders.py` numbers the blobs the same way.
    pub kind: PortKind,
    /// Equal to `kind.stage()`, written out so the table reads as the design does.
    pub stage: Stage,
    /// The signed cel port, compiled by `scripts/build-cel-shaders.py`. The game's pipeline is
    /// never given it: it is the pixel shader of a separate pipeline, the cel variant, which the
    /// command-list hooks bind in the game pipeline's place.
    pub dxil: &'static [u8],
}

/// The passes with a cel port.
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
        kind: PortKind::Ao,
        stage: Stage::Lighting,
        dxil: include_bytes!("../shaders/compiled/light_deferred_ao.dxil"),
    },
    Replacement {
        name: "GXLightDeferredAOWithSsr",
        original_len: 9684,
        original_fnv1: 0x2f89_244e_8be2_18a4,
        kind: PortKind::AoSsr,
        stage: Stage::Lighting,
        dxil: include_bytes!("../shaders/compiled/light_deferred_ao_ssr.dxil"),
    },
    Replacement {
        name: "GXLightCmbAlbedo",
        original_len: 5860,
        original_fnv1: 0x1c86_07a4_4475_d4f0,
        kind: PortKind::CmbAlbedo,
        stage: Stage::Albedo,
        dxil: include_bytes!("../shaders/compiled/light_cmb_albedo.dxil"),
    },
    Replacement {
        name: "ToneMap_TabledToneMapPS",
        original_len: 9591,
        original_fnv1: 0xaab9_d49c_8bb5_3daf,
        kind: PortKind::Tonemap,
        stage: Stage::Tonemap,
        dxil: include_bytes!("../shaders/compiled/tonemap.dxil"),
    },
    Replacement {
        name: "ToneMap_TabledToneMapLUTPS",
        original_len: 10219,
        original_fnv1: 0x2fb4_c417_54c0_7c18,
        kind: PortKind::TonemapLut,
        stage: Stage::Tonemap,
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

/// The bit of the vertex stage in `cmdlist::STAGE_ACTIVE`, beside the [`Stage::bit`]s: set while
/// a vertex layer draws (`docs/plans/er-shader-editor-vertex.md`, section 7.5). Vertex is not a
/// [`Stage`], because a stage there is a set of ports and vertex layers run in no port.
pub const VERTEX_STAGE_BIT: u32 = 1 << 3;

const _: () = assert!(VERTEX_STAGE_BIT > Stage::Tonemap.bit());

/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE`.
pub const SUBOBJECT_ROOT_SIGNATURE: u32 = 0;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_VS`.
pub const SUBOBJECT_VS: u32 = 1;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS`.
pub const SUBOBJECT_PS: u32 = 2;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DS`, `HS` and `GS`.
pub const SUBOBJECT_DS: u32 = 3;
pub const SUBOBJECT_HS: u32 = 4;
pub const SUBOBJECT_GS: u32 = 5;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_STREAM_OUTPUT`.
pub const SUBOBJECT_STREAM_OUTPUT: u32 = 7;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_INPUT_LAYOUT`.
pub const SUBOBJECT_INPUT_LAYOUT: u32 = 12;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_CACHED_PSO`.
pub const SUBOBJECT_CACHED_PSO: u32 = 19;
/// `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_VIEW_INSTANCING`.
pub const SUBOBJECT_VIEW_INSTANCING: u32 = 22;

/// Subobject types that carry shader bytecode, in the order a root signature is looked for:
/// `VS`, `PS`, `DS`, `HS`, `GS`, `CS`, `AS`, `MS`.
pub const SHADER_SUBOBJECTS: [u32; 8] = [1, 2, 3, 4, 5, 6, 24, 25];

/// Subobject types whose payload holds no pointer, so a bitwise copy of it is a complete copy.
/// Every other type the layout table knows is either one [`crate::record`] copies deeply or one it
/// refuses.
pub const POINTER_FREE_SUBOBJECTS: [u32; 15] =
    [8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20, 21, 26, 27, 28];

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

/// Append one subobject to a stream, laid out as [`subobjects`] reads it: the type, the payload at
/// `align`, and the whole padded to pointer alignment. `stream` must already end on that alignment,
/// which every earlier call leaves it on.
pub fn push_subobject(stream: &mut Vec<u8>, ty: u32, payload: &[u8], align: usize) {
    let pointer_align = core::mem::align_of::<usize>().max(align);
    let start = stream.len();
    stream.extend_from_slice(&ty.to_le_bytes());
    stream.resize(start + round_up(4, align), 0);
    stream.extend_from_slice(payload);
    stream.resize(
        start + round_up(round_up(4, align) + payload.len(), pointer_align),
        0,
    );
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

/// The size limit of a root signature, in `DWORD`s. How much of the rest the editor's parameters
/// take is `look_binding::ladder`'s choice.
pub const MAX_ROOT_SIGNATURE_DWORDS: u32 = 64;

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

/// The synthetic streams the walker and [`crate::record`] are tested on.
#[cfg(test)]
pub(crate) mod test_support {
    use super::Payload;
    use crate::record::{
        CachedPipelineState, InputLayoutDesc, ShaderBytecode, StreamOutputDesc, ViewInstancingDesc,
    };

    pub use super::push_subobject as push;

    fn payload_of<T>() -> Option<Payload> {
        Some(Payload {
            size: size_of::<T>(),
            align: align_of::<T>(),
        })
    }

    /// The payload layouts of the subobject types the tests build, as the walker sees them on a
    /// 64-bit target.
    pub fn test_layout(ty: u32) -> Option<Payload> {
        match ty {
            // root signature pointer
            0 => payload_of::<usize>(),
            // shader bytecode: pointer + length
            1..=6 | 24 | 25 => payload_of::<ShaderBytecode>(),
            7 => payload_of::<StreamOutputDesc>(),
            12 => payload_of::<InputLayoutDesc>(),
            // a 4-byte enum, like the primitive topology
            14 => payload_of::<u32>(),
            19 => payload_of::<CachedPipelineState>(),
            22 => payload_of::<ViewInstancingDesc>(),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::{push, test_layout};
    use super::*;

    #[test]
    fn each_replacement_is_numbered_by_its_place_and_staged_by_its_kind() {
        assert_eq!(REPLACEMENTS.len(), PortKind::COUNT);
        for (index, replacement) in REPLACEMENTS.iter().enumerate() {
            assert_eq!(replacement.kind as usize, index, "{}", replacement.name);
            assert_eq!(
                replacement.kind,
                PortKind::ALL[index],
                "{}",
                replacement.name
            );
            assert_eq!(
                replacement.stage,
                replacement.kind.stage(),
                "{}",
                replacement.name
            );
            assert!(std::ptr::eq(replacement.kind.replacement(), replacement));
        }
    }

    #[test]
    fn port_kinds_round_trip_through_their_byte_and_stay_below_no_port() {
        for kind in PortKind::ALL {
            assert_eq!(PortKind::from_u8(kind as u8), Some(kind));
            assert!((kind as u8) < crate::table::NO_PORT, "{kind:?}");
        }
        assert_eq!(PortKind::from_u8(PortKind::COUNT as u8), None);
        assert_eq!(PortKind::from_u8(crate::table::NO_PORT), None);
    }

    #[test]
    fn port_kinds_have_a_bit_and_a_name_each() {
        // Written out: the settings file and both proof scripts spell kinds this way.
        let named: Vec<(&str, u8)> = PortKind::ALL
            .iter()
            .map(|kind| (kind.key(), kind.bit()))
            .collect();
        assert_eq!(
            named,
            vec![
                ("ao", 1),
                ("ao_ssr", 2),
                ("cmb_albedo", 4),
                ("tonemap", 8),
                ("tonemap_lut", 16)
            ]
        );
        assert_eq!(PortKind::ALL_BITS, 0b1_1111);
        for kind in PortKind::ALL {
            assert_eq!(PortKind::from_key(kind.key()), Some(kind));
        }
        assert_eq!(PortKind::from_key("AO"), None);
        assert_eq!(PortKind::from_key(""), None);
        let kinds: Vec<PortKind> = PortKind::in_set(0b1_0010 | 0b1110_0000).collect();
        assert_eq!(kinds, vec![PortKind::AoSsr, PortKind::TonemapLut]);
    }

    #[test]
    fn stages_have_distinct_bits() {
        let bits: Vec<u32> = Stage::ALL.iter().map(|stage| stage.bit()).collect();
        assert_eq!(bits, vec![1, 2, 4]);
    }

    #[test]
    fn pointer_free_and_pointer_carrying_types_do_not_overlap() {
        for ty in POINTER_FREE_SUBOBJECTS {
            assert!(!SHADER_SUBOBJECTS.contains(&ty), "{ty}");
            assert!(
                ![
                    SUBOBJECT_ROOT_SIGNATURE,
                    SUBOBJECT_STREAM_OUTPUT,
                    SUBOBJECT_INPUT_LAYOUT,
                    SUBOBJECT_CACHED_PSO,
                    SUBOBJECT_VIEW_INSTANCING,
                ]
                .contains(&ty),
                "{ty}"
            );
        }
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

    #[test]
    fn each_parameter_kind_costs_its_dwords() {
        let tables = std::iter::repeat_n(RootParameterCost::Table, 10);
        let used = root_signature_dwords(tables.chain([
            RootParameterCost::Constants(4),
            RootParameterCost::Descriptor,
        ]));
        assert_eq!(used, 16);
        // The game's raster root signature: 36 descriptor tables.
        assert_eq!(
            root_signature_dwords(std::iter::repeat_n(RootParameterCost::Table, 36)),
            36
        );
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
