//! The Mushroom Man ME3 package: every file it holds, built from the two games' own files.
//!
//! | output | built from |
//! |---|---|
//! | `character.txt` | the id of the DSR character the package was built from, one line |
//! | `parts/fc_*.partsbnd.dcx` (31) | Elden Ring's `fc_m_0000` (and `_l`) with the character written into it and its textures, renamed per part |
//! | `parts/fg_a_*.partsbnd.dcx`, `parts/hr_a_*.partsbnd.dcx` | every face part and hair part the archives hold a FLVER for, with every face hidden |
//! | `parts/fg_a_0000_f.partsbnd.dcx` | the hidden `fg_a_0000_m` under the female name the archives do not carry |
//! | `parts/{hd,bd,am,lg}_{m,f}_0000{,_l}.partsbnd.dcx` (16) | each of those vanilla binders with every face hidden |
//! | `facegen/facegen.fgbnd.dcx` | Elden Ring's facegen binder with `face.flver` hidden |
//!
//! How the character goes into `FC_M_0000.flver` depends on its writer. c2280 is patched into
//! mesh 13 in place, as the user approved it. A Biped character is fitted, its held items cut,
//! and its meshes grouped by DSR material and back-face culling; each group becomes one slot of
//! the rebuild writer on a donor body mesh laid out as mesh 13 is, drawn with the Body material,
//! or with the Edge material when its MTD is alpha-tested ([`crate::edge`]), and every other mesh
//! is hidden. The low-detail binder carries the same FLVER.
//!
//! Why each exists. Character presets ask for many `fc_*` bodies, and a binder answers only to
//! its own name -- inside and out -- so the patched body is copied under every name with its
//! entry paths renamed to match. `mushroom_man.dll` zeroes every armour piece's model id, which
//! sends the game to the default naked head/body/arm/leg binders; those are emptied so nothing
//! human is drawn over the mushroom. The head is not part of any of those: a character's face
//! (`fg_a_01xx`), eyes (`fg_a_15xx`), brows, beard, lashes and eyepatch (`fg_a_2xxx`-`7xxx`)
//! and hair (`hr_a_*`) are separate parts chosen by its face data, so every one of them is
//! emptied too, along with the generated face in the facegen binder.
//!
//! This is the output of `scripts/route_a_mushroom_build_assets.sh`,
//! `route_a_mushroom_build_hidden_naked_slots.sh` and `route_a_mushroom_stage_all_model_variants.py`
//! with two deliberate differences. The female naked slots are the game's own female binders,
//! where the scripts renamed copies of the male ones (their rename looked for `parts\Head\`; the
//! binders say `parts\FullBody\`, so it never matched). And the unused `bd_m_1010` armour body
//! from the first prototype is not written: the DLL zeroes every armour model, so it is never
//! loaded.

use std::{
    fmt::Write as _,
    fs,
    path::{Path, PathBuf},
};

use crate::{
    archive::ArchiveSet,
    bnd::{BinderFile, Bnd4, file_name, read_bnd3, read_bxf3},
    character::{C2280, Character, FitConfig, Writer},
    dcx,
    edge::{EDGE_LAYOUT_DONOR_MESH, EDGE_MATERIAL, edge_append},
    error::{Context, Error, Result},
    fit::{self, Export, SourceMesh, biped, mushroom::export_c2280},
    flver::{
        Flver, LayoutMember, VertexBuffer,
        hide::{blank_vertex_buffers, hide_all_face_sets},
        mtd::{self, MtdClass},
        parse_layout_members, parse_u32_list,
        patch::{PatchReport, encode_vertices, patch_donor, skin_to_donor},
        rebuild::{HeaderAppend, Obb, Slot, SlotLayout, rebuild},
        source::{self, Source, StripReport},
    },
    locate,
    oodle::Oodle,
    textures::{self, TextureSet, character_tpf_from, with_zero_metallic},
};

/// Every full-body part a character preset can ask for, from the community file list of Elden
/// Ring's archives (`/parts/fc_*.partsbnd.dcx`), plus `fc_m_0202` and its `_l`, which the game
/// holds and that list does not name.
pub const FC_PART_NAMES: [&str; 31] = [
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
    "fc_m_0202.partsbnd.dcx",
    "fc_m_0202_l.partsbnd.dcx",
];

const PART_EXTENSION: &str = ".partsbnd.dcx";
const FC_SOURCE_STEM: &str = "FC_M_0000";
/// The mesh of `FC_M_0000.flver` the mushroom is written into: the largest body mesh, and the
/// one the Route A prototype and its Blender successor both targeted.
pub const FC_DONOR_MESH_INDEX: usize = 13;
/// The donor's first material, `#03#` on `P[FC_M_0000]_Body.matxml`, which every opaque slot
/// draws with.
pub const BODY_MATERIAL: u32 = 0;
/// The donor meshes a Biped character's slots take, first group first: mesh 13, then the other
/// body meshes from the most vertices down (`FC_M_0000.flver`: 1138, 952, 942, 604, ...). All 15
/// share mesh 13's vertex layout, which [`slot_buffer`] checks on the donor it is given; the hair
/// meshes after them do not.
const SLOT_DONOR_MESHES: [usize; 15] = [13, 14, 10, 8, 9, 3, 0, 11, 5, 12, 6, 4, 7, 2, 1];
const FG_SOURCE: &str = "fg_a_0000_m.partsbnd.dcx";
/// Requested by female characters but absent from the archives, so it is written as a renamed
/// copy of the male one, as the scripts did.
const FG_FEMALE_ALIAS: &str = "fg_a_0000_f.partsbnd.dcx";
/// The head parts a character's face data picks: `fg_a` is the face and everything on it,
/// `hr_a` the hair. Their ids are four digits, so every name is tried against the archives
/// rather than listed here, and a part a game update adds is hidden with the rest.
const HEAD_PART_PREFIXES: [&str; 2] = ["fg_a", "hr_a"];
const HEAD_PART_SUFFIXES: [&str; 3] = ["", "_l", "_m"];
const HEAD_PART_ID_COUNT: u32 = 10_000;
const NAKED_SLOTS: [&str; 4] = ["hd", "bd", "am", "lg"];
const GENDERS: [&str; 2] = ["m", "f"];
const LODS: [&str; 2] = ["", "_l"];
const FACEGEN: &str = "/facegen/facegen.fgbnd.dcx";
const PARTS_DIR: &str = "parts";
const FACEGEN_DIR: &str = "facegen";
/// Which character a package folder holds. Its presence also marks the folder as one this
/// builder wrote, so a rebuild may clear `parts/` and `facegen/` before writing.
pub const CHARACTER_FILE: &str = "character.txt";
pub const DLL_NAME: &str = "mushroom_man.dll";
/// The files `mushroom_man.dll` looks for beside itself before it does anything.
pub const DLL_PAYLOAD_PROBES: [&str; 2] =
    ["parts/fc_m_0000.partsbnd.dcx", "facegen/facegen.fgbnd.dcx"];

/// Where the inputs come from.
pub struct Inputs<'a> {
    pub archives: &'a ArchiveSet,
    pub oodle: &'a Oodle,
    pub dsr_dir: &'a Path,
    /// The DSR character to build.
    pub character: &'a Character,
    /// An authored mesh (OBJ + weight TSV) to use instead of fitting the character here.
    pub source_override: Option<SourceMesh>,
    /// How the body is written. The character table (`Character.writer`) is meant to choose it,
    /// with the builder's `--writer` developer flag overriding that choice for an authored mesh.
    pub writer: Writer,
    /// What a Biped character's alpha-tested meshes are drawn with.
    pub cutouts: Cutouts,
}

/// What a Biped character's alpha-tested (`*_Edge`) meshes are drawn with.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Cutouts {
    /// The Edge material ([`crate::edge`]), which cuts them out where the albedo's alpha is low.
    #[default]
    EdgeMaterial,
    /// The Body material, solid where they should have holes: the build before the Edge material
    /// existed, kept as a fallback (`--no-cutout-material`).
    BodyMaterial,
}

/// How the body went into the donor, for the summary line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BodyReport {
    /// [`Writer::Patch`]: what each donor FLVER's patch did.
    Patched { high: PatchReport, low: PatchReport },
    /// [`Writer::Rebuild`]: every slot written, in one FLVER both donor binders carry, and, for a
    /// fitted Biped character, what cutting its held items left out.
    Rebuilt {
        slots: Vec<SlotReport>,
        strip: Option<StripReport>,
    },
}

/// One slot the rebuild wrote.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SlotReport {
    pub donor_mesh: usize,
    /// The donor material it draws with: [`BODY_MATERIAL`], or [`EDGE_MATERIAL`] retargeted.
    pub material: u32,
    pub cull_backfaces: bool,
    /// The DSR meshes it holds; `None` for an authored mesh.
    pub source: Option<SlotSource>,
    pub vertices: usize,
    /// LOD0, LOD1 and LOD2.
    pub triangles: [usize; 3],
}

/// The DSR material a slot's meshes share, and which of the character's meshes they are.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SlotSource {
    pub material: usize,
    /// The MTD's file name, `C_DullLeather[DSB]_Edge.mtd`.
    pub mtd: String,
    pub class: MtdClass,
    pub meshes: Vec<usize>,
}

impl SlotReport {
    /// One line of the build summary.
    pub fn describe(&self) -> String {
        let material = match self.material {
            BODY_MATERIAL => format!("Body material {BODY_MATERIAL}"),
            EDGE_MATERIAL => format!("Edge material {EDGE_MATERIAL}"),
            other => format!("material {other}"),
        };
        let sides = if self.cull_backfaces {
            "back faces culled"
        } else {
            "double-sided"
        };
        let triangles = match self.triangles {
            [a, b, c] if a == b && b == c => format!("{a} triangles at every distance"),
            [a, b, c] => format!("{a}/{b}/{c} triangles (LOD0/1/2)"),
        };
        let mut line = format!(
            "mesh {}: {} vertices, {triangles}, {material}, {sides}",
            self.donor_mesh, self.vertices
        );
        if let Some(source) = &self.source {
            let meshes: Vec<String> = source.meshes.iter().map(usize::to_string).collect();
            let _ = write!(
                line,
                "; from DSR mesh{} {} on material {} ({}, {})",
                if meshes.len() == 1 { "" } else { "es" },
                meshes.join(", "),
                source.material,
                source.mtd,
                class_name(source.class)
            );
        }
        line
    }
}

fn class_name(class: MtdClass) -> &'static str {
    match class {
        MtdClass::Opaque => "opaque",
        MtdClass::AlphaTest => "alpha-tested",
        MtdClass::AlphaBlend => "alpha-blended",
        MtdClass::Unsupported => "unsupported",
    }
}

/// What a build wrote, for the summary line and the tests.
#[derive(Debug)]
pub struct BuildReport {
    pub files: Vec<PathBuf>,
    pub body: BodyReport,
    pub source_vertices: usize,
    pub source_triangles: usize,
    /// Face and hair parts written with their faces hidden.
    pub head_parts: usize,
    /// Face and hair parts left to the game because they hold no model.
    pub head_parts_without_model: usize,
}

impl BuildReport {
    /// The body's lines of the build summary: what went into the donor, slot by slot, and what
    /// cutting the held items left out.
    pub fn body_summary(&self) -> String {
        match &self.body {
            BodyReport::Patched { high, .. } => {
                let mut line = format!(
                    "body: {} vertices and {} triangles written in place into donor mesh \
                     {FC_DONOR_MESH_INDEX}, on Body material {BODY_MATERIAL}",
                    self.source_vertices, self.source_triangles
                );
                if high.triangles_dropped > 0 {
                    let _ = write!(
                        line,
                        " ({} triangles did not fit and were left out)",
                        high.triangles_dropped
                    );
                }
                line
            }
            BodyReport::Rebuilt { slots, strip } => {
                let vertices: usize = slots.iter().map(|slot| slot.vertices).sum();
                let triangles: usize = slots.iter().map(|slot| slot.triangles[0]).sum();
                let mut out = format!(
                    "body: {vertices} vertices and {triangles} triangles in {} slots of a rebuilt \
                     donor, every other mesh hidden",
                    slots.len()
                );
                for slot in slots {
                    let _ = write!(out, "\n  {}", slot.describe());
                }
                if let Some(strip) = strip {
                    let _ = write!(
                        out,
                        "\n  held items: {} vertices cut and the {} triangles touching them; {} \
                         vertices only the far LODs drew left out",
                        strip.stripped_vertices,
                        strip.dropped_triangles,
                        strip.unreferenced_vertices
                    );
                }
                out
            }
        }
    }
}

/// A DSR character's model, its TPF, and the texture set its kept materials name, with the TPF of
/// every other character that set is read out of.
pub struct CharacterFiles {
    pub id: &'static str,
    pub flver: Vec<u8>,
    /// `<id>.tpf`, the character's own.
    pub tpf: Vec<u8>,
    /// `<owner>.tpf` out of `chr/<owner>.chrbnd.dcx` for each other character the set's paths
    /// name, by owner id: c2900's body is drawn with c2909's textures.
    pub borrowed_tpfs: Vec<(String, Vec<u8>)>,
    pub textures: TextureSet,
}

impl CharacterFiles {
    /// Every TPF [`Self::textures`] is read out of, under the id of the character holding it, as
    /// [`crate::textures::character_tpf_from`] takes them.
    pub fn texture_tpfs(&self) -> Vec<(&str, &[u8])> {
        std::iter::once((self.id, self.tpf.as_slice()))
            .chain(
                self.borrowed_tpfs
                    .iter()
                    .map(|(owner, tpf)| (owner.as_str(), tpf.as_slice())),
            )
            .collect()
    }
}

/// `<id>.flver` and `<id>.tpf` out of `chr/<id>.chrbnd.dcx` in a Dark Souls Remastered install,
/// and the texture set of the materials the build keeps ([`kept_materials`]): a stripped held
/// item's textures are never asked for. A texture its path puts in another character's folder
/// is read out of that character's binder.
pub fn read_character(dsr_dir: &Path, character: &Character) -> Result<CharacterFiles> {
    let id = character.id;
    let (path, files) = read_chrbnd(
        dsr_dir,
        id,
        &format!("this Dark Souls Remastered install has no {id}"),
    )?;
    let flver = binder_entry(&files, &format!("{id}.flver"), &path)?;
    let source = source::parse(&flver).context(format!("{id}.flver"))?;
    let kept = kept_materials(character, &source)?
        .into_iter()
        .map(|index| {
            source.materials.get(index).cloned().ok_or_else(|| {
                Error::new(format!(
                    "{id}.flver draws with material {index}, which it does not have"
                ))
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let textures = TextureSet::of_materials(&kept, id).context(format!("{id}.flver"))?;
    let mut borrowed_tpfs = Vec::new();
    for owner in textures.owners() {
        if owner.eq_ignore_ascii_case(id) {
            continue;
        }
        let (path, files) = read_chrbnd(
            dsr_dir,
            owner,
            &format!("{id}'s materials draw with {owner}'s textures"),
        )?;
        borrowed_tpfs.push((
            owner.to_owned(),
            character_textures(dsr_dir, owner, &files, &path)?,
        ));
    }
    let mut read = CharacterFiles {
        id,
        tpf: character_textures(dsr_dir, id, &files, &path)?,
        flver,
        borrowed_tpfs,
        textures,
    };
    read.textures = read
        .textures
        .clone()
        .without_absent_maps(&read.texture_tpfs())?;
    Ok(read)
}

/// Character `id`'s textures as one TPF: `<id>.tpf` out of its binder `files`, or, for a
/// character that keeps them split, every `<texture>.tpf.dcx` of the `<id>.chrtpfbhd` header in
/// its binder and the `chr/<id>.chrtpfbdt` data beside it, merged. c2320's are split six ways,
/// one texture to a TPF.
fn character_textures(
    dsr_dir: &Path,
    id: &str,
    files: &[BinderFile],
    path: &Path,
) -> Result<Vec<u8>> {
    let header_name = format!("{id}.chrtpfbhd");
    let Some(header) = files
        .iter()
        .find(|file| file_name(&file.name).eq_ignore_ascii_case(&header_name))
    else {
        return binder_entry(files, &format!("{id}.tpf"), path);
    };
    let data_path = locate::chrtpfbdt_path(dsr_dir, id);
    let data = fs::read(&data_path).context(format!(
        "reading {}, the textures {} indexes",
        data_path.display(),
        path.display()
    ))?;
    let entries = read_bxf3(&header.data, &data).context(data_path.display())?;
    textures::merge_tpfs(
        &entries
            .iter()
            .map(|entry| {
                dcx::decompress(&entry.data, None).context(format!(
                    "{} in {}",
                    file_name(&entry.name),
                    data_path.display()
                ))
            })
            .collect::<Result<Vec<_>>>()?,
    )
    .context(data_path.display())
}

/// The materials of the meshes `character`'s build keeps, each once, ascending: for a Biped
/// character, the meshes its held-item strip leaves a triangle in ([`biped::kept_meshes`]); for
/// c2280, every mesh. c2900's sword, greatsword, shield and bow are four of its six materials,
/// each with textures of its own, and none of them is kept.
pub fn kept_materials(character: &Character, source: &Source) -> Result<Vec<usize>> {
    let mut kept: Vec<usize> = match character.fit {
        FitConfig::Biped(params) => biped::kept_meshes(source, params)
            .context(format!("cutting {}'s held items", character.id))?
            .0
            .iter()
            .map(|mesh| mesh.material_index)
            .collect(),
        FitConfig::Mushroom(_) => source
            .meshes
            .iter()
            .map(|mesh| mesh.material_index)
            .collect(),
    };
    kept.sort_unstable();
    kept.dedup();
    Ok(kept)
}

/// The entries of `chr/<id>.chrbnd.dcx`, and its path; `why` says what wanted it when it is
/// missing.
fn read_chrbnd(dsr_dir: &Path, id: &str, why: &str) -> Result<(PathBuf, Vec<BinderFile>)> {
    let path = locate::chrbnd_path(dsr_dir, id);
    if !path.is_file() {
        return Err(Error::new(format!(
            "{} is missing -- {why}",
            path.display()
        )));
    }
    let packed = fs::read(&path).context(format!("reading {}", path.display()))?;
    let files = read_bnd3(&dcx::decompress(&packed, None)?).context(path.display())?;
    Ok((path, files))
}

fn binder_entry(files: &[BinderFile], name: &str, path: &Path) -> Result<Vec<u8>> {
    files
        .iter()
        .find(|file| file_name(&file.name).eq_ignore_ascii_case(name))
        .map(|file| file.data.clone())
        .ok_or_else(|| Error::new(format!("{} has no {name}", path.display())))
}

/// The donor body, `FC_M_0000.flver`, out of the installed game's archives: what every fit is
/// made against.
pub fn read_donor(archives: &ArchiveSet, oodle: &Oodle) -> Result<Vec<u8>> {
    let logical = format!("/parts/{}{PART_EXTENSION}", FC_SOURCE_STEM.to_lowercase());
    let packed = archives.read(&logical)?;
    let binder = Bnd4::read(&dcx::decompress(&packed, Some(oodle))?).context(&logical)?;
    let name = format!("{FC_SOURCE_STEM}.flver");
    binder
        .file_named(&name)
        .map(|file| file.data.clone())
        .ok_or_else(|| Error::new(format!("{logical} has no {name}")))
}

/// Everything [`build`] does to `character` that needs no archive: the fit against `donor`, the
/// slots, the rebuilt FLVER and the body TPF. Returns the slot count.
pub fn trial_build(donor: &[u8], character: &Character, files: &CharacterFiles) -> Result<usize> {
    let FitConfig::Biped(_) = character.fit else {
        return Err(Error::new(format!(
            "{} is not fitted as a Biped",
            character.id
        )));
    };
    let body = biped_body(donor, character, &files.flver, Cutouts::EdgeMaterial)?;
    rebuild(donor, &body.slots, true, &HeaderAppend::default())?;
    character_tpf_from(&files.texture_tpfs(), &files.textures, FC_SOURCE_STEM, "")?;
    Ok(body.slots.len())
}

/// c2280's model and textures out of a Dark Souls Remastered install.
pub fn read_c2280(dsr_dir: &Path) -> Result<(Vec<u8>, Vec<u8>)> {
    let files = read_character(dsr_dir, &C2280)?;
    Ok((files.flver, files.tpf))
}

/// Why `build` cannot build `character` through `writer` with `cutouts`, if it cannot. Asked
/// before any archive is opened, so a refusal costs nothing.
pub fn buildable(character: &Character, writer: Writer, cutouts: Cutouts) -> Result<()> {
    match (character.fit, writer, cutouts) {
        (FitConfig::Biped(_), Writer::Patch, _) => Err(Error::new(format!(
            "{} is a Biped character: its meshes go into the donor through the rebuild writer, \
             which the patch writer cannot stand in for",
            character.id
        ))),
        (FitConfig::Mushroom(_), _, Cutouts::BodyMaterial) => Err(Error::new(format!(
            "{} has no cutout meshes to move onto the Body material (--no-cutout-material is for \
             Biped characters)",
            character.id
        ))),
        _ => Ok(()),
    }
}

/// Build the whole package into `out_dir` (`character.txt`, and `parts/` and `facegen/` beneath
/// it).
pub fn build(inputs: Inputs<'_>, out_dir: &Path) -> Result<BuildReport> {
    let character = inputs.character;
    buildable(character, inputs.writer, inputs.cutouts)?;
    let dsr = read_character(inputs.dsr_dir, character)?;
    let parts_dir = out_dir.join(PARTS_DIR);
    let facegen_dir = out_dir.join(FACEGEN_DIR);
    let mut files = vec![claim_folder(out_dir, character)?];
    fs::create_dir_all(&parts_dir).context(format!("creating {}", parts_dir.display()))?;
    fs::create_dir_all(&facegen_dir).context(format!("creating {}", facegen_dir.display()))?;
    let oodle = Some(inputs.oodle);
    let fitting = format!("fitting {} to the donor", character.id);

    // The body, high and low detail. The fit is made once, against the high-detail donor's
    // skeleton; the low-detail one has the same bones.
    let mut source = inputs.source_override;
    let mut bodies = Vec::new();
    let mut patches = Vec::new();
    let mut rebuilt: Option<Rebuilt> = None;
    for lod in LODS {
        let stem = format!("{FC_SOURCE_STEM}{}", lod.to_uppercase());
        let logical = format!("/parts/{}{PART_EXTENSION}", stem.to_lowercase());
        let packed = inputs.archives.read(&logical)?;
        let mut binder = Bnd4::read(&dcx::decompress(&packed, oodle)?).context(&logical)?;
        let flver = binder_file(&mut binder, &format!("{stem}.flver"), &logical)?;
        let rebuilding = format!("rebuilding {stem}.flver");
        match (&rebuilt, character.fit) {
            // The high and low donor FLVERs are the same file (sha256 f32ee054...), so the body
            // is rebuilt once and the result written into both.
            (Some(done), _) => {
                if done.donor != flver.data {
                    return Err(Error::new(format!(
                        "{stem}.flver differs from the high-detail donor; the rebuilt body is \
                         shared between them"
                    )));
                }
                flver.data.clone_from(&done.body);
            }
            // `buildable` has refused the patch writer for a Biped character.
            (None, FitConfig::Biped(_)) => {
                let body = biped_body(&flver.data, character, &dsr.flver, inputs.cutouts)
                    .context(&fitting)?;
                let append = if body.edge {
                    edge_append(inputs.archives, inputs.oodle, &flver.data, character.id)
                        .context("the Edge material")?
                } else {
                    HeaderAppend::default()
                };
                let bytes =
                    rebuild(&flver.data, &body.slots, true, &append).context(&rebuilding)?;
                rebuilt = Some(Rebuilt {
                    donor: std::mem::replace(&mut flver.data, bytes.clone()),
                    body: bytes,
                    slots: body.reports,
                    strip: Some(body.strip),
                    edge: body.edge,
                    source_vertices: body.source_vertices,
                    source_triangles: body.source_triangles,
                });
            }
            (None, FitConfig::Mushroom(fit)) => {
                if source.is_none() {
                    source = Some(export_c2280(&dsr.flver, &flver.data, fit).context(&fitting)?);
                }
                let source = source.as_ref().ok_or("no source mesh")?;
                match inputs.writer {
                    Writer::Patch => patches.push(
                        patch_donor(&mut flver.data, source, FC_DONOR_MESH_INDEX)
                            .context(format!("patching {stem}.flver"))?,
                    ),
                    Writer::Rebuild => {
                        let (bytes, report) =
                            rebuild_body(&flver.data, source).context(&rebuilding)?;
                        rebuilt = Some(Rebuilt {
                            donor: std::mem::replace(&mut flver.data, bytes.clone()),
                            body: bytes,
                            slots: vec![report],
                            strip: None,
                            edge: false,
                            source_vertices: source.vertices.len(),
                            source_triangles: source.triangles.len(),
                        });
                    }
                }
            }
        }
        let mut textures =
            character_tpf_from(&dsr.texture_tpfs(), &dsr.textures, FC_SOURCE_STEM, lod)
                .context(format!("{}'s textures", character.id))?;
        // The Edge material samples a metallic map, which no DSR set has.
        if rebuilt.as_ref().is_some_and(|done| done.edge) {
            textures = with_zero_metallic(&textures, FC_SOURCE_STEM, lod)?;
        }
        binder_file(&mut binder, &format!("{stem}.tpf"), &logical)?.data = textures;
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

    // Face and hair, every part the archives hold, emptied. A part with no FLVER (the face
    // decals are textures only) draws nothing once the face under it is hidden, so it is left
    // to the game's own copy.
    let mut head_parts = 0;
    let mut head_parts_without_model = 0;
    for name in head_part_names().filter(|name| inputs.archives.contains(&format!("/parts/{name}")))
    {
        let logical = format!("/parts/{name}");
        let packed = inputs.archives.read(&logical)?;
        let mut binder = Bnd4::read(&dcx::decompress(&packed, oodle)?).context(&logical)?;
        if hide_flvers(&mut binder, Vertices::Blanked).context(&logical)? == 0 {
            head_parts_without_model += 1;
            continue;
        }
        files.push(write_part(&parts_dir.join(&name), &packed, &binder, oodle)?);
        head_parts += 1;
        if name == FG_SOURCE {
            let female = renamed(&binder, &[("FG_A_0000_M", "FG_A_0000_F")]);
            files.push(write_part(
                &parts_dir.join(FG_FEMALE_ALIAS),
                &packed,
                &female,
                oodle,
            )?);
        }
    }
    if !files.contains(&parts_dir.join(FG_FEMALE_ALIAS)) {
        return Err(Error::new(format!(
            "the archives have no /parts/{FG_SOURCE}"
        )));
    }

    // The default naked slots the DLL's zeroed armour falls back to, emptied.
    for slot in NAKED_SLOTS {
        for gender in GENDERS {
            for lod in LODS {
                let name = format!("{slot}_{gender}_0000{lod}{PART_EXTENSION}");
                let logical = format!("/parts/{name}");
                let packed = inputs.archives.read(&logical)?;
                let mut binder = Bnd4::read(&dcx::decompress(&packed, oodle)?).context(&logical)?;
                if hide_flvers(&mut binder, Vertices::Kept).context(&logical)? == 0 {
                    return Err(Error::new(format!("{logical} holds no FLVER to hide")));
                }
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

    let (body, source_vertices, source_triangles) = match (rebuilt, patches.as_slice()) {
        (Some(done), _) => (
            BodyReport::Rebuilt {
                slots: done.slots,
                strip: done.strip,
            },
            done.source_vertices,
            done.source_triangles,
        ),
        (None, &[high, low]) => {
            let source = source.ok_or("no source mesh")?;
            (
                BodyReport::Patched { high, low },
                source.vertices.len(),
                source.triangles.len(),
            )
        }
        (None, _) => return Err("the body was neither patched twice nor rebuilt".into()),
    };
    Ok(BuildReport {
        files,
        body,
        source_vertices,
        source_triangles,
        head_parts,
        head_parts_without_model,
    })
}

/// The donor FLVER as read and as rebuilt, once, with what went into it.
struct Rebuilt {
    donor: Vec<u8>,
    body: Vec<u8>,
    slots: Vec<SlotReport>,
    strip: Option<StripReport>,
    /// A slot draws with the Edge material, so the body's TPFs need its metallic map.
    edge: bool,
    source_vertices: usize,
    source_triangles: usize,
}

/// `donor` (`FC_M_0000.flver`) with `source` as its mesh 13, through the rebuild writer: one
/// slot on the Body material, back faces culled, its box around its own vertices, the same
/// triangles at every distance, and every other mesh hidden.
pub fn rebuild_body(donor: &[u8], source: &SourceMesh) -> Result<(Vec<u8>, SlotReport)> {
    let flver = Flver::parse(donor)?;
    let buffer = slot_buffer(donor, &flver, FC_DONOR_MESH_INDEX)?;
    let vertices = skin_to_donor(source, &flver)?;
    let vertex_bytes = encode_vertices(
        &buffer_members(donor, &flver, &buffer)?,
        buffer.vertex_size,
        &vertices,
        flver.header.uv_factor(),
    )?;
    let obb = Obb::around(vertices.iter().map(|vertex| vertex.position))
        .ok_or("the source mesh has no vertices")?;
    let triangles = &source.triangles;
    let slot = Slot {
        donor_mesh: FC_DONOR_MESH_INDEX,
        material: BODY_MATERIAL,
        cull_backfaces: true,
        vertex_count: vertices.len(),
        vertex_bytes,
        layout: None,
        lods: [triangles.clone(), triangles.clone(), triangles.clone()],
        obb,
    };
    let report = slot_report(&slot, None);
    Ok((
        rebuild(donor, &[slot], true, &HeaderAppend::default())?,
        report,
    ))
}

/// A Biped character fitted onto the donor, as the slots the rebuild writer takes.
pub struct BipedBody {
    pub slots: Vec<Slot>,
    pub reports: Vec<SlotReport>,
    pub strip: StripReport,
    /// Some slot draws with [`EDGE_MATERIAL`], so the rebuild needs the Edge material's
    /// [`HeaderAppend`] (`edge::edge_append`) and the body's TPFs a metallic map.
    pub edge: bool,
    pub source_vertices: usize,
    pub source_triangles: usize,
}

/// `character` (a Biped one, its model `character_flver`) fitted to `donor` (`FC_M_0000.flver`)
/// and cut into slots: its kept meshes grouped by DSR material and back-face culling, in the
/// order the groups first appear, each group on the next of [`SLOT_DONOR_MESHES`]. A group
/// draws with [`BODY_MATERIAL`], or with [`EDGE_MATERIAL`] when its MTD is alpha-tested and
/// `cutouts` asks for the Edge material; it keeps its source's culling, its own box, and its
/// LOD0 triangles at every distance. A kept mesh whose MTD has no Elden Ring route is refused.
pub fn biped_body(
    donor: &[u8],
    character: &Character,
    character_flver: &[u8],
    cutouts: Cutouts,
) -> Result<BipedBody> {
    let export = fit::export(character, character_flver, donor)?;
    let strip = export
        .strip
        .ok_or("the Biped fit reported no held-item strip")?;
    let groups = group_by_material(&export, &source::parse(character_flver)?, character.id)?;
    if groups.len() > SLOT_DONOR_MESHES.len() {
        return Err(Error::new(format!(
            "{} material groups, and the donor has {} body meshes to put them on",
            groups.len(),
            SLOT_DONOR_MESHES.len()
        )));
    }
    let flver = Flver::parse(donor)?;
    let layout_of = slot_buffer(donor, &flver, FC_DONOR_MESH_INDEX)?;
    let edge_buffer = slot_buffer(donor, &flver, EDGE_LAYOUT_DONOR_MESH)?;
    let skinned = skin_to_donor(&export.mesh, &flver)?;
    let mut body = BipedBody {
        slots: Vec::with_capacity(groups.len()),
        reports: Vec::with_capacity(groups.len()),
        strip,
        edge: false,
        source_vertices: export.mesh.vertices.len(),
        source_triangles: export.mesh.triangles.len(),
    };
    for (group, donor_mesh) in groups.into_iter().zip(SLOT_DONOR_MESHES) {
        let buffer = slot_buffer(donor, &flver, donor_mesh)?;
        if (buffer.layout_index, buffer.vertex_size)
            != (layout_of.layout_index, layout_of.vertex_size)
        {
            return Err(Error::new(format!(
                "the donor's mesh {donor_mesh} is laid out as layout {} ({} bytes a vertex), not \
                 as mesh {FC_DONOR_MESH_INDEX}'s layout {} ({} bytes)",
                buffer.layout_index,
                buffer.vertex_size,
                layout_of.layout_index,
                layout_of.vertex_size
            )));
        }
        let material = match (group.source.class, cutouts) {
            (MtdClass::Opaque, _) | (MtdClass::AlphaTest, Cutouts::BodyMaterial) => BODY_MATERIAL,
            (MtdClass::AlphaTest, Cutouts::EdgeMaterial) => EDGE_MATERIAL,
            (class, _) => {
                return Err(Error::new(format!(
                    "DSR material {} is {}, which has no Elden Ring route",
                    group.source.material,
                    class_name(class)
                )));
            }
        };
        body.edge |= material == EDGE_MATERIAL;
        let (layout, layout_buffer) = if material == EDGE_MATERIAL {
            (
                Some(SlotLayout {
                    layout_index: edge_buffer.layout_index,
                    vertex_size: edge_buffer.vertex_size,
                }),
                edge_buffer,
            )
        } else {
            (None, buffer)
        };
        let mut remap = vec![u32::MAX; skinned.len()];
        for (new, &old) in group.vertices.iter().enumerate() {
            remap[old] = u32::try_from(new)?;
        }
        let vertices: Vec<_> = group.vertices.iter().map(|&old| skinned[old]).collect();
        let triangles: Vec<[u32; 3]> = group
            .triangles
            .iter()
            .map(|triangle| triangle.map(|corner| remap[corner as usize]))
            .collect();
        let slot = Slot {
            donor_mesh,
            material,
            cull_backfaces: group.source.cull_backfaces,
            vertex_count: vertices.len(),
            vertex_bytes: encode_vertices(
                &buffer_members(donor, &flver, &layout_buffer)?,
                layout_buffer.vertex_size,
                &vertices,
                flver.header.uv_factor(),
            )?,
            layout,
            lods: [triangles.clone(), triangles.clone(), triangles],
            obb: Obb::around(vertices.iter().map(|vertex| vertex.position))
                .ok_or("a material group has no vertices")?,
        };
        body.reports.push(slot_report(
            &slot,
            Some(SlotSource {
                material: group.source.material,
                mtd: group.source.mtd,
                class: group.source.class,
                meshes: group.source.meshes,
            }),
        ));
        body.slots.push(slot);
    }
    Ok(body)
}

fn slot_report(slot: &Slot, source: Option<SlotSource>) -> SlotReport {
    SlotReport {
        donor_mesh: slot.donor_mesh,
        material: slot.material,
        cull_backfaces: slot.cull_backfaces,
        source,
        vertices: slot.vertex_count,
        triangles: slot.lods.each_ref().map(Vec::len),
    }
}

/// The one vertex buffer of the donor's mesh `mesh`.
fn slot_buffer(donor: &[u8], flver: &Flver, mesh: usize) -> Result<VertexBuffer> {
    let entry = flver
        .meshes
        .get(mesh)
        .ok_or_else(|| Error::new(format!("the donor has no mesh {mesh}")))?;
    let buffer_index =
        *parse_u32_list(donor, entry.vertex_buffer_offset, entry.vertex_buffer_count)?
            .first()
            .ok_or_else(|| Error::new(format!("the donor's mesh {mesh} has no vertex buffer")))?
            as usize;
    flver
        .vertex_buffers
        .get(buffer_index)
        .copied()
        .ok_or_else(|| {
            Error::new(format!(
                "the donor's mesh {mesh} names a missing vertex buffer"
            ))
        })
}

fn buffer_members(donor: &[u8], flver: &Flver, buffer: &VertexBuffer) -> Result<Vec<LayoutMember>> {
    let layout = *flver
        .layouts
        .get(buffer.layout_index)
        .ok_or("a donor vertex buffer names a missing layout")?;
    parse_layout_members(donor, layout)
}

/// One slot's worth of the fitted mesh: the export vertices and triangles of the kept meshes
/// that share a DSR material and a culling.
#[derive(Debug)]
struct Group {
    source: GroupSource,
    /// Export vertex indices, in export order.
    vertices: Vec<usize>,
    /// Triangles, in export vertex indices.
    triangles: Vec<[u32; 3]>,
}

#[derive(Debug)]
struct GroupSource {
    material: usize,
    cull_backfaces: bool,
    mtd: String,
    class: MtdClass,
    meshes: Vec<usize>,
}

/// The export's vertices and triangles grouped by the DSR material and culling of the mesh each
/// came from (`Export::source_map`), in the order the groups first appear. Refuses a kept mesh
/// whose MTD has no Elden Ring route, and a triangle whose corners come from two groups.
fn group_by_material(export: &Export, source: &Source, id: &str) -> Result<Vec<Group>> {
    let mut groups: Vec<Group> = Vec::new();
    let mut group_of = Vec::with_capacity(export.source_map.len());
    for (vertex, row) in export.source_map.iter().enumerate() {
        let mesh = source
            .meshes
            .get(row.mesh)
            .ok_or_else(|| Error::new(format!("{id} has no mesh {}", row.mesh)))?;
        let key = (mesh.material_index, mesh.cull_backfaces);
        let index = match groups
            .iter()
            .position(|group| (group.source.material, group.source.cull_backfaces) == key)
        {
            Some(index) => index,
            None => {
                let material = source.materials.get(mesh.material_index).ok_or_else(|| {
                    Error::new(format!(
                        "{id}'s mesh {} names material {}, which it does not have",
                        row.mesh, mesh.material_index
                    ))
                })?;
                let file = material
                    .mtd
                    .rsplit(['\\', '/'])
                    .next()
                    .unwrap_or(&material.mtd);
                let class = mtd::classify(&material.mtd);
                if class.route().is_none() {
                    return Err(Error::new(format!(
                        "{id}'s mesh {} is drawn with {file:?}, an {} MTD that has no Elden Ring \
                         route yet",
                        row.mesh,
                        class_name(class)
                    )));
                }
                groups.push(Group {
                    source: GroupSource {
                        material: mesh.material_index,
                        cull_backfaces: mesh.cull_backfaces,
                        mtd: file.to_owned(),
                        class,
                        meshes: Vec::new(),
                    },
                    vertices: Vec::new(),
                    triangles: Vec::new(),
                });
                groups.len() - 1
            }
        };
        let group = &mut groups[index];
        if !group.source.meshes.contains(&row.mesh) {
            group.source.meshes.push(row.mesh);
        }
        group.vertices.push(vertex);
        group_of.push(index);
    }
    for triangle in &export.mesh.triangles {
        let [a, b, c] = triangle.map(|corner| group_of.get(corner as usize).copied());
        match (a, b, c) {
            (Some(a), Some(b), Some(c)) if a == b && b == c => groups[a].triangles.push(*triangle),
            _ => {
                return Err(Error::new(format!(
                    "triangle {triangle:?} of the fitted {id} spans two materials"
                )));
            }
        }
    }
    Ok(groups)
}

/// Mark `out_dir` as `character`'s package. A folder an earlier build already marked has its
/// `parts/` and `facegen/` removed first, so a character whose file set differs from the last one
/// leaves nothing stale behind; a folder without the mark is never cleared.
fn claim_folder(out_dir: &Path, character: &Character) -> Result<PathBuf> {
    let marker = out_dir.join(CHARACTER_FILE);
    if marker.is_file() {
        for sub in [PARTS_DIR, FACEGEN_DIR] {
            let dir = out_dir.join(sub);
            if dir.is_dir() {
                fs::remove_dir_all(&dir).context(format!("clearing {}", dir.display()))?;
            }
        }
    }
    fs::create_dir_all(out_dir).context(format!("creating {}", out_dir.display()))?;
    fs::write(&marker, format!("{}\n", character.id))
        .context(format!("writing {}", marker.display()))?;
    Ok(marker)
}

/// Every name a face or hair part could have, `fg_a_0000.partsbnd.dcx` to
/// `hr_a_9999_m.partsbnd.dcx`.
fn head_part_names() -> impl Iterator<Item = String> {
    HEAD_PART_PREFIXES.into_iter().flat_map(|prefix| {
        (0..HEAD_PART_ID_COUNT).flat_map(move |id| {
            HEAD_PART_SUFFIXES
                .into_iter()
                .map(move |suffix| format!("{prefix}_{id:04}{suffix}{PART_EXTENSION}"))
        })
    })
}

fn binder_file<'a>(binder: &'a mut Bnd4, name: &str, context: &str) -> Result<&'a mut BinderFile> {
    binder
        .file_named_mut(name)
        .ok_or_else(|| Error::new(format!("{context} has no {name}")))
}

/// What hiding a FLVER does to its vertices. The naked slots keep theirs, as they did when they
/// were first seen working in game. The face and hair parts are blanked: hidden in place they
/// made the package 263 MB against 47 MB without them, and their vertices are most of that.
#[derive(Clone, Copy)]
enum Vertices {
    Kept,
    Blanked,
}

/// Hide every FLVER in `binder`, returning how many there were.
fn hide_flvers(binder: &mut Bnd4, vertices: Vertices) -> Result<usize> {
    let mut hidden = 0;
    for file in &mut binder.files {
        let name = file_name(&file.name).to_owned();
        if name.to_lowercase().ends_with(".flver") {
            hide_all_face_sets(&mut file.data).context(&name)?;
            if let Vertices::Blanked = vertices {
                blank_vertex_buffers(&mut file.data).context(&name)?;
            }
            hidden += 1;
        }
    }
    Ok(hidden)
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
    use crate::{
        character::C2650,
        fit::{MapRole, SourceRow, SourceVertex},
        flver::source::{DsrMesh, SourceMaterial},
    };

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
    fn head_part_names_cover_face_eyes_and_hair_but_no_body() {
        let names: Vec<String> = head_part_names().collect();
        for expected in [
            FG_SOURCE,
            "fg_a_0100.partsbnd.dcx",
            "fg_a_1500_l.partsbnd.dcx",
            "hr_a_0000.partsbnd.dcx",
            "hr_a_0125_l.partsbnd.dcx",
        ] {
            assert!(names.iter().any(|name| name == expected), "{expected}");
        }
        assert!(
            names
                .iter()
                .all(|name| name.starts_with("fg_a_") || name.starts_with("hr_a_"))
        );
        assert!(
            !names
                .iter()
                .any(|name| FC_PART_NAMES.contains(&name.as_str()))
        );
    }

    #[test]
    fn a_marked_folder_is_cleared_and_an_unmarked_one_is_not() {
        let dir = std::env::temp_dir().join(format!("mmb-claim-{}", std::process::id()));
        let stray = dir.join(PARTS_DIR).join("stray.partsbnd.dcx");
        let kept = dir.join("mushroom-man.me3");
        fs::create_dir_all(stray.parent().unwrap()).unwrap();
        fs::write(&stray, b"x").unwrap();
        fs::write(&kept, b"x").unwrap();
        // A folder this builder never marked keeps what is in it.
        claim_folder(&dir, &C2280).unwrap();
        assert!(stray.is_file());
        assert_eq!(
            fs::read_to_string(dir.join(CHARACTER_FILE)).unwrap(),
            "c2280\n"
        );
        // Once marked, a rebuild starts from empty `parts/`, and leaves the profile alone.
        claim_folder(&dir, &C2650).unwrap();
        assert!(!stray.exists() && kept.is_file());
        assert_eq!(
            fs::read_to_string(dir.join(CHARACTER_FILE)).unwrap(),
            "c2650\n"
        );
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_biped_needs_the_rebuild_writer_and_only_a_biped_takes_the_body_fallback() {
        let refused = |character, writer, cutouts| {
            buildable(character, writer, cutouts)
                .expect_err("expected a refusal")
                .to_string()
        };
        buildable(&C2280, Writer::Patch, Cutouts::EdgeMaterial).unwrap();
        buildable(&C2650, Writer::Rebuild, Cutouts::EdgeMaterial).unwrap();
        buildable(&C2650, Writer::Rebuild, Cutouts::BodyMaterial).unwrap();
        assert!(refused(&C2650, Writer::Patch, Cutouts::EdgeMaterial).contains("rebuild writer"));
        let fallback = refused(&C2280, Writer::Patch, Cutouts::BodyMaterial);
        assert!(fallback.contains("c2280") && fallback.contains("--no-cutout-material"));
    }

    #[test]
    fn the_slot_meshes_start_at_mesh_13_and_none_repeats() {
        let mut meshes = SLOT_DONOR_MESHES.to_vec();
        assert_eq!(meshes[0], FC_DONOR_MESH_INDEX);
        meshes.sort_unstable();
        meshes.dedup();
        assert_eq!(meshes, (0..=14).collect::<Vec<_>>());
    }

    fn material(name: &str) -> SourceMaterial {
        SourceMaterial {
            mtd: format!(r"N:\FRPG\data\Material\mtd\character\{name}.mtd"),
            ..SourceMaterial::default()
        }
    }

    fn dsr_mesh(material_index: usize, cull_backfaces: bool) -> DsrMesh {
        DsrMesh {
            material_index,
            cull_backfaces,
            palette: Vec::new(),
            vertices: Vec::new(),
            lods: Default::default(),
        }
    }

    /// Three vertices from each of `meshes`, in that order, and `triangles` over them.
    fn export(meshes: &[usize], triangles: Vec<[u32; 3]>) -> Export {
        let source_map: Vec<SourceRow> = meshes
            .iter()
            .flat_map(|&mesh| {
                (0..3).map(move |vertex| SourceRow {
                    mesh,
                    vertex,
                    bone: String::new(),
                    role: MapRole::Other,
                })
            })
            .collect();
        Export {
            character: "c2650",
            mesh: SourceMesh {
                vertices: vec![SourceVertex::default(); source_map.len()],
                triangles,
                weights: Vec::new(),
            },
            scale: 1.0,
            params: Vec::new(),
            stretch: Vec::new(),
            grip_anchor: None,
            source_map,
            strip: None,
        }
    }

    /// c2650's shape: an opaque body on two meshes, an alpha-tested cowl, and an opaque skirt
    /// drawn double-sided on a material of its own.
    fn source() -> Source {
        Source {
            bones: Vec::new(),
            materials: vec![
                material("C_DullLeather[DSB]"),
                material("C_DullLeather[DSB]_Edge"),
                material("C_DullLeather[DSB]"),
            ],
            meshes: vec![
                dsr_mesh(0, true),
                dsr_mesh(0, true),
                dsr_mesh(1, false),
                dsr_mesh(2, false),
            ],
        }
    }

    #[test]
    fn kept_meshes_group_by_material_and_culling_in_the_order_they_first_appear() {
        // Export vertices 0..3 are mesh 0's, 3..6 mesh 2's, 6..9 mesh 1's, 9..12 mesh 3's.
        let triangles = vec![[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11], [2, 1, 0]];
        let groups =
            group_by_material(&export(&[0, 2, 1, 3], triangles), &source(), "c2650").unwrap();
        let keys: Vec<(usize, bool, MtdClass)> = groups
            .iter()
            .map(|group| {
                let source = &group.source;
                (source.material, source.cull_backfaces, source.class)
            })
            .collect();
        assert_eq!(
            keys,
            [
                (0, true, MtdClass::Opaque),
                (1, false, MtdClass::AlphaTest),
                (2, false, MtdClass::Opaque),
            ]
        );
        let meshes: Vec<&[usize]> = groups.iter().map(|g| g.source.meshes.as_slice()).collect();
        assert_eq!(meshes, [&[0, 1][..], &[2], &[3]]);
        let vertices: Vec<&[usize]> = groups.iter().map(|g| g.vertices.as_slice()).collect();
        assert_eq!(
            vertices,
            [&[0, 1, 2, 6, 7, 8][..], &[3, 4, 5], &[9, 10, 11]]
        );
        assert_eq!(groups[0].triangles, [[0, 1, 2], [6, 7, 8], [2, 1, 0]]);
        assert_eq!(groups[1].triangles, [[3, 4, 5]]);
        assert_eq!(groups[1].source.mtd, "C_DullLeather[DSB]_Edge.mtd");
    }

    #[test]
    fn a_mesh_with_no_route_and_a_triangle_across_materials_are_refused() {
        let mut ghost = source();
        ghost.materials[2] = material("Ps_Wander_Ghost");
        let error = group_by_material(&export(&[0, 3], vec![[0, 1, 2]]), &ghost, "c2650")
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("mesh 3") && error.contains("no Elden Ring route"),
            "{error}"
        );

        let across = group_by_material(&export(&[0, 2], vec![[0, 1, 3]]), &source(), "c2650")
            .unwrap_err()
            .to_string();
        assert!(across.contains("spans two materials"), "{across}");
    }

    #[test]
    fn the_summary_names_each_slot_and_what_the_strip_left_out() {
        let slot = |donor_mesh, material, cull_backfaces, meshes: Vec<usize>, class| SlotReport {
            donor_mesh,
            material,
            cull_backfaces,
            source: Some(SlotSource {
                material: meshes[0],
                mtd: "C_DullLeather[DSB].mtd".to_owned(),
                class,
                meshes,
            }),
            vertices: 10,
            triangles: [7; 3],
        };
        let report = BuildReport {
            files: Vec::new(),
            body: BodyReport::Rebuilt {
                slots: vec![
                    slot(13, BODY_MATERIAL, true, vec![0, 1], MtdClass::Opaque),
                    slot(14, EDGE_MATERIAL, false, vec![2], MtdClass::AlphaTest),
                ],
                strip: Some(StripReport {
                    stripped_vertices: 817,
                    dropped_triangles: 811,
                    unreferenced_vertices: 45,
                    kept_vertices: 20,
                    kept_triangles: 14,
                }),
            },
            source_vertices: 20,
            source_triangles: 14,
            head_parts: 0,
            head_parts_without_model: 0,
        };
        assert_eq!(
            report.body_summary(),
            "body: 20 vertices and 14 triangles in 2 slots of a rebuilt donor, every other mesh \
             hidden\n  \
             mesh 13: 10 vertices, 7 triangles at every distance, Body material 0, back faces \
             culled; from DSR meshes 0, 1 on material 0 (C_DullLeather[DSB].mtd, opaque)\n  \
             mesh 14: 10 vertices, 7 triangles at every distance, Edge material 14, \
             double-sided; from DSR mesh 2 on material 2 (C_DullLeather[DSB].mtd, alpha-tested)\n  \
             held items: 817 vertices cut and the 811 triangles touching them; 45 vertices only \
             the far LODs drew left out"
        );
    }

    #[test]
    fn root_stem_drops_one_detail_suffix() {
        assert_eq!(root_stem("FC_F_0102_L"), "FC_F_0102");
        assert_eq!(root_stem("FC_M_0000"), "FC_M_0000");
        assert_eq!(root_stem("FG_A_0000_F"), "FG_A_0000");
    }
}
