//! The Mushroom Man ME3 package: every file it holds, built from the two games' own files.
//!
//! | output | built from |
//! |---|---|
//! | `parts/fc_*.partsbnd.dcx` (29) | Elden Ring's `fc_m_0000` (and `_l`) with c2280 patched into mesh 13 and its textures, renamed per part |
//! | `parts/fg_a_0000_m.partsbnd.dcx`, `_f` | Elden Ring's `fg_a_0000_m` with every face hidden |
//! | `parts/{hd,bd,am,lg}_{m,f}_0000{,_l}.partsbnd.dcx` (16) | each of those vanilla binders with every face hidden |
//! | `facegen/facegen.fgbnd.dcx` | Elden Ring's facegen binder with `face.flver` hidden |
//!
//! Why each exists. Character presets ask for many `fc_*` bodies, and a binder answers only to
//! its own name -- inside and out -- so the patched body is copied under every name with its
//! entry paths renamed to match. `mushroom_man.dll` zeroes every armour piece's model id, which
//! sends the game to the default naked head/body/arm/leg binders; those are emptied so nothing
//! human is drawn over the mushroom. The face-part and facegen binders would otherwise draw the
//! character's generated face; they are emptied too.
//!
//! This is the output of `scripts/route_a_mushroom_build_assets.sh`,
//! `route_a_mushroom_build_hidden_naked_slots.sh` and `route_a_mushroom_stage_all_model_variants.py`
//! with two deliberate differences. The female naked slots are the game's own female binders,
//! where the scripts renamed copies of the male ones (their rename looked for `parts\Head\`; the
//! binders say `parts\FullBody\`, so it never matched). And the unused `bd_m_1010` armour body
//! from the first prototype is not written: the DLL zeroes every armour model, so it is never
//! loaded.

use std::{
    fs,
    path::{Path, PathBuf},
};

use crate::{
    archive::ArchiveSet,
    bnd::{BinderFile, Bnd4, file_name, read_bnd3},
    dcx,
    error::{Context, Error, Result},
    flver::{
        export::{RouteAParams, SourceMesh, export_c2280},
        hide::hide_all_face_sets,
        patch::{PatchReport, patch_donor},
    },
    oodle::Oodle,
    textures::mushroom_tpf,
};

/// Every full-body part a character preset can ask for, from the community file list of Elden
/// Ring's archives (`/parts/fc_*.partsbnd.dcx`).
pub const FC_PART_NAMES: [&str; 29] = [
    "fc_f_0000.partsbnd.dcx",
    "fc_f_0000_l.partsbnd.dcx",
    "fc_f_0001.partsbnd.dcx",
    "fc_f_0001_l.partsbnd.dcx",
    "fc_f_0002.partsbnd.dcx",
    "fc_f_0002_l.partsbnd.dcx",
    "fc_f_0100.partsbnd.dcx",
    "fc_f_0100_l.partsbnd.dcx",
    "fc_f_0101.partsbnd.dcx",
    "fc_f_0101_l.partsbnd.dcx",
    "fc_f_0102.partsbnd.dcx",
    "fc_f_0102_l.partsbnd.dcx",
    "fc_m_0000.partsbnd.dcx",
    "fc_m_0000_l.partsbnd.dcx",
    "fc_m_0000_m.partsbnd.dcx",
    "fc_m_0001.partsbnd.dcx",
    "fc_m_0001_l.partsbnd.dcx",
    "fc_m_0002.partsbnd.dcx",
    "fc_m_0002_l.partsbnd.dcx",
    "fc_m_0090.partsbnd.dcx",
    "fc_m_0090_l.partsbnd.dcx",
    "fc_m_0100.partsbnd.dcx",
    "fc_m_0100_l.partsbnd.dcx",
    "fc_m_0101.partsbnd.dcx",
    "fc_m_0101_l.partsbnd.dcx",
    "fc_m_0102.partsbnd.dcx",
    "fc_m_0102_l.partsbnd.dcx",
    "fc_m_0105.partsbnd.dcx",
    "fc_m_0105_l.partsbnd.dcx",
];

const PART_EXTENSION: &str = ".partsbnd.dcx";
const FC_SOURCE_STEM: &str = "FC_M_0000";
/// The mesh of `FC_M_0000.flver` the mushroom is written into: the largest body mesh, and the
/// one the Route A prototype and its Blender successor both targeted.
pub const FC_DONOR_MESH_INDEX: usize = 13;
const FG_SOURCE: &str = "fg_a_0000_m.partsbnd.dcx";
/// Requested by female characters but absent from the archives, so it is written as a renamed
/// copy of the male one, as the scripts did.
const FG_FEMALE_ALIAS: &str = "fg_a_0000_f.partsbnd.dcx";
const NAKED_SLOTS: [&str; 4] = ["hd", "bd", "am", "lg"];
const GENDERS: [&str; 2] = ["m", "f"];
const LODS: [&str; 2] = ["", "_l"];
const FACEGEN: &str = "/facegen/facegen.fgbnd.dcx";
const DSR_CHRBND: &str = "chr/c2280.chrbnd.dcx";
pub const DLL_NAME: &str = "mushroom_man.dll";
/// The files `mushroom_man.dll` looks for beside itself before it does anything.
pub const DLL_PAYLOAD_PROBES: [&str; 2] =
    ["parts/fc_m_0000.partsbnd.dcx", "facegen/facegen.fgbnd.dcx"];

/// Where the inputs come from.
pub struct Inputs<'a> {
    pub archives: &'a ArchiveSet,
    pub oodle: &'a Oodle,
    pub dsr_dir: &'a Path,
    /// An authored mesh (OBJ + weight TSV) to use instead of exporting c2280 with Route A.
    pub source_override: Option<SourceMesh>,
}

/// What a build wrote, for the summary line and the tests.
#[derive(Debug)]
pub struct BuildReport {
    pub files: Vec<PathBuf>,
    pub patch_high: PatchReport,
    pub patch_low: PatchReport,
    pub source_vertices: usize,
    pub source_triangles: usize,
}

/// c2280's model and textures out of a Dark Souls Remastered install.
pub fn read_c2280(dsr_dir: &Path) -> Result<(Vec<u8>, Vec<u8>)> {
    let path = dsr_dir.join(DSR_CHRBND);
    let packed = fs::read(&path).context(format!(
        "reading {} -- is this the Dark Souls Remastered folder?",
        path.display()
    ))?;
    let files = read_bnd3(&dcx::decompress(&packed, None)?).context(path.display())?;
    let find = |name: &str| -> Result<Vec<u8>> {
        files
            .iter()
            .find(|file| file_name(&file.name).eq_ignore_ascii_case(name))
            .map(|file| file.data.clone())
            .ok_or_else(|| Error::new(format!("{} has no {name}", path.display())))
    };
    Ok((find("c2280.flver")?, find("c2280.tpf")?))
}

/// Build the whole package into `out_dir` (`parts/` and `facegen/` beneath it).
pub fn build(inputs: Inputs<'_>, out_dir: &Path) -> Result<BuildReport> {
    let (c2280_flver, c2280_tpf) = read_c2280(inputs.dsr_dir)?;
    let source = match inputs.source_override {
        Some(mesh) => mesh,
        None => export_c2280(&c2280_flver, RouteAParams::default()).context("exporting c2280")?,
    };
    let parts_dir = out_dir.join("parts");
    let facegen_dir = out_dir.join("facegen");
    fs::create_dir_all(&parts_dir).context(format!("creating {}", parts_dir.display()))?;
    fs::create_dir_all(&facegen_dir).context(format!("creating {}", facegen_dir.display()))?;
    let mut files = Vec::new();
    let oodle = Some(inputs.oodle);

    // The mushroom body, high and low detail.
    let mut bodies = Vec::new();
    let mut patches = Vec::new();
    for lod in LODS {
        let stem = format!("{FC_SOURCE_STEM}{}", lod.to_uppercase());
        let logical = format!("/parts/{}{PART_EXTENSION}", stem.to_lowercase());
        let packed = inputs.archives.read(&logical)?;
        let mut binder = Bnd4::read(&dcx::decompress(&packed, oodle)?).context(&logical)?;
        let flver = binder_file(&mut binder, &format!("{stem}.flver"), &logical)?;
        patches.push(
            patch_donor(&mut flver.data, &source, FC_DONOR_MESH_INDEX)
                .context(format!("patching {stem}.flver"))?,
        );
        let tpf = binder_file(&mut binder, &format!("{stem}.tpf"), &logical)?;
        tpf.data = mushroom_tpf(&c2280_tpf, FC_SOURCE_STEM, lod)?;
        bodies.push((binder, packed));
    }
    for name in FC_PART_NAMES {
        let target = name
            .strip_suffix(PART_EXTENSION)
            .ok_or("FC part name without its extension")?
            .to_uppercase();
        let low = target.ends_with("_L");
        let (body, packed) = &bodies[usize::from(low)];
        let source_stem = if low {
            format!("{FC_SOURCE_STEM}_L")
        } else {
            FC_SOURCE_STEM.to_owned()
        };
        let renamed = renamed(
            body,
            &[
                (&source_stem, &target),
                (FC_SOURCE_STEM, root_stem(&target)),
            ],
        );
        files.push(write_part(&parts_dir.join(name), packed, &renamed, oodle)?);
    }

    // Face parts: the male one emptied, and the female alias the archives do not carry.
    let fg_logical = format!("/parts/{FG_SOURCE}");
    let fg_packed = inputs.archives.read(&fg_logical)?;
    let mut fg = Bnd4::read(&dcx::decompress(&fg_packed, oodle)?).context(&fg_logical)?;
    hide_every_flver(&mut fg).context(&fg_logical)?;
    files.push(write_part(
        &parts_dir.join(FG_SOURCE),
        &fg_packed,
        &fg,
        oodle,
    )?);
    let fg_female = renamed(&fg, &[("FG_A_0000_M", "FG_A_0000_F")]);
    files.push(write_part(
        &parts_dir.join(FG_FEMALE_ALIAS),
        &fg_packed,
        &fg_female,
        oodle,
    )?);

    // The default naked slots the DLL's zeroed armour falls back to, emptied.
    for slot in NAKED_SLOTS {
        for gender in GENDERS {
            for lod in LODS {
                let name = format!("{slot}_{gender}_0000{lod}{PART_EXTENSION}");
                let logical = format!("/parts/{name}");
                let packed = inputs.archives.read(&logical)?;
                let mut binder = Bnd4::read(&dcx::decompress(&packed, oodle)?).context(&logical)?;
                hide_every_flver(&mut binder).context(&logical)?;
                files.push(write_part(&parts_dir.join(&name), &packed, &binder, oodle)?);
            }
        }
    }

    // The generated face.
    let facegen_packed = inputs.archives.read(FACEGEN)?;
    let mut facegen = Bnd4::read(&dcx::decompress(&facegen_packed, oodle)?).context(FACEGEN)?;
    let face = binder_file(&mut facegen, "face.flver", FACEGEN)?;
    hide_all_face_sets(&mut face.data).context("hiding face.flver")?;
    files.push(write_part(
        &facegen_dir.join("facegen.fgbnd.dcx"),
        &facegen_packed,
        &facegen,
        oodle,
    )?);

    Ok(BuildReport {
        files,
        patch_high: patches[0],
        patch_low: patches[1],
        source_vertices: source.vertices.len(),
        source_triangles: source.triangles.len(),
    })
}

fn binder_file<'a>(binder: &'a mut Bnd4, name: &str, context: &str) -> Result<&'a mut BinderFile> {
    binder
        .file_named_mut(name)
        .ok_or_else(|| Error::new(format!("{context} has no {name}")))
}

fn hide_every_flver(binder: &mut Bnd4) -> Result<()> {
    let mut hid_any = false;
    for file in &mut binder.files {
        if file_name(&file.name).to_lowercase().ends_with(".flver") {
            hide_all_face_sets(&mut file.data).context(file_name(&file.name).to_owned())?;
            hid_any = true;
        }
    }
    if hid_any {
        Ok(())
    } else {
        Err(Error::new("binder holds no FLVER to hide"))
    }
}

/// A copy of `binder` with each `(from, to)` replacement applied in order to every entry path,
/// as the scripts applied them to WitchyBND's manifest.
fn renamed(binder: &Bnd4, replacements: &[(&str, &str)]) -> Bnd4 {
    let mut copy = binder.clone();
    for file in &mut copy.files {
        for (from, to) in replacements {
            file.name = file.name.replace(from, to);
        }
    }
    copy
}

/// `FC_F_0102_L` -> `FC_F_0102`: the directory a low/mid-detail part's files live under.
fn root_stem(upper_stem: &str) -> &str {
    ["_L", "_M", "_F"]
        .iter()
        .find_map(|suffix| upper_stem.strip_suffix(suffix))
        .unwrap_or(upper_stem)
}

fn write_part(
    path: &Path,
    source_packed: &[u8],
    binder: &Bnd4,
    oodle: Option<&Oodle>,
) -> Result<PathBuf> {
    let packed = dcx::recompress_like(source_packed, &binder.write()?, oodle)
        .context(format!("packing {}", path.display()))?;
    fs::write(path, packed).context(format!("writing {}", path.display()))?;
    Ok(path.to_path_buf())
}

/// Whether `dir` already holds what `mushroom_man.dll` needs beside it to switch on.
pub fn payload_present(dir: &Path) -> bool {
    DLL_PAYLOAD_PROBES
        .iter()
        .all(|probe| dir.join(probe).is_file())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn binder(names: &[&str]) -> Bnd4 {
        Bnd4 {
            unk04: 0,
            unk05: 0,
            bit_big_endian_byte: 1,
            version: *b"07D7R6\0\0",
            unicode: true,
            raw_format: 0x74,
            extended: 4,
            files: names
                .iter()
                .map(|name| BinderFile {
                    flags: 0x40,
                    id: 0,
                    name: (*name).to_owned(),
                    data: Vec::new(),
                })
                .collect(),
        }
    }

    #[test]
    fn low_detail_aliases_rename_the_file_and_the_directory() {
        let source =
            binder(&[r"N:\GR\data\INTERROOT_win64\parts\Face\FC_M_0000\FC_M_0000_L.flver"]);
        let target = "FC_F_0102_L";
        let alias = renamed(
            &source,
            &[("FC_M_0000_L", target), (FC_SOURCE_STEM, root_stem(target))],
        );
        assert_eq!(
            alias.files[0].name,
            r"N:\GR\data\INTERROOT_win64\parts\Face\FC_F_0102\FC_F_0102_L.flver"
        );
    }

    #[test]
    fn the_middle_detail_male_body_keeps_its_root_directory_name() {
        let source = binder(&[r"N:\GR\data\INTERROOT_win64\parts\Face\FC_M_0000\FC_M_0000.tpf"]);
        let target = "FC_M_0000_M";
        let alias = renamed(
            &source,
            &[
                (FC_SOURCE_STEM, target),
                (FC_SOURCE_STEM, root_stem(target)),
            ],
        );
        assert_eq!(
            alias.files[0].name,
            r"N:\GR\data\INTERROOT_win64\parts\Face\FC_M_0000_M\FC_M_0000_M.tpf"
        );
    }

    #[test]
    fn every_fc_name_is_a_partsbnd_and_none_repeats() {
        let mut names = FC_PART_NAMES.to_vec();
        assert!(names.iter().all(|name| name.ends_with(PART_EXTENSION)));
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), FC_PART_NAMES.len());
    }

    #[test]
    fn root_stem_drops_one_detail_suffix() {
        assert_eq!(root_stem("FC_F_0102_L"), "FC_F_0102");
        assert_eq!(root_stem("FC_M_0000"), "FC_M_0000");
        assert_eq!(root_stem("FG_A_0000_F"), "FG_A_0000");
    }
}
