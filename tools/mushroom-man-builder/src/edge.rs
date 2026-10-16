//! The alpha-tested Edge material: Dark Souls Remastered's `*_Edge` meshes, which cut holes out
//! of themselves where the albedo's alpha is low, drawn with Elden Ring's generic `C[AMSN]_e`
//! material instead of the opaque Body one.
//!
//! No material `FC_M_0000` has can cut a mesh out by its albedo: Body is opaque, and the two
//! hair materials sample no albedo at all. So one donor material is retargeted
//! ([`HeaderAppend::retarget_material`]) and the Edge slot draws with it, while every opaque slot
//! stays on material 0, the `#03#` Body material the approved c2280 build uses:
//!
//! - **Which material.** Material 14, `#73#_Special`: only donor mesh 14 draws with it, so
//!   pointing it elsewhere changes no other mesh's material, and it has 13 texture entries,
//!   enough for `C[AMSN]_e`'s three. [`edge_material`] checks both on the donor it is given.
//! - **What it becomes.** The name `<id>_edge`, with no `#NN#` group tag, as vanilla armour
//!   materials have none; the `C[AMSN]_e` matxml, whose matbin alpha-tests at 50 and leaves
//!   every sampler's file to the FLVER; and the body's own textures, `FC_M_0000_a`, `_m` and
//!   `_n`, by path, the way vanilla `FC_M_0090.flver` names `FC_M_0000_a`.
//! - **Flags and GX list.** Those of a vanilla `C[AMSN]_e` material, `BD_F_1451.flver`'s
//!   material 0, read out of the installed game at build time ([`read_edge_template`]) rather
//!   than written down here.
//!
//! The metallic map the material samples is generated: see [`crate::textures::zero_metallic`].
//!
//! # Further texture sets
//!
//! The Body material can draw only the set in the donor's own slots, so a character with more
//! than one ([`crate::textures::TextureSets`]) draws every further set with a material of its
//! own, one per set and per opaque or cutout: one of the donor's materials 1 to 13
//! ([`SET_MATERIALS`]), which, like material 14, are `#NN#` Body materials of 13 texture entries
//! that only their own mesh draws with. A cutout one is retargeted after the Edge template; an
//! opaque one after `BD_M_1711.flver`'s material 8, `MetalAndLeather`, a vanilla `C[AMSN]`
//! material. Its matbin is `C[AMSN]_e`'s with the alpha test at 0 instead of 50 and nothing
//! else changed (`Chr_Base/matxml`, measured 2026-10-15), so it too takes every sampler's file
//! from the FLVER, and its meshes are laid out in the same 40-byte layout. Set `k`'s material
//! samples `FC_M_0000_<k>_a`, the shared zero metallic map and `FC_M_0000_<k>_n`
//! ([`set_textures`]).

use std::ops::RangeInclusive;

use crate::{
    archive::ArchiveSet,
    bnd::Bnd4,
    dcx,
    error::{Context, Error, Result},
    flver::{
        Flver, Texture,
        rebuild::{HeaderAppend, MaterialTemplate, material_template},
    },
    oodle::Oodle,
};

/// The donor material the Edge slot draws with.
pub const EDGE_MATERIAL: u32 = 14;
/// The donor mesh whose buffer layout an Edge slot's vertices are written in: mesh 15, the
/// first hair mesh, whose 40-byte layout (position, normal, one tangent, bone indices and
/// weights, colour, one packed UV) is the one vanilla `C[AMSN]_e` meshes use, `BD_F_1451`'s
/// mesh 0 among them. The body meshes' 52-byte layout, used for Edge slots until 2026-10-15,
/// adds a second tangent and two more UVs, and those slots drew pitch black under ray tracing
/// while the Body-material slots beside them did not. Vamos (c2920) drew right under ray tracing
/// once his cloak took this layout, seen in game 2026-10-15.
pub const EDGE_LAYOUT_DONOR_MESH: usize = 15;
pub const EDGE_MTD: &str = r"N:\GR\data\Material\mtd\Chr_Base\matxml\C[AMSN]_e.matxml";
/// Where the template material lives: a vanilla body part whose material 0 is `C[AMSN]_e`.
pub const TEMPLATE_PART: &str = "/parts/bd_f_1451.partsbnd.dcx";
pub const TEMPLATE_FLVER: &str = "BD_F_1451.flver";
pub const TEMPLATE_MATERIAL: usize = 0;
/// The donor materials a further texture set's slots draw with, in the order they are taken.
pub const SET_MATERIALS: RangeInclusive<u32> = 1..=13;
pub const OPAQUE_MTD: &str = r"N:\GR\data\Material\mtd\Chr_Base\matxml\C[AMSN].matxml";
/// Where the opaque template lives: a vanilla body part whose material 8 is `C[AMSN]`.
pub const OPAQUE_TEMPLATE_PART: &str = "/parts/bd_m_1711.partsbnd.dcx";
pub const OPAQUE_TEMPLATE_FLVER: &str = "BD_M_1711.flver";
pub const OPAQUE_TEMPLATE_MATERIAL: usize = 8;

/// One donor material pointed at a `C[AMSN]` matxml for a texture set: the Edge material is
/// [`Retarget::EDGE`], set 0 cut out on material 14.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Retarget {
    pub material: u32,
    pub set: usize,
    /// Alpha-tested, after the Edge template; otherwise opaque, after the `C[AMSN]` one.
    pub cutout: bool,
}

impl Retarget {
    pub const EDGE: Self = Self {
        material: EDGE_MATERIAL,
        set: 0,
        cutout: true,
    };

    /// The material's name for character `id`: `<id>_edge` for the Edge material, `<id>_<set>`
    /// or `<id>_<set>_edge` for a further set.
    pub fn name(&self, id: &str) -> String {
        match (self.set, self.cutout) {
            (0, true) => edge_material_name(id),
            (0, false) => id.to_owned(),
            (set, true) => format!("{id}_{set}_edge"),
            (set, false) => format!("{id}_{set}"),
        }
    }
}

/// The two vanilla materials a retarget is made after.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Templates {
    pub cutout: MaterialTemplate,
    pub opaque: MaterialTemplate,
}

impl Templates {
    fn for_retarget(&self, retarget: &Retarget) -> &MaterialTemplate {
        if retarget.cutout {
            &self.cutout
        } else {
            &self.opaque
        }
    }
}
/// `C[AMSN]_e`'s samplers in the order its vanilla materials list them, and the body texture
/// each is given.
const SAMPLERS: [(&str, &str); 3] = [
    ("C_AMSN__snp_Texture2D_2_AlbedoMap_0", "_a"),
    ("C_AMSN__snp_Texture2D_0_MetallicMap_0", METALLIC_SUFFIX),
    ("C_AMSN__snp_Texture2D_7_NormalMap_4", "_n"),
];
const METALLIC_SUFFIX: &str = "_m";
/// The body textures' names in the TPF, and the directory vanilla gives their paths.
const TEXTURE_PREFIX: &str = "FC_M_0000";
const TEXTURE_DIR: &str = r"N:\GR\data\Model\parts\Face\FC_M_0000\tex";

/// The Edge material's name for character `id`.
pub fn edge_material_name(id: &str) -> String {
    format!("{id}_edge")
}

/// The three texture entries the Edge material carries, in [`SAMPLERS`] order.
pub fn edge_textures() -> Vec<Texture> {
    set_textures(0)
}

/// The three texture entries a material drawing texture set `set` carries, in [`SAMPLERS`]
/// order: the set's albedo and normal map under [`crate::textures::set_prefix`], and the one zero
/// metallic map every set shares.
pub fn set_textures(set: usize) -> Vec<Texture> {
    let prefix = crate::textures::set_prefix(TEXTURE_PREFIX, set);
    SAMPLERS
        .iter()
        .map(|(sampler, suffix)| {
            let owner = if *suffix == METALLIC_SUFFIX {
                TEXTURE_PREFIX
            } else {
                &prefix
            };
            Texture {
                path: format!(r"{TEXTURE_DIR}\{owner}{suffix}.tif"),
                sampler: (*sampler).to_owned(),
            }
        })
        .collect()
}

/// `BD_F_1451.flver`'s material 0 out of the installed game's archives.
pub fn read_edge_template(archives: &ArchiveSet, oodle: &Oodle) -> Result<MaterialTemplate> {
    edge_template(&read_part_flver(
        archives,
        oodle,
        TEMPLATE_PART,
        TEMPLATE_FLVER,
    )?)
}

/// Both templates out of the installed game's archives.
pub fn read_templates(archives: &ArchiveSet, oodle: &Oodle) -> Result<Templates> {
    Ok(Templates {
        cutout: read_edge_template(archives, oodle)?,
        opaque: opaque_template(&read_part_flver(
            archives,
            oodle,
            OPAQUE_TEMPLATE_PART,
            OPAQUE_TEMPLATE_FLVER,
        )?)?,
    })
}

fn read_part_flver(
    archives: &ArchiveSet,
    oodle: &Oodle,
    part: &str,
    flver_name: &str,
) -> Result<Vec<u8>> {
    let packed = archives.read(part)?;
    let binder = Bnd4::read(&dcx::decompress(&packed, Some(oodle))?).context(part)?;
    binder
        .file_named(flver_name)
        .map(|file| file.data.clone())
        .ok_or_else(|| Error::new(format!("{part} has no {flver_name}")))
}

/// The template material out of `BD_F_1451.flver`'s bytes, checked to be the `C[AMSN]_e` one
/// with the samplers this module fills.
pub fn edge_template(template_flver: &[u8]) -> Result<MaterialTemplate> {
    let context = format!("{TEMPLATE_FLVER} material {TEMPLATE_MATERIAL}");
    let template = material_template(template_flver, TEMPLATE_MATERIAL).context(context.clone())?;
    check_template(&template, EDGE_MTD, &context)?;
    Ok(template)
}

/// The opaque template out of `BD_M_1711.flver`'s bytes, checked to be the `C[AMSN]` one with
/// the samplers this module fills.
pub fn opaque_template(template_flver: &[u8]) -> Result<MaterialTemplate> {
    let context = format!("{OPAQUE_TEMPLATE_FLVER} material {OPAQUE_TEMPLATE_MATERIAL}");
    let template =
        material_template(template_flver, OPAQUE_TEMPLATE_MATERIAL).context(context.clone())?;
    check_template(&template, OPAQUE_MTD, &context)?;
    Ok(template)
}

fn check_template(template: &MaterialTemplate, mtd: &str, context: &str) -> Result<()> {
    if !template.mtd.eq_ignore_ascii_case(mtd) {
        return Err(Error::new(format!(
            "{context} is drawn with {}, not {mtd}",
            template.mtd
        )));
    }
    let same = template.samplers.len() == SAMPLERS.len()
        && template
            .samplers
            .iter()
            .zip(SAMPLERS)
            .all(|(found, (wanted, _))| found.eq_ignore_ascii_case(wanted));
    if !same {
        return Err(Error::new(format!(
            "{context} samples {:?}, not the three C[AMSN] samplers this builder fills",
            template.samplers
        )));
    }
    Ok(())
}

/// What `donor`'s header region gains so a slot drawing with [`EDGE_MATERIAL`] is cut out by
/// its albedo: material 14 retargeted onto `C[AMSN]_e` after `template`, named for character
/// `id`. Pass the result to [`crate::flver::rebuild::rebuild`] with that slot.
pub fn edge_material(donor: &[u8], template: &MaterialTemplate, id: &str) -> Result<HeaderAppend> {
    let mut append = HeaderAppend::for_donor(donor)?;
    retarget_onto(
        &mut append,
        &Flver::parse(donor)?,
        template,
        &Retarget::EDGE,
        id,
    )?;
    Ok(append)
}

/// What `donor`'s header region gains for every material of `retargets`, each after its
/// template of `templates` and named for character `id`. With only [`Retarget::EDGE`] it is
/// [`edge_material`]'s append.
pub fn retarget_materials(
    donor: &[u8],
    templates: &Templates,
    id: &str,
    retargets: &[Retarget],
) -> Result<HeaderAppend> {
    let flver = Flver::parse(donor)?;
    let mut append = HeaderAppend::for_donor(donor)?;
    for retarget in retargets {
        retarget_onto(
            &mut append,
            &flver,
            templates.for_retarget(retarget),
            retarget,
            id,
        )?;
    }
    Ok(append)
}

/// [`retarget_materials`] with its templates read out of the installed game: the one call a
/// build with a retargeted slot makes.
pub fn retarget_append(
    archives: &ArchiveSet,
    oodle: &Oodle,
    donor: &[u8],
    id: &str,
    retargets: &[Retarget],
) -> Result<HeaderAppend> {
    retarget_materials(donor, &read_templates(archives, oodle)?, id, retargets)
}

/// Retarget `retarget.material` of `donor` in `append`. Material `n` must be drawn by donor mesh
/// `n` alone, as every one of the donor's materials is, so pointing it elsewhere changes no other
/// mesh's material.
fn retarget_onto(
    append: &mut HeaderAppend,
    donor: &Flver,
    template: &MaterialTemplate,
    retarget: &Retarget,
    id: &str,
) -> Result<()> {
    let (mtd, context) = if retarget.cutout {
        (
            EDGE_MTD,
            format!("{TEMPLATE_FLVER} material {TEMPLATE_MATERIAL}"),
        )
    } else {
        (
            OPAQUE_MTD,
            format!("{OPAQUE_TEMPLATE_FLVER} material {OPAQUE_TEMPLATE_MATERIAL}"),
        )
    };
    check_template(template, mtd, &context)?;
    if retarget.material != EDGE_MATERIAL && !SET_MATERIALS.contains(&retarget.material) {
        return Err(Error::new(format!(
            "material {} is neither the Edge material {EDGE_MATERIAL} nor one of the set \
             materials {SET_MATERIALS:?}",
            retarget.material
        )));
    }
    let material = retarget.material as usize;
    let owners: Vec<usize> = donor
        .meshes
        .iter()
        .enumerate()
        .filter(|(_, mesh)| mesh.material_index as usize == material)
        .map(|(index, _)| index)
        .collect();
    if owners != [material] {
        return Err(Error::new(format!(
            "the donor's material {material} is drawn by meshes {owners:?}, not by mesh \
             {material} alone"
        )));
    }
    // Retargeting refuses a material with fewer texture entries than the three it writes.
    append.retarget_material(
        material,
        &retarget.name(id),
        mtd,
        &set_textures(retarget.set),
        &template.gx,
        template.flags,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn template() -> MaterialTemplate {
        MaterialTemplate {
            mtd: EDGE_MTD.to_lowercase(),
            flags: 0,
            gx: Vec::new(),
            samplers: SAMPLERS
                .iter()
                .map(|(name, _)| (*name).to_owned())
                .collect(),
        }
    }

    #[test]
    fn the_edge_textures_name_the_body_set_by_path() {
        let textures = edge_textures();
        let paths: Vec<&str> = textures.iter().map(|t| t.path.as_str()).collect();
        assert_eq!(
            paths,
            [
                r"N:\GR\data\Model\parts\Face\FC_M_0000\tex\FC_M_0000_a.tif",
                r"N:\GR\data\Model\parts\Face\FC_M_0000\tex\FC_M_0000_m.tif",
                r"N:\GR\data\Model\parts\Face\FC_M_0000\tex\FC_M_0000_n.tif",
            ]
        );
        assert_eq!(textures[1].sampler, "C_AMSN__snp_Texture2D_0_MetallicMap_0");
        assert_eq!(edge_material_name("c2650"), "c2650_edge");
        assert_eq!(Retarget::EDGE.name("c2650"), "c2650_edge");
    }

    #[test]
    fn a_further_set_names_its_own_albedo_and_normal_and_the_shared_metallic_map() {
        let paths: Vec<String> = set_textures(2).into_iter().map(|t| t.path).collect();
        assert_eq!(
            paths,
            [
                r"N:\GR\data\Model\parts\Face\FC_M_0000\tex\FC_M_0000_2_a.tif",
                r"N:\GR\data\Model\parts\Face\FC_M_0000\tex\FC_M_0000_m.tif",
                r"N:\GR\data\Model\parts\Face\FC_M_0000\tex\FC_M_0000_2_n.tif",
            ]
        );
        let set = |set, cutout| Retarget {
            material: 1,
            set,
            cutout,
        };
        assert_eq!(set(2, false).name("c2360"), "c2360_2");
        assert_eq!(set(2, true).name("c2360"), "c2360_2_edge");
    }

    #[test]
    fn a_template_that_is_not_c_amsn_e_is_refused() {
        let check = |template: &MaterialTemplate| check_template(template, EDGE_MTD, "test");
        check(&template()).unwrap();
        let mut body = template();
        body.mtd = r"N:\GR\data\Material\mtd\Parts\matxml\P[FC_M_0000]_Body.matxml".to_owned();
        assert!(check(&body).is_err());
        let mut reordered = template();
        reordered.samplers.swap(0, 2);
        assert!(check(&reordered).is_err());
        let mut short = template();
        short.samplers.pop();
        assert!(check(&short).is_err());
        // The opaque matxml is a different one.
        assert!(check_template(&template(), OPAQUE_MTD, "test").is_err());
    }
}
