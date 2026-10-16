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
/// The donor mesh that, alone, draws with [`EDGE_MATERIAL`].
const EDGE_MATERIAL_OWNER: usize = 14;
pub const EDGE_MTD: &str = r"N:\GR\data\Material\mtd\Chr_Base\matxml\C[AMSN]_e.matxml";
/// Where the template material lives: a vanilla body part whose material 0 is `C[AMSN]_e`.
pub const TEMPLATE_PART: &str = "/parts/bd_f_1451.partsbnd.dcx";
pub const TEMPLATE_FLVER: &str = "BD_F_1451.flver";
pub const TEMPLATE_MATERIAL: usize = 0;
/// `C[AMSN]_e`'s samplers in the order its vanilla materials list them, and the body texture
/// each is given.
const SAMPLERS: [(&str, &str); 3] = [
    ("C_AMSN__snp_Texture2D_2_AlbedoMap_0", "_a"),
    ("C_AMSN__snp_Texture2D_0_MetallicMap_0", "_m"),
    ("C_AMSN__snp_Texture2D_7_NormalMap_4", "_n"),
];
/// The body textures' names in the TPF, and the directory vanilla gives their paths.
const TEXTURE_PREFIX: &str = "FC_M_0000";
const TEXTURE_DIR: &str = r"N:\GR\data\Model\parts\Face\FC_M_0000\tex";

/// The Edge material's name for character `id`.
pub fn edge_material_name(id: &str) -> String {
    format!("{id}_edge")
}

/// The three texture entries the Edge material carries, in [`SAMPLERS`] order.
pub fn edge_textures() -> Vec<Texture> {
    SAMPLERS
        .iter()
        .map(|(sampler, suffix)| Texture {
            path: format!(r"{TEXTURE_DIR}\{TEXTURE_PREFIX}{suffix}.tif"),
            sampler: (*sampler).to_owned(),
        })
        .collect()
}

/// `BD_F_1451.flver`'s material 0 out of the installed game's archives.
pub fn read_edge_template(archives: &ArchiveSet, oodle: &Oodle) -> Result<MaterialTemplate> {
    let packed = archives.read(TEMPLATE_PART)?;
    let binder = Bnd4::read(&dcx::decompress(&packed, Some(oodle))?).context(TEMPLATE_PART)?;
    let flver = binder
        .file_named(TEMPLATE_FLVER)
        .ok_or_else(|| Error::new(format!("{TEMPLATE_PART} has no {TEMPLATE_FLVER}")))?;
    edge_template(&flver.data)
}

/// The template material out of `BD_F_1451.flver`'s bytes, checked to be the `C[AMSN]_e` one
/// with the samplers this module fills.
pub fn edge_template(template_flver: &[u8]) -> Result<MaterialTemplate> {
    let template = material_template(template_flver, TEMPLATE_MATERIAL)
        .context(format!("{TEMPLATE_FLVER} material {TEMPLATE_MATERIAL}"))?;
    check_template(&template)?;
    Ok(template)
}

fn check_template(template: &MaterialTemplate) -> Result<()> {
    let context = format!("{TEMPLATE_FLVER} material {TEMPLATE_MATERIAL}");
    if !template.mtd.eq_ignore_ascii_case(EDGE_MTD) {
        return Err(Error::new(format!(
            "{context} is drawn with {}, not {EDGE_MTD}",
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
            "{context} samples {:?}, not the three C[AMSN]_e samplers this builder fills",
            template.samplers
        )));
    }
    Ok(())
}

/// What `donor`'s header region gains so a slot drawing with [`EDGE_MATERIAL`] is cut out by
/// its albedo: material 14 retargeted onto `C[AMSN]_e` after `template`, named for character
/// `id`. Pass the result to [`crate::flver::rebuild::rebuild`] with that slot.
pub fn edge_material(donor: &[u8], template: &MaterialTemplate, id: &str) -> Result<HeaderAppend> {
    check_template(template)?;
    let flver = Flver::parse(donor)?;
    let material = EDGE_MATERIAL as usize;
    let owners: Vec<usize> = flver
        .meshes
        .iter()
        .enumerate()
        .filter(|(_, mesh)| mesh.material_index as usize == material)
        .map(|(index, _)| index)
        .collect();
    if owners != [EDGE_MATERIAL_OWNER] {
        return Err(Error::new(format!(
            "the donor's material {material} is drawn by meshes {owners:?}, not by mesh \
             {EDGE_MATERIAL_OWNER} alone"
        )));
    }
    // Retargeting refuses a material with fewer texture entries than the three it writes.
    let mut append = HeaderAppend::for_donor(donor)?;
    append.retarget_material(
        material,
        &edge_material_name(id),
        EDGE_MTD,
        &edge_textures(),
        &template.gx,
        template.flags,
    )?;
    Ok(append)
}

/// [`edge_material`] with its template read out of the installed game: the one call a build
/// with an Edge slot makes.
pub fn edge_append(
    archives: &ArchiveSet,
    oodle: &Oodle,
    donor: &[u8],
    id: &str,
) -> Result<HeaderAppend> {
    edge_material(donor, &read_edge_template(archives, oodle)?, id)
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
    }

    #[test]
    fn a_template_that_is_not_c_amsn_e_is_refused() {
        check_template(&template()).unwrap();
        let mut body = template();
        body.mtd = r"N:\GR\data\Material\mtd\Parts\matxml\P[FC_M_0000]_Body.matxml".to_owned();
        assert!(check_template(&body).is_err());
        let mut reordered = template();
        reordered.samplers.swap(0, 2);
        assert!(check_template(&reordered).is_err());
        let mut short = template();
        short.samplers.pop();
        assert!(check_template(&short).is_err());
    }
}
