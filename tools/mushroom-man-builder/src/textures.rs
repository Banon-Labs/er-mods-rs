//! The mushroom's textures, moved into the TPF the Elden Ring face/body binder carries.
//!
//! c2280's TPF holds `c2280` (albedo), `c2280_s` (specular) and `c2280_n` (normal). They are
//! renamed to the donor's slots -- `<PREFIX>_a`, `<PREFIX>_3m`, `<PREFIX>_n`, with `_l` on the
//! low-detail binder -- and their DDS payloads are copied byte for byte. The texture list, order,
//! format bytes and header flags are the ones `scripts/route_a_mushroom_stage_textures.rs` gave
//! WitchyBND in its `fc` manifest; the mip count is read from each DDS header, as WitchyBND did.

use er_tpf::{TEX_TYPE_TEXTURE, TPF_ENCODING_UTF16, TPF_PLATFORM_PC, Tpf, TpfTexture};

use crate::error::{Error, Result};

const DSR_TEXTURE_PREFIX: &str = "c2280";
const FLAG2: u8 = 0x03;
const FORMAT_DEFAULT: u8 = 0;
const FORMAT_NORMAL: u8 = 107;
const DDS_MAGIC: &[u8; 4] = b"DDS ";
const DDS_MIP_COUNT_OFFSET: usize = 0x1C;

/// `(source suffix, target slot, format byte)` in the order the donor manifest lists them.
const SLOTS: [(&str, &str, u8); 3] = [
    ("_s", "_3m", FORMAT_DEFAULT),
    ("", "_a", FORMAT_DEFAULT),
    ("_n", "_n", FORMAT_NORMAL),
];

/// A TPF for `target_prefix` (`FC_M_0000`) holding c2280's three textures; `lod_suffix` is
/// `""` for the high-detail binder and `"_l"` for the low one.
pub fn mushroom_tpf(dsr_tpf: &[u8], target_prefix: &str, lod_suffix: &str) -> Result<Vec<u8>> {
    let source = Tpf::parse(dsr_tpf).map_err(|error| Error::new(format!("c2280 TPF: {error}")))?;
    let mut textures = Vec::with_capacity(SLOTS.len());
    for (source_suffix, slot, format) in SLOTS {
        let source_name = format!("{DSR_TEXTURE_PREFIX}{source_suffix}");
        let dds = source
            .textures
            .iter()
            .find(|texture| texture.name.eq_ignore_ascii_case(&source_name))
            .ok_or_else(|| Error::new(format!("c2280's TPF has no texture {source_name}")))?
            .dds
            .clone();
        let dds = if slot == ALBEDO_SLOT {
            as_srgb_bc1(dds)
        } else {
            dds
        };
        textures.push(TpfTexture {
            name: format!("{target_prefix}{slot}{lod_suffix}"),
            format,
            tex_type: TEX_TYPE_TEXTURE,
            mip_count: dds_mip_count(&dds)?,
            flags1: 0,
            dds,
        });
    }
    write_tpf(&Tpf {
        platform: TPF_PLATFORM_PC,
        flag2: FLAG2,
        encoding: TPF_ENCODING_UTF16,
        textures,
    })
}

const TPF_HEADER_SIZE: usize = 0x10;
const TPF_ENTRY_SIZE: usize = 0x14;

/// A PC TPF laid out the way Elden Ring's own are: header, entries, every name, then every
/// texture's data back to back.
///
/// `er_tpf::Tpf::build` writes each name directly before its texture's data instead. The game
/// was not shown to reject that -- the boot crash first blamed on it came from the binder's
/// Kraken compression (see `oodle.rs`) -- but this layout is the one the game ships, and it is
/// held to reproducing a vanilla TPF byte for byte (`tpf-roundtrip`).
pub fn write_tpf(tpf: &Tpf) -> Result<Vec<u8>> {
    let utf16 = tpf.encoding == TPF_ENCODING_UTF16;
    let names: Vec<Vec<u8>> = tpf
        .textures
        .iter()
        .map(|texture| {
            if utf16 {
                texture
                    .name
                    .encode_utf16()
                    .chain([0])
                    .flat_map(u16::to_le_bytes)
                    .collect()
            } else {
                texture.name.bytes().chain([0]).collect()
            }
        })
        .collect();
    let mut name_offset = TPF_HEADER_SIZE + tpf.textures.len() * TPF_ENTRY_SIZE;
    let mut data_offset = name_offset + names.iter().map(Vec::len).sum::<usize>();
    let total: usize = tpf.textures.iter().map(|texture| texture.dds.len()).sum();
    let u32_of = |value: usize| u32::try_from(value).map_err(|_| Error::new("TPF exceeds 4 GiB"));

    let mut out = crate::bytes::Writer::default();
    out.bytes(b"TPF\0");
    out.u32_le(u32_of(total)?);
    out.u32_le(u32_of(tpf.textures.len())?);
    out.bytes(&[tpf.platform, tpf.flag2, tpf.encoding, 0]);
    for (texture, name) in tpf.textures.iter().zip(&names) {
        out.u32_le(u32_of(data_offset)?);
        out.u32_le(u32_of(texture.dds.len())?);
        out.bytes(&[
            texture.format,
            texture.tex_type,
            texture.mip_count,
            texture.flags1,
        ]);
        out.u32_le(u32_of(name_offset)?);
        out.u32_le(0);
        name_offset += name.len();
        data_offset += texture.dds.len();
    }
    for name in &names {
        out.bytes(name);
    }
    for texture in &tpf.textures {
        out.bytes(&texture.dds);
    }
    Ok(out.into_inner())
}

const ALBEDO_SLOT: &str = "_a";
const DDS_FOURCC_OFFSET: usize = 0x54;
const DDS_HEADER_END: usize = 0x80;
/// `DXGI_FORMAT_BC1_UNORM_SRGB`: how Elden Ring stores its own body albedo, `FC_M_0000_a`.
const DXGI_BC1_UNORM_SRGB: u32 = 72;
const DX10_TEXTURE_2D: u32 = 3;

/// c2280's albedo under the header Elden Ring's own body albedo has. c2280's is a legacy `DXT1`
/// header, which names no colour space; the game's is the same BC1 blocks under a DX10 header
/// that says sRGB, so colours are not read as linear and washed out. The pixels are unchanged.
fn as_srgb_bc1(dds: Vec<u8>) -> Vec<u8> {
    if dds.len() < DDS_HEADER_END
        || dds.get(DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4) != Some(b"DXT1".as_slice())
    {
        return dds;
    }
    let mut out = Vec::with_capacity(dds.len() + 20);
    out.extend_from_slice(&dds[..DDS_HEADER_END]);
    out[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4].copy_from_slice(b"DX10");
    for value in [DXGI_BC1_UNORM_SRGB, DX10_TEXTURE_2D, 0, 1, 0] {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out.extend_from_slice(&dds[DDS_HEADER_END..]);
    out
}

/// `dwMipMapCount` out of a DDS header, at least one.
fn dds_mip_count(dds: &[u8]) -> Result<u8> {
    if dds.get(..4) != Some(DDS_MAGIC.as_slice()) {
        return Err(Error::new("a c2280 texture is not a DDS file"));
    }
    let count = crate::bytes::u32_le(dds, DDS_MIP_COUNT_OFFSET)?;
    Ok(u8::try_from(count.max(1)).unwrap_or(u8::MAX))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_legacy_dxt1_albedo_gets_the_srgb_dx10_header_and_keeps_its_pixels() {
        let mut legacy = dds(3, 7);
        legacy[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4].copy_from_slice(b"DXT1");
        let srgb = as_srgb_bc1(legacy.clone());
        assert_eq!(&srgb[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4], b"DX10");
        assert_eq!(
            crate::bytes::u32_le(&srgb, DDS_HEADER_END).unwrap(),
            DXGI_BC1_UNORM_SRGB
        );
        assert_eq!(&srgb[DDS_HEADER_END + 20..], &legacy[DDS_HEADER_END..]);
        assert_eq!(dds_mip_count(&srgb).unwrap(), 3);
        // Anything that is not a legacy DXT1 passes through untouched.
        let other = dds(3, 9);
        assert_eq!(as_srgb_bc1(other.clone()), other);
    }

    fn dds(mips: u32, fill: u8) -> Vec<u8> {
        let mut bytes = vec![fill; 0x80 + 16];
        bytes[..4].copy_from_slice(DDS_MAGIC);
        bytes[DDS_MIP_COUNT_OFFSET..DDS_MIP_COUNT_OFFSET + 4].copy_from_slice(&mips.to_le_bytes());
        bytes
    }

    #[test]
    fn the_three_c2280_textures_land_in_the_donor_slots_in_manifest_order() {
        let source = Tpf {
            platform: TPF_PLATFORM_PC,
            flag2: FLAG2,
            encoding: TPF_ENCODING_UTF16,
            textures: [("c2280", 1_u8), ("c2280_s", 2), ("c2280_n", 3)]
                .iter()
                .map(|&(name, fill)| TpfTexture {
                    name: name.to_owned(),
                    format: 0,
                    tex_type: TEX_TYPE_TEXTURE,
                    mip_count: 1,
                    flags1: 0,
                    dds: dds(u32::from(fill) + 4, fill),
                })
                .collect(),
        }
        .build()
        .unwrap();
        let bytes = mushroom_tpf(&source, "FC_M_0000", "_l").unwrap();
        let built = Tpf::parse(&bytes).unwrap();
        let names: Vec<&str> = built.textures.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["FC_M_0000_3m_l", "FC_M_0000_a_l", "FC_M_0000_n_l"]);
        assert_eq!(
            built.textures[0].dds[0x80], 2,
            "specular comes from c2280_s"
        );
        assert_eq!(built.textures[1].mip_count, 5);
        assert_eq!(built.textures[2].format, FORMAT_NORMAL);
        assert_eq!(
            write_tpf(&built).unwrap(),
            bytes,
            "our own output round-trips"
        );
    }

    #[test]
    fn every_name_precedes_every_texture_payload() {
        let tpf = Tpf {
            platform: TPF_PLATFORM_PC,
            flag2: FLAG2,
            encoding: TPF_ENCODING_UTF16,
            textures: ["A_a", "A_n"]
                .iter()
                .map(|name| TpfTexture {
                    name: (*name).to_owned(),
                    format: 0,
                    tex_type: TEX_TYPE_TEXTURE,
                    mip_count: 1,
                    flags1: 0,
                    dds: dds(1, 7),
                })
                .collect(),
        };
        let bytes = write_tpf(&tpf).unwrap();
        let entry = |i: usize, field: usize| {
            u32::from_le_bytes(bytes[0x10 + i * 0x14 + field..][..4].try_into().unwrap()) as usize
        };
        let (first_data, last_name) = (entry(0, 0), entry(1, 0x0C));
        assert!(
            last_name < first_data,
            "names {last_name:#x} must sit before data {first_data:#x}"
        );
        assert_eq!(
            first_data,
            0x10 + 2 * 0x14 + 2 * 8,
            "two UTF-16 names of three chars"
        );
        assert_eq!(
            entry(1, 0),
            first_data + tpf.textures[0].dds.len(),
            "data is contiguous"
        );
    }
}
