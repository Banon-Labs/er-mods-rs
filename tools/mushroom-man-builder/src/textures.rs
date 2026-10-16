//! A DSR character's textures, moved into the TPF the Elden Ring face/body binder carries.
//!
//! The character's materials name an albedo (`g_Diffuse`), a specular (`g_Specular`) and a
//! normal map (`g_Bumpmap`): for c2280 `c2280`, `c2280_s` and `c2280_n`, all in its own TPF. They
//! are renamed to the donor's slots -- `<PREFIX>_a`, `<PREFIX>_3m`, `<PREFIX>_n`, with `_l` on the
//! low-detail binder -- and their DDS payloads are copied byte for byte, apart from the albedo's
//! colour-space header. A material that names no specular or normal map gets a generated flat one.
//! The texture list, order and header flags are the ones
//! `scripts/route_a_mushroom_stage_textures.rs` gave WitchyBND in its `fc` manifest; the mip count
//! is read from each DDS header, as WitchyBND did, and the format byte from the DDS format.
//!
//! A texture need not live in the character's own binder. The path a material names says whose
//! it is: c2900's body samples `N:\FRPG\data\Model\chr\c2909\tex\c2909.tga`, which is in
//! `chr/c2909.chrbnd.dcx`, and its own `c2900.tpf` holds only its weapons' textures. So each
//! texture of a set carries the character its path names ([`TextureRef::owner`]), and
//! [`character_tpf_from`] looks it up in that character's TPF.
//!
//! A character may draw with several sets ([`TextureSets`]): Smough's legs have their own. Only
//! set 0 can go in the donor's slots, since the Body material's matbin names `FC_M_0000_a` and
//! its neighbours itself. Every other set goes in under `FC_M_0000_<set>` ([`set_prefix`]), for a
//! retargeted `C[AMSN]` material that names its textures by path ([`body_tpf`]).

use er_tpf::{TEX_TYPE_TEXTURE, TPF_ENCODING_UTF16, TPF_PLATFORM_PC, Tpf, TpfTexture};

use crate::{
    error::{Error, Result},
    flver::source::SourceMaterial,
};

const FLAG2: u8 = 0x03;
const DDS_MAGIC: &[u8; 4] = b"DDS ";
const DDS_MIP_COUNT_OFFSET: usize = 0x1C;

/// One texture a material samples: its name in a TPF and the character whose binder holds it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TextureRef {
    /// The file stem of the material's path, `c2909_n`.
    pub name: String,
    /// The character folder the path names, `c2909` for `...\chr\c2909\tex\c2909_n.tga`; for a
    /// path under no `chr\<id>` folder (`...\parts\Arm\...\ref\c2799_AM_A_9490.tga`), the
    /// character the file name starts with, and failing that the character whose model names it.
    pub owner: String,
}

impl TextureRef {
    fn new(owner: &str, name: &str) -> Self {
        Self {
            name: name.to_owned(),
            owner: owner.to_lowercase(),
        }
    }

    fn same(&self, other: &Self) -> bool {
        self.name.eq_ignore_ascii_case(&other.name) && self.owner == other.owner
    }
}

/// The three maps a DSR material samples.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TextureSet {
    pub albedo: TextureRef,
    pub specular: Option<TextureRef>,
    pub normal: Option<TextureRef>,
}

const DSR_ALBEDO: &str = "g_Diffuse";
const DSR_SPECULAR: &str = "g_Specular";
const DSR_NORMAL: &str = "g_Bumpmap";
/// The folder of a DSR texture path whose next component is the character holding it.
const CHR_FOLDER: &str = "chr";

impl TextureSet {
    /// `<id>`, `<id>_s`, `<id>_n` out of character `id`'s own binder: the names DSR gives a
    /// character's own set.
    pub fn named_after(id: &str) -> Self {
        Self {
            albedo: TextureRef::new(id, id),
            specular: Some(TextureRef::new(id, &format!("{id}_s"))),
            normal: Some(TextureRef::new(id, &format!("{id}_n"))),
        }
    }

    /// The one set every one of `materials` samples, as character `id` names it. Two materials
    /// naming different maps for one role carry two sets, and are refused here; see
    /// [`TextureSets::of_materials`] for a character that has several.
    pub fn of_materials(materials: &[SourceMaterial], id: &str) -> Result<Self> {
        let indexed: Vec<(usize, SourceMaterial)> = materials.iter().cloned().enumerate().collect();
        let mut sets = TextureSets::of_materials(&indexed, id)?;
        if let [_, second, ..] = sets.sets.as_slice() {
            let first = &sets.sets[0];
            return Err(Error::new(format!(
                "the materials name two texture sets, {}'s {} and {}'s {}",
                first.albedo.owner, first.albedo.name, second.albedo.owner, second.albedo.name
            )));
        }
        Ok(sets.sets.remove(0))
    }

    /// The maps one material names, by role: albedo, specular, normal.
    fn roles_of(material: &SourceMaterial, id: &str) -> [Option<TextureRef>; 3] {
        let mut found: [Option<TextureRef>; 3] = Default::default();
        for (texture_type, path) in &material.textures {
            let role = [DSR_ALBEDO, DSR_SPECULAR, DSR_NORMAL]
                .iter()
                .position(|name| texture_type.eq_ignore_ascii_case(name));
            if let (Some(role), Some(stem)) = (role, texture_stem(path)) {
                let owner = texture_owner(path)
                    .or_else(|| stem_owner(stem))
                    .unwrap_or(id);
                found[role].get_or_insert(TextureRef::new(owner, stem));
            }
        }
        found
    }
}

/// Every texture set a character's kept materials sample, and which one each material samples.
///
/// Smough's body and his leg armour are two sets, `c2360` and `c2360_LG_M_9200`; the Black
/// Knights' arms, body halves, helm and legs are five. A material joins the first set whose maps
/// do not contradict its own -- a role either of them leaves empty, or the same map -- and fills
/// in what that set lacked, so materials that share an albedo and differ only in naming a
/// specular map stay one set. Set 0 is the set of the lowest-numbered kept material: the one
/// the donor's own Body material draws.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TextureSets {
    pub sets: Vec<TextureSet>,
    /// `(DSR material index, set index)`, one per kept material, ascending.
    materials: Vec<(usize, usize)>,
}

impl TextureSets {
    /// One set for a character drawn by a single material set: c2280, or a test.
    pub fn single(set: TextureSet) -> Self {
        Self {
            sets: vec![set],
            materials: Vec::new(),
        }
    }

    /// The sets `materials` (each with its DSR material index) sample, as character `id` names
    /// them. The caller passes only the materials it keeps, so a stripped weapon's own set is
    /// never one of them.
    pub fn of_materials(materials: &[(usize, SourceMaterial)], id: &str) -> Result<Self> {
        let mut found: Vec<[Option<TextureRef>; 3]> = Vec::new();
        let mut assigned = Vec::with_capacity(materials.len());
        let mut ordered: Vec<&(usize, SourceMaterial)> = materials.iter().collect();
        ordered.sort_by_key(|(index, _)| *index);
        for (index, material) in ordered {
            let roles = TextureSet::roles_of(material, id);
            let agrees = |set: &[Option<TextureRef>; 3]| {
                set.iter().zip(&roles).all(|pair| match pair {
                    (Some(seen), Some(texture)) => seen.same(texture),
                    _ => true,
                })
            };
            let at = match found.iter().position(agrees) {
                Some(at) => at,
                None => {
                    found.push(Default::default());
                    found.len() - 1
                }
            };
            for (slot, texture) in found[at].iter_mut().zip(roles) {
                if slot.is_none() {
                    *slot = texture;
                }
            }
            assigned.push((*index, at));
        }
        let sets = found
            .into_iter()
            .map(|[albedo, specular, normal]| {
                Ok(TextureSet {
                    albedo: albedo.ok_or("no material names an albedo (g_Diffuse) texture")?,
                    specular,
                    normal,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        if sets.is_empty() {
            return Err("no material names an albedo (g_Diffuse) texture".into());
        }
        Ok(Self {
            sets,
            materials: assigned,
        })
    }

    /// The set DSR material `material` samples. A [`Self::single`] set is every material's.
    pub fn set_of(&self, material: usize) -> Option<usize> {
        if self.materials.is_empty() {
            return Some(0);
        }
        self.materials
            .iter()
            .find(|(index, _)| *index == material)
            .map(|&(_, set)| set)
    }

    /// The set the donor's Body material draws.
    pub fn primary(&self) -> &TextureSet {
        &self.sets[0]
    }

    /// The characters whose binders hold any of the sets, each once, set by set.
    pub fn owners(&self) -> Vec<&str> {
        let mut owners: Vec<&str> = Vec::new();
        for owner in self.sets.iter().flat_map(TextureSet::owners) {
            if !owners.contains(&owner) {
                owners.push(owner);
            }
        }
        owners
    }

    /// Every set [`TextureSet::without_absent_maps`].
    pub fn without_absent_maps(self, tpfs: &[(&str, &[u8])]) -> Result<Self> {
        Ok(Self {
            sets: self
                .sets
                .into_iter()
                .map(|set| set.without_absent_maps(tpfs))
                .collect::<Result<_>>()?,
            materials: self.materials,
        })
    }
}

impl TextureSet {
    /// The set without a specular or normal map that none of `tpfs` holds, as [`character_tpf_from`]
    /// takes them, so the generated flat one stands in. A missing albedo has no stand-in and is
    /// refused: c4091's materials name `c4090` and `c4090_s`, and c4090's TPF holds neither.
    pub fn without_absent_maps(mut self, tpfs: &[(&str, &[u8])]) -> Result<Self> {
        let mut held: Vec<(String, String)> = Vec::new();
        for &(owner, bytes) in tpfs {
            let tpf = parse_dsr_tpf(bytes, &format!("{owner}'s"))?;
            held.extend(
                tpf.textures
                    .into_iter()
                    .map(|texture| (owner.to_lowercase(), texture.name.to_lowercase())),
            );
        }
        let present = |texture: &TextureRef| {
            held.iter().any(|(owner, name)| {
                *owner == texture.owner && name.eq_ignore_ascii_case(&texture.name)
            })
        };
        if !present(&self.albedo) {
            return Err(Error::new(format!(
                "its albedo {} is in no TPF of {}",
                self.albedo.name, self.albedo.owner
            )));
        }
        self.specular = self.specular.filter(present);
        self.normal = self.normal.filter(present);
        Ok(self)
    }

    /// The characters whose binders hold the set, each once, in albedo, specular, normal order.
    pub fn owners(&self) -> Vec<&str> {
        let mut owners: Vec<&str> = Vec::new();
        for texture in [
            Some(&self.albedo),
            self.specular.as_ref(),
            self.normal.as_ref(),
        ]
        .into_iter()
        .flatten()
        {
            if !owners.contains(&texture.owner.as_str()) {
                owners.push(&texture.owner);
            }
        }
        owners
    }
}

/// `N:\FRPG\data\Model\chr\c2650\tex\c2650_n.tga` -> `c2650_n`; an empty path is no texture.
fn texture_stem(path: &str) -> Option<&str> {
    let file = path.rsplit(['\\', '/']).next()?.trim();
    let stem = file.rsplit_once('.').map_or(file, |(stem, _)| stem);
    (!stem.is_empty()).then_some(stem)
}

/// `N:\FRPG\data\Model\chr\c2909\tex\c2909.tga` -> `c2909`: the character folder after `chr`, when
/// the path has one.
fn texture_owner(path: &str) -> Option<&str> {
    let mut parts = path.split(['\\', '/']);
    parts.find(|part| part.eq_ignore_ascii_case(CHR_FOLDER))?;
    let id = parts.next()?.trim();
    is_character_id(id).then_some(id)
}

/// `c2799_AM_A_9490` -> `c2799`: the character a texture's name starts with. The Black Knights'
/// armour is drawn from `N:\FRPG\data\Model\parts\Arm\AM_A_9490\tex\ref\c2799_AM_A_9490.tga`, a
/// path under no character folder, and `c2799`'s binder is the one holding it; a weapon's
/// `c2900_WP_A_0400` names its own character the same way.
fn stem_owner(stem: &str) -> Option<&str> {
    let id = stem.get(..5)?;
    let rest = &stem[5..];
    (is_character_id(id) && rest.starts_with('_')).then_some(id)
}

/// `c` or `C` and four digits.
fn is_character_id(id: &str) -> bool {
    id.strip_prefix(['c', 'C'])
        .is_some_and(|digits| digits.len() == 4 && digits.bytes().all(|b| b.is_ascii_digit()))
}

/// The donor slots in the order the donor manifest lists them.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Slot {
    Mask,
    Albedo,
    Normal,
}

const SLOTS: [(Slot, &str); 3] = [
    (Slot::Mask, "_3m"),
    (Slot::Albedo, ALBEDO_SLOT),
    (Slot::Normal, NORMAL_SLOT),
];

/// A TPF for `target_prefix` (`FC_M_0000`) holding c2280's three textures; `lod_suffix` is
/// `""` for the high-detail binder and `"_l"` for the low one.
pub fn mushroom_tpf(dsr_tpf: &[u8], target_prefix: &str, lod_suffix: &str) -> Result<Vec<u8>> {
    character_tpf(
        dsr_tpf,
        &TextureSet::named_after("c2280"),
        target_prefix,
        lod_suffix,
    )
}

/// A TPF for `target_prefix` holding `set`, every texture of it out of the one TPF `dsr_tpf`.
pub fn character_tpf(
    dsr_tpf: &[u8],
    set: &TextureSet,
    target_prefix: &str,
    lod_suffix: &str,
) -> Result<Vec<u8>> {
    let source = parse_dsr_tpf(dsr_tpf, "the character's")?;
    let lookup = |texture: &TextureRef| find_texture(&source, texture, "the character's");
    donor_tpf(lookup, set, target_prefix, lod_suffix)
}

/// A TPF for `target_prefix` holding `set`, each texture out of the TPF of the character that
/// holds it: `tpfs` pairs a character id with that character's TPF (`c2909` with `c2909.tpf`).
pub fn character_tpf_from(
    tpfs: &[(&str, &[u8])],
    set: &TextureSet,
    target_prefix: &str,
    lod_suffix: &str,
) -> Result<Vec<u8>> {
    let sources = read_sources(tpfs)?;
    donor_tpf(
        |texture| lookup_in(&sources, texture),
        set,
        target_prefix,
        lod_suffix,
    )
}

/// The prefix set `set`'s textures take in the body TPF: `FC_M_0000` for set 0, whose names the
/// Body material's matbin fixes, and `FC_M_0000_<set>` for every other, which only a material
/// naming them by path ([`crate::edge::set_textures`]) samples.
pub fn set_prefix(target_prefix: &str, set: usize) -> String {
    if set == 0 {
        target_prefix.to_owned()
    } else {
        format!("{target_prefix}_{set}")
    }
}

/// The body TPF for `sets`: set 0 in the donor's three slots, as [`character_tpf_from`] writes
/// it; with `metallic`, the zero metallic map every `C[AMSN]` material samples
/// ([`with_zero_metallic`]); and each set of `extra` as an albedo and a normal map under its
/// [`set_prefix`], a flat normal standing in for one it lacks. A `C[AMSN]` material samples no
/// specular map, so an extra set's is not carried. Every texture goes in in name order.
pub fn body_tpf(
    tpfs: &[(&str, &[u8])],
    sets: &TextureSets,
    extra: &[usize],
    metallic: bool,
    target_prefix: &str,
    lod_suffix: &str,
) -> Result<Vec<u8>> {
    let mut tpf = character_tpf_from(tpfs, sets.primary(), target_prefix, lod_suffix)?;
    if metallic {
        tpf = with_zero_metallic(&tpf, target_prefix, lod_suffix)?;
    }
    if extra.is_empty() {
        return Ok(tpf);
    }
    let sources = read_sources(tpfs)?;
    let mut built = Tpf::parse(&tpf).map_err(|error| Error::new(format!("TPF: {error}")))?;
    for &set in extra {
        if set == 0 {
            return Err("set 0 is the Body material's, in the donor's own slots".into());
        }
        let texture_set = sets.sets.get(set).ok_or_else(|| {
            Error::new(format!(
                "texture set {set}: the character has {}",
                sets.sets.len()
            ))
        })?;
        let prefix = set_prefix(target_prefix, set);
        let albedo = as_srgb_albedo(lookup_in(&sources, &texture_set.albedo)?);
        let normal = match &texture_set.normal {
            Some(texture) => lookup_in(&sources, texture)?,
            None => flat_normal(),
        };
        for (slot_suffix, dds) in [(ALBEDO_SLOT, albedo), (NORMAL_SLOT, normal)] {
            insert_in_name_order(
                &mut built,
                tpf_texture(format!("{prefix}{slot_suffix}{lod_suffix}"), dds)?,
            )?;
        }
    }
    write_tpf(&built)
}

/// Each `(owner, TPF)` of `tpfs` parsed, under the owner's lower-case id.
fn read_sources(tpfs: &[(&str, &[u8])]) -> Result<Vec<(String, Tpf)>> {
    tpfs.iter()
        .map(|&(owner, bytes)| {
            let whose = format!("{owner}'s");
            Ok((owner.to_lowercase(), parse_dsr_tpf(bytes, &whose)?))
        })
        .collect()
}

/// `texture` out of the TPF of the character holding it.
fn lookup_in(sources: &[(String, Tpf)], texture: &TextureRef) -> Result<Vec<u8>> {
    let (_, tpf) = sources
        .iter()
        .find(|(owner, _)| *owner == texture.owner)
        .ok_or_else(|| {
            Error::new(format!(
                "{} is {}'s, and no TPF of {} was read",
                texture.name, texture.owner, texture.owner
            ))
        })?;
    find_texture(tpf, texture, &format!("{}'s", texture.owner))
}

/// A TPF entry for `dds` under `name`, its format byte and mip count read out of the DDS.
fn tpf_texture(name: String, dds: Vec<u8>) -> Result<TpfTexture> {
    Ok(TpfTexture {
        format: tpf_format_byte(&dds)?,
        tex_type: TEX_TYPE_TEXTURE,
        mip_count: dds_mip_count(&dds)?,
        flags1: 0,
        dds,
        name,
    })
}

/// `texture` added to `tpf` before the first name that sorts after its own, ignoring case. A name
/// the TPF already holds is refused.
fn insert_in_name_order(tpf: &mut Tpf, texture: TpfTexture) -> Result<()> {
    if tpf
        .textures
        .iter()
        .any(|other| other.name.eq_ignore_ascii_case(&texture.name))
    {
        return Err(Error::new(format!(
            "the TPF already holds {}",
            texture.name
        )));
    }
    let key = texture.name.to_lowercase();
    let at = tpf
        .textures
        .iter()
        .position(|other| other.name.to_lowercase() > key)
        .unwrap_or(tpf.textures.len());
    tpf.textures.insert(at, texture);
    Ok(())
}

/// One TPF holding every texture of `tpfs`, in order, with the first one's header: a character
/// that keeps its textures split, one to a TPF, as [`character_tpf_from`] takes a character's.
/// Two textures with one name are refused, since a lookup by name could take either.
pub fn merge_tpfs(tpfs: &[Vec<u8>]) -> Result<Vec<u8>> {
    let mut merged: Option<Tpf> = None;
    for (index, bytes) in tpfs.iter().enumerate() {
        let tpf = parse_dsr_tpf(bytes, &format!("split texture {index}'s"))?;
        let Some(into) = merged.as_mut() else {
            merged = Some(tpf);
            continue;
        };
        for texture in tpf.textures {
            if into
                .textures
                .iter()
                .any(|seen| seen.name.eq_ignore_ascii_case(&texture.name))
            {
                return Err(Error::new(format!(
                    "two split TPFs hold a texture named {}",
                    texture.name
                )));
            }
            into.textures.push(texture);
        }
    }
    write_tpf(&merged.ok_or("no split TPF to merge")?)
}

fn parse_dsr_tpf(bytes: &[u8], whose: &str) -> Result<Tpf> {
    Tpf::parse(bytes).map_err(|error| Error::new(format!("{whose} DSR TPF: {error}")))
}

fn find_texture(tpf: &Tpf, texture: &TextureRef, whose: &str) -> Result<Vec<u8>> {
    tpf.textures
        .iter()
        .find(|candidate| candidate.name.eq_ignore_ascii_case(&texture.name))
        .map(|candidate| candidate.dds.clone())
        .ok_or_else(|| Error::new(format!("{whose} TPF has no texture {}", texture.name)))
}

/// The donor's three slots filled from `set` through `lookup`.
fn donor_tpf(
    lookup: impl Fn(&TextureRef) -> Result<Vec<u8>>,
    set: &TextureSet,
    target_prefix: &str,
    lod_suffix: &str,
) -> Result<Vec<u8>> {
    let mut textures = Vec::with_capacity(SLOTS.len());
    for (slot, slot_suffix) in SLOTS {
        let dds = match slot {
            Slot::Albedo => as_srgb_albedo(lookup(&set.albedo)?),
            Slot::Mask => match &set.specular {
                Some(texture) => lookup(texture)?,
                None => flat_mask(),
            },
            Slot::Normal => match &set.normal {
                Some(texture) => lookup(texture)?,
                None => flat_normal(),
            },
        };
        textures.push(TpfTexture {
            name: format!("{target_prefix}{slot_suffix}{lod_suffix}"),
            format: tpf_format_byte(&dds)?,
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
const NORMAL_SLOT: &str = "_n";
const DDS_FOURCC_OFFSET: usize = 0x54;
const DDS_HEADER_END: usize = 0x80;
const DX10_HEADER_SIZE: usize = 20;
const DX10_TEXTURE_2D: u32 = 3;

/// The `DXGI_FORMAT` values this module reads or writes.
mod dxgi {
    pub const BC1_UNORM: u32 = 71;
    /// How Elden Ring stores its own body albedo, `FC_M_0000_a`.
    pub const BC1_UNORM_SRGB: u32 = 72;
    pub const BC2_UNORM: u32 = 74;
    pub const BC2_UNORM_SRGB: u32 = 75;
    pub const BC3_UNORM: u32 = 77;
    pub const BC3_UNORM_SRGB: u32 = 78;
    pub const BC4_UNORM: u32 = 80;
    pub const BC5_UNORM: u32 = 83;
    pub const BC7_UNORM: u32 = 98;
    pub const BC7_UNORM_SRGB: u32 = 99;
}

/// An albedo under a header that says sRGB, as Elden Ring's own albedos are, so its colours are
/// not read as linear and washed out. The pixels are unchanged.
///
/// A legacy `DXT1`/`DXT3`/`DXT5` header names no colour space, so it becomes a DX10 header naming
/// the sRGB form of the same blocks (c2280's albedo is `DXT1`). A DX10 header naming a linear
/// format is switched to its sRGB twin (c2650's is `BC7_UNORM`). Anything else passes through.
fn as_srgb_albedo(dds: Vec<u8>) -> Vec<u8> {
    let srgb_of_legacy = match dds.get(DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4) {
        _ if dds.len() < DDS_HEADER_END => return dds,
        Some(b"DXT1") => dxgi::BC1_UNORM_SRGB,
        Some(b"DXT3") => dxgi::BC2_UNORM_SRGB,
        Some(b"DXT5") => dxgi::BC3_UNORM_SRGB,
        Some(b"DX10") => {
            let mut dds = dds;
            let Ok(format) = crate::bytes::u32_le(&dds, DDS_HEADER_END) else {
                return dds;
            };
            let srgb = match format {
                dxgi::BC1_UNORM => dxgi::BC1_UNORM_SRGB,
                dxgi::BC2_UNORM => dxgi::BC2_UNORM_SRGB,
                dxgi::BC3_UNORM => dxgi::BC3_UNORM_SRGB,
                dxgi::BC7_UNORM => dxgi::BC7_UNORM_SRGB,
                other => other,
            };
            dds[DDS_HEADER_END..DDS_HEADER_END + 4].copy_from_slice(&srgb.to_le_bytes());
            return dds;
        }
        _ => return dds,
    };
    let mut out = Vec::with_capacity(dds.len() + DX10_HEADER_SIZE);
    out.extend_from_slice(&dds[..DDS_HEADER_END]);
    out[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4].copy_from_slice(b"DX10");
    for value in [srgb_of_legacy, DX10_TEXTURE_2D, 0, 1, 0] {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out.extend_from_slice(&dds[DDS_HEADER_END..]);
    out
}

/// The TPF format byte Elden Ring's own parts give a texture in this DDS format, from the 9524
/// textures of the extracted parts binders: BC1 0, BC7 sRGB (an albedo) 102, BC4 103, BC7 linear
/// (a normal map) 107. BC5 is 107 rather than vanilla's 104 because c2280's BC5 normal map
/// shipped and was approved under 107. DSR's character textures are DXT1, BC5 and BC7 only, so
/// any other format is refused rather than given a guessed byte.
fn tpf_format_byte(dds: &[u8]) -> Result<u8> {
    let fourcc = crate::bytes::slice(dds, DDS_FOURCC_OFFSET, 4)?;
    let byte = match fourcc {
        b"DXT1" => Some(0),
        b"ATI1" | b"BC4U" => Some(103),
        b"ATI2" | b"BC5U" => Some(107),
        b"DX10" => match crate::bytes::u32_le(dds, DDS_HEADER_END)? {
            dxgi::BC1_UNORM | dxgi::BC1_UNORM_SRGB => Some(0),
            dxgi::BC7_UNORM_SRGB => Some(102),
            dxgi::BC4_UNORM => Some(103),
            dxgi::BC5_UNORM | dxgi::BC7_UNORM => Some(107),
            _ => None,
        },
        _ => None,
    };
    byte.ok_or_else(|| {
        Error::new(format!(
            "no Elden Ring TPF format byte is known for a DDS of fourcc {} (DXGI {:?})",
            fourcc.escape_ascii(),
            crate::bytes::u32_le(dds, DDS_HEADER_END).ok()
        ))
    })
}

/// Edge length of a generated stand-in map, with a full mip chain under it.
const GENERATED_SIZE: u32 = 64;
const DDSD_CAPS_HEIGHT_WIDTH_PIXELFORMAT_MIPMAPCOUNT_LINEARSIZE: u32 = 0x000A_1007;
const DDPF_FOURCC: u32 = 0x4;
const DDSCAPS_COMPLEX_TEXTURE_MIPMAP: u32 = 0x0040_1008;
const DDS_HEADER_SIZE: u32 = 124;
const DDS_PIXEL_FORMAT_SIZE: u32 = 32;

/// A zero `_3m` for a material with no specular map: legacy DXT1, every block black, the same
/// header shape c2280's own `_s` has.
fn flat_mask() -> Vec<u8> {
    generated_dds(b"DXT1", None, &[0; 8])
}

/// A flat `_n` for a material with no normal map: BC5 with red and green both at 0x80, which
/// decodes to a normal straight out of the surface.
fn flat_normal() -> Vec<u8> {
    let half = [0x80, 0x80, 0, 0, 0, 0, 0, 0];
    generated_dds(b"DX10", Some(dxgi::BC5_UNORM), &[half, half].concat())
}

const METALLIC_SLOT: &str = "_m";

/// The metallic map a `C[AMSN]` material samples, `_m`, with nothing metallic in it: BC4 under
/// the legacy `ATI1` code, every block zero. Vanilla `FC_M_0090` ships its `FC_M_0000_m` in the
/// same size and format. c2650's specular green, the channel a metallic map would come from,
/// averages 0.1 of 255, so zero is what it holds.
pub fn zero_metallic() -> Vec<u8> {
    generated_dds(b"ATI1", None, &[0; 8])
}

/// `tpf` with [`zero_metallic`] added as `<target_prefix>_m<lod_suffix>`, for a body whose Edge
/// material samples one (see `edge.rs`). It goes in among the others in name order, as the
/// three it joins already are; they are kept byte for byte.
pub fn with_zero_metallic(tpf: &[u8], target_prefix: &str, lod_suffix: &str) -> Result<Vec<u8>> {
    let mut built = Tpf::parse(tpf).map_err(|error| Error::new(format!("TPF: {error}")))?;
    let name = format!("{target_prefix}{METALLIC_SLOT}{lod_suffix}");
    insert_in_name_order(&mut built, tpf_texture(name, zero_metallic())?)?;
    write_tpf(&built)
}

/// A square block-compressed DDS of [`GENERATED_SIZE`], every block of every mip level `block`.
fn generated_dds(fourcc: &[u8; 4], dxgi_format: Option<u32>, block: &[u8]) -> Vec<u8> {
    let mips = GENERATED_SIZE.ilog2() + 1;
    let blocks_at = |level: u32| ((GENERATED_SIZE >> level).div_ceil(4)).pow(2) as usize;
    let mut out = Vec::new();
    out.extend_from_slice(DDS_MAGIC);
    for value in [
        DDS_HEADER_SIZE,
        DDSD_CAPS_HEIGHT_WIDTH_PIXELFORMAT_MIPMAPCOUNT_LINEARSIZE,
        GENERATED_SIZE,
        GENERATED_SIZE,
        (blocks_at(0) * block.len()) as u32,
        0,
        mips,
    ] {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out.resize(DDS_HEADER_END - 0x34, 0);
    for value in [DDS_PIXEL_FORMAT_SIZE, DDPF_FOURCC] {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out.extend_from_slice(fourcc);
    out.resize(DDS_HEADER_END - 0x14, 0);
    out.extend_from_slice(&DDSCAPS_COMPLEX_TEXTURE_MIPMAP.to_le_bytes());
    out.resize(DDS_HEADER_END, 0);
    if let Some(format) = dxgi_format {
        for value in [format, DX10_TEXTURE_2D, 0, 1, 0] {
            out.extend_from_slice(&value.to_le_bytes());
        }
    }
    for level in 0..mips {
        for _ in 0..blocks_at(level) {
            out.extend_from_slice(block);
        }
    }
    out
}

/// `dwMipMapCount` out of a DDS header, at least one.
fn dds_mip_count(dds: &[u8]) -> Result<u8> {
    if dds.get(..4) != Some(DDS_MAGIC.as_slice()) {
        return Err(Error::new("a DSR texture is not a DDS file"));
    }
    let count = crate::bytes::u32_le(dds, DDS_MIP_COUNT_OFFSET)?;
    Ok(u8::try_from(count.max(1)).unwrap_or(u8::MAX))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_legacy_dxt1_albedo_gets_the_srgb_dx10_header_and_keeps_its_pixels() {
        let legacy = legacy_dds(3, 7, b"DXT1");
        let srgb = as_srgb_albedo(legacy.clone());
        assert_eq!(&srgb[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4], b"DX10");
        assert_eq!(
            crate::bytes::u32_le(&srgb, DDS_HEADER_END).unwrap(),
            dxgi::BC1_UNORM_SRGB
        );
        assert_eq!(&srgb[DDS_HEADER_END + 20..], &legacy[DDS_HEADER_END..]);
        assert_eq!(dds_mip_count(&srgb).unwrap(), 3);
        assert_eq!(
            crate::bytes::u32_le(&as_srgb_albedo(legacy_dds(1, 7, b"DXT5")), DDS_HEADER_END)
                .unwrap(),
            dxgi::BC3_UNORM_SRGB
        );
        // A format with no sRGB twin passes through untouched.
        let other = legacy_dds(3, 9, b"ATI2");
        assert_eq!(as_srgb_albedo(other.clone()), other);
    }

    #[test]
    fn a_dx10_bc7_albedo_becomes_bc7_srgb_with_its_pixels_unchanged() {
        let linear = dx10_dds(11, 0x5A, dxgi::BC7_UNORM);
        let srgb = as_srgb_albedo(linear.clone());
        assert_eq!(srgb.len(), linear.len());
        assert_eq!(
            crate::bytes::u32_le(&srgb, DDS_HEADER_END).unwrap(),
            dxgi::BC7_UNORM_SRGB
        );
        assert_eq!(&srgb[..DDS_HEADER_END], &linear[..DDS_HEADER_END]);
        assert_eq!(&srgb[DDS_HEADER_END + 4..], &linear[DDS_HEADER_END + 4..]);
        // Already sRGB, and normal-map formats, are left alone.
        let already = dx10_dds(1, 0, dxgi::BC1_UNORM_SRGB);
        assert_eq!(as_srgb_albedo(already.clone()), already);
        let normal = dx10_dds(1, 0, dxgi::BC5_UNORM);
        assert_eq!(as_srgb_albedo(normal.clone()), normal);
    }

    #[test]
    fn the_format_byte_follows_the_dds_format() {
        let byte = |dds: Vec<u8>| tpf_format_byte(&dds).unwrap();
        assert_eq!(byte(legacy_dds(1, 0, b"DXT1")), 0);
        assert_eq!(byte(dx10_dds(1, 0, dxgi::BC1_UNORM_SRGB)), 0);
        assert_eq!(byte(dx10_dds(1, 0, dxgi::BC7_UNORM_SRGB)), 102);
        assert_eq!(byte(dx10_dds(1, 0, dxgi::BC7_UNORM)), 107);
        assert_eq!(byte(dx10_dds(1, 0, dxgi::BC5_UNORM)), 107);
        assert_eq!(byte(legacy_dds(1, 0, b"ATI2")), 107);
        assert_eq!(byte(dx10_dds(1, 0, dxgi::BC4_UNORM)), 103);
        assert_eq!(byte(legacy_dds(1, 0, b"ATI1")), 103);
        assert!(tpf_format_byte(&legacy_dds(1, 0, b"DXT5")).is_err());
        assert!(tpf_format_byte(&dx10_dds(1, 0, 28)).is_err());
    }

    #[test]
    fn generated_stand_ins_are_full_mip_chains_the_tpf_check_accepts() {
        for (dds, fourcc, format, block) in [
            (flat_mask(), b"DXT1", 0, 8),
            (flat_normal(), b"DX10", 107, 16),
        ] {
            assert_eq!(&dds[..4], DDS_MAGIC);
            assert_eq!(&dds[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4], fourcc);
            assert_eq!(dds_mip_count(&dds).unwrap(), 7, "64, 32, 16, 8, 4, 2, 1");
            assert_eq!(tpf_format_byte(&dds).unwrap(), format);
            let header = DDS_HEADER_END
                + if fourcc == b"DX10" {
                    DX10_HEADER_SIZE
                } else {
                    0
                };
            assert_eq!(dds.len(), header + (256 + 64 + 16 + 4 + 1 + 1 + 1) * block);
            assert_eq!(crate::bytes::u32_le(&dds, 4).unwrap(), DDS_HEADER_SIZE);
        }
        assert!(flat_mask()[DDS_HEADER_END..].iter().all(|&b| b == 0));
        assert_eq!(flat_normal()[DDS_HEADER_END + DX10_HEADER_SIZE], 0x80);
    }

    fn dds(mips: u32, fill: u8) -> Vec<u8> {
        let mut bytes = vec![fill; 0x80 + 16];
        bytes[..4].copy_from_slice(DDS_MAGIC);
        bytes[DDS_MIP_COUNT_OFFSET..DDS_MIP_COUNT_OFFSET + 4].copy_from_slice(&mips.to_le_bytes());
        bytes
    }

    fn legacy_dds(mips: u32, fill: u8, fourcc: &[u8; 4]) -> Vec<u8> {
        let mut bytes = dds(mips, fill);
        bytes[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4].copy_from_slice(fourcc);
        bytes
    }

    fn dx10_dds(mips: u32, fill: u8, format: u32) -> Vec<u8> {
        let mut bytes = legacy_dds(mips, fill, b"DX10");
        bytes[DDS_HEADER_END..DDS_HEADER_END + 4].copy_from_slice(&format.to_le_bytes());
        bytes
    }

    fn texture(name: &str, dds: Vec<u8>) -> TpfTexture {
        TpfTexture {
            name: name.to_owned(),
            format: 0,
            tex_type: TEX_TYPE_TEXTURE,
            mip_count: 1,
            flags1: 0,
            dds,
        }
    }

    fn source_tpf(textures: Vec<TpfTexture>) -> Vec<u8> {
        Tpf {
            platform: TPF_PLATFORM_PC,
            flag2: FLAG2,
            encoding: TPF_ENCODING_UTF16,
            textures,
        }
        .build()
        .unwrap()
    }

    #[test]
    fn split_tpfs_merge_into_one_a_set_is_read_out_of() {
        let split = [
            source_tpf(vec![texture("c2320", legacy_dds(5, 1, b"DXT1"))]),
            source_tpf(vec![texture("c2320_n", dx10_dds(7, 3, dxgi::BC5_UNORM))]),
            source_tpf(vec![texture("c2320_s", legacy_dds(6, 2, b"DXT1"))]),
        ];
        let merged = merge_tpfs(&split).unwrap();
        let parsed = Tpf::parse(&merged).unwrap();
        let names: Vec<&str> = parsed.textures.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["c2320", "c2320_n", "c2320_s"]);
        let built = character_tpf_from(
            &[("c2320", merged.as_slice())],
            &TextureSet::named_after("c2320"),
            "FC_M_0000",
            "",
        )
        .unwrap();
        assert_eq!(Tpf::parse(&built).unwrap().textures.len(), 3);
        // One name twice is refused, and so is nothing at all.
        let twice = [split[0].clone(), split[0].clone()];
        assert!(merge_tpfs(&twice).is_err());
        assert!(merge_tpfs(&[]).is_err());
    }

    #[test]
    fn the_three_c2280_textures_land_in_the_donor_slots_in_manifest_order() {
        let source = source_tpf(vec![
            texture("c2280", legacy_dds(5, 1, b"DXT1")),
            texture("c2280_s", legacy_dds(6, 2, b"DXT1")),
            texture("c2280_n", dx10_dds(7, 3, dxgi::BC5_UNORM)),
        ]);
        let bytes = mushroom_tpf(&source, "FC_M_0000", "_l").unwrap();
        let built = Tpf::parse(&bytes).unwrap();
        let names: Vec<&str> = built.textures.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["FC_M_0000_3m_l", "FC_M_0000_a_l", "FC_M_0000_n_l"]);
        assert_eq!(
            built.textures[0].dds[0x80], 2,
            "specular comes from c2280_s"
        );
        assert_eq!(built.textures[1].mip_count, 5);
        let formats: Vec<u8> = built.textures.iter().map(|t| t.format).collect();
        assert_eq!(formats, [0, 0, 107], "the bytes c2280 shipped with");
        assert_eq!(
            write_tpf(&built).unwrap(),
            bytes,
            "our own output round-trips"
        );
    }

    #[test]
    fn a_set_comes_from_the_materials_and_a_missing_map_is_generated() {
        let material = |textures: &[(&str, &str)]| SourceMaterial {
            textures: textures
                .iter()
                .map(|&(kind, path)| (kind.to_owned(), path.to_owned()))
                .collect(),
            ..SourceMaterial::default()
        };
        let path = |stem: &str| format!(r"N:\FRPG\data\Model\chr\c2650\tex\{stem}.tga");
        let full = material(&[
            ("g_Diffuse", &path("c2650")),
            ("g_Specular", &path("c2650_s")),
            ("g_Bumpmap", &path("c2650_n")),
            ("g_DetailBumpmap", ""),
        ]);
        let edge = full.clone();
        assert_eq!(
            TextureSet::of_materials(&[full.clone(), edge], "c2650").unwrap(),
            TextureSet::named_after("c2650")
        );
        let diffuse_only = material(&[("g_Diffuse", &path("c2650")), ("g_DetailBumpmap", "")]);
        let set = TextureSet::of_materials(&[diffuse_only], "c2650").unwrap();
        assert_eq!((&set.specular, &set.normal), (&None, &None));
        let weapon = material(&[("g_Diffuse", &path("c2650_WP_A_0200"))]);
        assert!(TextureSet::of_materials(&[full, weapon], "c2650").is_err());

        let tpf = source_tpf(vec![texture("c2650", dx10_dds(11, 4, dxgi::BC7_UNORM))]);
        let built = Tpf::parse(&character_tpf(&tpf, &set, "FC_M_0000", "").unwrap()).unwrap();
        let formats: Vec<u8> = built.textures.iter().map(|t| t.format).collect();
        assert_eq!(formats, [0, 102, 107]);
        assert_eq!(built.textures[0].dds, flat_mask());
        assert_eq!(built.textures[2].dds, flat_normal());
        let missing = TextureSet::named_after("c2519");
        assert!(character_tpf(&tpf, &missing, "FC_M_0000", "").is_err());
    }

    #[test]
    fn a_texture_belongs_to_the_character_folder_its_path_names() {
        assert_eq!(
            texture_owner(r"N:\FRPG\data\Model\chr\c2909\tex\c2909_n.tga"),
            Some("c2909")
        );
        assert_eq!(
            texture_owner("N:/FRPG/data/Model/CHR/C2519/tex/c2519.tga"),
            Some("C2519")
        );
        for elsewhere in [
            r"N:\FRPG\data\Model\parts\Weapon\WP_A_0400\tex\ref\c2900_WP_A_0400.tga",
            r"N:\FRPG\data\Model\chr\tex\c2909.tga",
            r"N:\FRPG\data\Model\chr\c29\tex\c29.tga",
            "c2909.tga",
            "",
        ] {
            assert_eq!(texture_owner(elsewhere), None, "{elsewhere}");
        }
        // With no character folder, a name that starts with a character id names its owner.
        assert_eq!(stem_owner("c2799_AM_A_9490"), Some("c2799"));
        assert_eq!(stem_owner("c2900_WP_A_0400"), Some("c2900"));
        for unowned in ["c2909", "c29_x", "AM_M_9410_L", "c27990_a", "M_9470_hair_L"] {
            assert_eq!(stem_owner(unowned), None, "{unowned}");
        }

        // c2900's two body materials sample c2909's set; a weapon path names no character
        // folder, so it is c2900's own.
        let material = |stems: [&str; 3], dir: &str| SourceMaterial {
            textures: [DSR_ALBEDO, DSR_SPECULAR, DSR_NORMAL]
                .iter()
                .zip(stems)
                .map(|(kind, stem)| ((*kind).to_owned(), format!(r"{dir}\{stem}.tga")))
                .collect(),
            ..SourceMaterial::default()
        };
        let body = material(
            ["c2909", "c2909_s", "c2909_n"],
            r"N:\FRPG\data\Model\chr\c2909\tex",
        );
        let set = TextureSet::of_materials(&[body.clone(), body.clone()], "c2900").unwrap();
        assert_eq!(set, TextureSet::named_after("c2909"));
        assert_eq!(set.owners(), ["c2909"]);
        let sword = material(
            ["c2900_WP_A_0400", "c2900_WP_A_0400_s", "c2900_WP_A_0400_n"],
            r"N:\FRPG\data\Model\parts\Weapon\WP_A_0400\tex\ref",
        );
        let set = TextureSet::of_materials(std::slice::from_ref(&sword), "c2900").unwrap();
        assert_eq!(set.albedo, TextureRef::new("c2900", "c2900_WP_A_0400"));
        // Kept together, the body and the sword are two sets.
        let error = TextureSet::of_materials(&[body, sword], "c2900")
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("c2909's c2909 and c2900's c2900_WP_A_0400"),
            "{error}"
        );
    }

    fn smough_material(stems: &[(&str, &str)]) -> SourceMaterial {
        SourceMaterial {
            textures: stems
                .iter()
                .map(|&(kind, stem)| {
                    (
                        kind.to_owned(),
                        format!(r"N:\FRPG\data\Model\chr\c2360\tex\{stem}.tga"),
                    )
                })
                .collect(),
            ..SourceMaterial::default()
        }
    }

    /// Smough's shape: a body set on materials 0, 2 and 3 (2 naming no specular map) and his
    /// leg armour's own set on material 1.
    fn smough() -> Vec<(usize, SourceMaterial)> {
        let body = [
            (DSR_ALBEDO, "c2360"),
            (DSR_SPECULAR, "c2360_s"),
            (DSR_NORMAL, "c2360_n"),
        ];
        let legs = [(DSR_ALBEDO, "c2360_LG_M_9200")];
        vec![
            (3, smough_material(&body)),
            (1, smough_material(&legs)),
            (0, smough_material(&body)),
            (2, smough_material(&[body[0], body[2]])),
        ]
    }

    #[test]
    fn materials_split_into_sets_by_the_maps_they_disagree_on() {
        let sets = TextureSets::of_materials(&smough(), "c2360").unwrap();
        assert_eq!(sets.sets.len(), 2);
        assert_eq!(*sets.primary(), TextureSet::named_after("c2360"));
        assert_eq!(
            sets.sets[1].albedo,
            TextureRef::new("c2360", "c2360_LG_M_9200")
        );
        assert_eq!(
            (&sets.sets[1].specular, &sets.sets[1].normal),
            (&None, &None)
        );
        let of: Vec<Option<usize>> = (0..5).map(|material| sets.set_of(material)).collect();
        assert_eq!(of, [Some(0), Some(1), Some(0), Some(0), None]);
        assert_eq!(sets.owners(), ["c2360"]);
        // The sets follow the material numbers, not the order the materials are passed in.
        let reversed: Vec<_> = smough().into_iter().rev().collect();
        assert_eq!(TextureSets::of_materials(&reversed, "c2360").unwrap(), sets);
        // The one-set reading refuses the two, naming both albedos.
        let flat: Vec<SourceMaterial> = smough().into_iter().map(|(_, m)| m).collect();
        let error = TextureSet::of_materials(&flat, "c2360")
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("c2360's c2360 and c2360's c2360_LG_M_9200"),
            "{error}"
        );
        assert_eq!(
            TextureSets::single(sets.primary().clone()).set_of(7),
            Some(0)
        );
    }

    #[test]
    fn a_further_set_goes_into_the_body_tpf_as_an_albedo_and_a_normal_under_its_prefix() {
        let source = source_tpf(vec![
            texture("c2360", legacy_dds(5, 1, b"DXT1")),
            texture("c2360_s", legacy_dds(5, 2, b"DXT1")),
            texture("c2360_n", dx10_dds(5, 3, dxgi::BC5_UNORM)),
            texture("c2360_LG_M_9200", dx10_dds(6, 4, dxgi::BC7_UNORM)),
        ]);
        let sets = TextureSets::of_materials(&smough(), "c2360").unwrap();
        let tpfs = [("c2360", source.as_slice())];
        for lod in ["", "_l"] {
            let only = body_tpf(&tpfs, &sets, &[], false, "FC_M_0000", lod).unwrap();
            assert_eq!(
                only,
                character_tpf_from(&tpfs, sets.primary(), "FC_M_0000", lod).unwrap(),
                "with no further set it is the one-set TPF"
            );
            let built =
                Tpf::parse(&body_tpf(&tpfs, &sets, &[1], true, "FC_M_0000", lod).unwrap()).unwrap();
            let names: Vec<&str> = built.textures.iter().map(|t| t.name.as_str()).collect();
            let expected = ["_1_a", "_1_n", "_3m", "_a", "_m", "_n"]
                .map(|slot| format!("FC_M_0000{slot}{lod}"));
            assert_eq!(names, expected);
            let albedo = &built.textures[0];
            assert_eq!(
                crate::bytes::u32_le(&albedo.dds, DDS_HEADER_END).unwrap(),
                dxgi::BC7_UNORM_SRGB,
                "the legs' albedo goes in as sRGB"
            );
            assert_eq!((albedo.format, albedo.mip_count), (102, 6));
            assert_eq!(
                built.textures[1].dds,
                flat_normal(),
                "the legs name no normal map"
            );
        }
        let refused = |extra: &[usize]| {
            body_tpf(&tpfs, &sets, extra, true, "FC_M_0000", "")
                .unwrap_err()
                .to_string()
        };
        assert!(refused(&[0]).contains("set 0"));
        assert!(refused(&[2]).contains("has 2"));
        assert!(refused(&[1, 1]).contains("already holds"));
        assert_eq!(set_prefix("FC_M_0000", 0), "FC_M_0000");
        assert_eq!(set_prefix("FC_M_0000", 3), "FC_M_0000_3");
    }

    #[test]
    fn each_texture_comes_out_of_the_tpf_of_the_character_holding_it() {
        // The same name in two characters' TPFs: only the owner's copy may be taken.
        let own = source_tpf(vec![
            texture("c2909", dx10_dds(1, 0xEE, dxgi::BC7_UNORM)),
            texture("c2900_WP_A_0400", legacy_dds(1, 5, b"DXT1")),
        ]);
        let lender = source_tpf(vec![
            texture("c2909", dx10_dds(2, 1, dxgi::BC7_UNORM)),
            texture("c2909_s", legacy_dds(3, 2, b"DXT1")),
            texture("c2909_n", dx10_dds(4, 3, dxgi::BC5_UNORM)),
        ]);
        let set = TextureSet::named_after("c2909");
        let tpfs = [("c2900", own.as_slice()), ("C2909", lender.as_slice())];
        let built =
            Tpf::parse(&character_tpf_from(&tpfs, &set, "FC_M_0000", "_l").unwrap()).unwrap();
        let shown: Vec<(&str, u8, u8)> = built
            .textures
            .iter()
            .map(|t| (t.name.as_str(), *t.dds.last().unwrap(), t.mip_count))
            .collect();
        assert_eq!(
            shown,
            [
                ("FC_M_0000_3m_l", 2, 3),
                ("FC_M_0000_a_l", 1, 2),
                ("FC_M_0000_n_l", 3, 4)
            ]
        );
        // The same set out of the one TPF is the same bytes.
        assert_eq!(
            character_tpf(&lender, &set, "FC_M_0000", "_l").unwrap(),
            character_tpf_from(&tpfs, &set, "FC_M_0000", "_l").unwrap()
        );
        let unread = character_tpf_from(&tpfs[..1], &set, "FC_M_0000", "")
            .unwrap_err()
            .to_string();
        assert!(unread.contains("no TPF of c2909 was read"), "{unread}");
        let mut absent = set.clone();
        absent.normal = Some(TextureRef::new("c2909", "c2909_x"));
        let error = character_tpf_from(&tpfs, &absent, "FC_M_0000", "")
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("c2909's TPF has no texture c2909_x"),
            "{error}"
        );
    }

    #[test]
    fn the_zero_metallic_map_is_a_full_bc4_chain_of_zero_blocks() {
        let dds = zero_metallic();
        assert_eq!(&dds[DDS_FOURCC_OFFSET..DDS_FOURCC_OFFSET + 4], b"ATI1");
        assert_eq!(crate::bytes::u32_le(&dds, 0x0C).unwrap(), 64, "height");
        assert_eq!(crate::bytes::u32_le(&dds, 0x10).unwrap(), 64, "width");
        assert_eq!(dds_mip_count(&dds).unwrap(), 7, "64, 32, 16, 8, 4, 2, 1");
        assert_eq!(tpf_format_byte(&dds).unwrap(), 103, "vanilla's BC4 byte");
        assert_eq!(
            dds.len(),
            DDS_HEADER_END + (256 + 64 + 16 + 4 + 1 + 1 + 1) * 8
        );
        assert!(dds[DDS_HEADER_END..].iter().all(|&b| b == 0));
    }

    #[test]
    fn the_metallic_map_joins_a_body_tpf_in_name_order_and_leaves_the_rest_alone() {
        let source = source_tpf(vec![
            texture("c2650", dx10_dds(3, 1, dxgi::BC7_UNORM)),
            texture("c2650_s", legacy_dds(2, 2, b"DXT1")),
            texture("c2650_n", dx10_dds(3, 3, dxgi::BC5_UNORM)),
        ]);
        let set = TextureSet::named_after("c2650");
        for lod in ["", "_l"] {
            let body = character_tpf(&source, &set, "FC_M_0000", lod).unwrap();
            let with = with_zero_metallic(&body, "FC_M_0000", lod).unwrap();
            let (before, after) = (Tpf::parse(&body).unwrap(), Tpf::parse(&with).unwrap());
            let names: Vec<&str> = after.textures.iter().map(|t| t.name.as_str()).collect();
            let expected = ["_3m", "_a", "_m", "_n"].map(|slot| format!("FC_M_0000{slot}{lod}"));
            assert_eq!(names, expected);
            let metallic = &after.textures[2];
            assert_eq!(metallic.dds, zero_metallic());
            assert_eq!((metallic.format, metallic.mip_count), (103, 7));
            for (old, new) in before
                .textures
                .iter()
                .zip([0, 1, 3].map(|i| &after.textures[i]))
            {
                assert_eq!(
                    (&old.name, old.format, old.mip_count, old.flags1, &old.dds),
                    (&new.name, new.format, new.mip_count, new.flags1, &new.dds)
                );
            }
            assert!(
                with_zero_metallic(&with, "FC_M_0000", lod)
                    .unwrap_err()
                    .to_string()
                    .contains("already holds")
            );
        }
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
