//! Pack new map-asset geometry into an Elden Ring asset the game already has, and strip one
//! material (ivy) out of other assets, writing an ME3 package folder.
//!
//! ```text
//! er-asset-pack --game <ELDEN RING/Game> --out <package dir>
//!     [--mesh <mesh.txt> --donor AEG004_501 --carrier AEG004_545]
//!     [--strip <material prefix> AEG004_501 AEG007_558 ...]
//! ```
//!
//! `--mesh` geometry is drawn through the donor's own materials: each `mesh <material>` block
//! replaces the donor mesh drawing that material, every other donor mesh is hidden, and the
//! FLVER that results goes into the carrier's binder in place of the carrier's own model. The
//! carrier should be an asset no map places (`scripts/er-asset-placement-census.py`), so the
//! override changes nothing already in the world and the game still has its param row for
//! spawning it.
//!
//! The mesh file is text, already in the FLVER's frame (left-handed, winding as the game
//! draws it): `mesh <material>`, then `v px py pz nx ny nz tx ty tz tw u v` lines and
//! `f a b c` lines with zero-based indices into that block's vertices. Every vertex is
//! encoded over a copy of the donor mesh's first vertex, so members this tool does not
//! compute (color, a second UV) keep the donor's values.
//!
//! `--strip` zeroes every face set of every mesh whose material name starts with the prefix,
//! in every FLVER of each named asset's binder: the meshes stay, drawing nothing.
//!
//! Kraken needs the game's Oodle DLL, so this runs as the Windows build (under wine here).

use std::{collections::BTreeMap, env, fs, path::Path};

use mushroom_man_builder::{
    archive::ArchiveSet,
    bnd::Bnd4,
    dcx,
    error::{Error, Result},
    flver::{
        Flver, Vec3, index_range, parse_materials, parse_textures, parse_u32_list,
        rebuild::{HeaderAppend, Obb, Slot, material_template, rebuild},
    },
    oodle,
};

struct Vertex {
    position: [f32; 3],
    normal: [f32; 3],
    tangent: [f32; 4],
    uv: [f32; 2],
}

struct MeshBlock {
    material: String,
    vertices: Vec<Vertex>,
    triangles: Vec<[u32; 3]>,
}

fn read_mesh(path: &str) -> Result<Vec<MeshBlock>> {
    let text = fs::read_to_string(path).map_err(|e| Error::new(format!("{path}: {e}")))?;
    let mut blocks: Vec<MeshBlock> = Vec::new();
    for (number, line) in text.lines().enumerate() {
        let mut words = line.split_whitespace();
        let bad = || Error::new(format!("{path}:{}: {line}", number + 1));
        match words.next() {
            Some("mesh") => blocks.push(MeshBlock {
                material: words.next().ok_or_else(bad)?.to_string(),
                vertices: Vec::new(),
                triangles: Vec::new(),
            }),
            Some("v") => {
                let n: Vec<f32> = words
                    .map(str::parse)
                    .collect::<std::result::Result<_, _>>()
                    .map_err(|_| bad())?;
                if n.len() != 12 {
                    return Err(bad());
                }
                blocks.last_mut().ok_or_else(bad)?.vertices.push(Vertex {
                    position: [n[0], n[1], n[2]],
                    normal: [n[3], n[4], n[5]],
                    tangent: [n[6], n[7], n[8], n[9]],
                    uv: [n[10], n[11]],
                });
            }
            Some("f") => {
                let n: Vec<u32> = words
                    .map(str::parse)
                    .collect::<std::result::Result<_, _>>()
                    .map_err(|_| bad())?;
                let [a, b, c] = n[..] else { return Err(bad()) };
                blocks.last_mut().ok_or_else(bad)?.triangles.push([a, b, c]);
            }
            None => {}
            Some(_) => return Err(bad()),
        }
    }
    Ok(blocks)
}

/// A unit component as the biased byte FLVER format 0x11 stores: 127 is zero.
fn biased(value: f32) -> u8 {
    (value.clamp(-1.0, 1.0) * 127.0 + 127.0).round() as u8
}

/// The donor mesh's buffer layout must be the one this encoder writes: position f32x3 at 0,
/// normal and tangent as biased bytes at 12 and 16, UV0 as two i16 at 24.
fn encode(block: &MeshBlock, template: &[u8], uv_factor: f32) -> Vec<u8> {
    let mut out = Vec::with_capacity(template.len() * block.vertices.len());
    for v in &block.vertices {
        let mut bytes = template.to_vec();
        for (i, p) in v.position.iter().enumerate() {
            bytes[i * 4..i * 4 + 4].copy_from_slice(&p.to_le_bytes());
        }
        for i in 0..3 {
            bytes[12 + i] = biased(v.normal[i]);
            bytes[16 + i] = biased(v.tangent[i]);
        }
        bytes[19] = biased(v.tangent[3]);
        for i in 0..2 {
            let scaled = (v.uv[i] * uv_factor)
                .round()
                .clamp(i16::MIN as f32, i16::MAX as f32) as i16;
            bytes[24 + i * 2..26 + i * 2].copy_from_slice(&scaled.to_le_bytes());
        }
        out.extend_from_slice(&bytes);
    }
    out
}

/// A material the donor lacks, taken from another FLVER: drawn through a donor mesh no block
/// uses, whose material entry is pointed at the lender's (name, matbin, textures, GX list).
struct Loan {
    material: String,
    lender: Vec<u8>,
    spare: String,
}

fn build_flver(donor: &[u8], blocks: &[MeshBlock], loans: &[Loan]) -> Result<Vec<u8>> {
    let flver = Flver::parse(donor)?;
    let materials = parse_materials(donor, &flver)?;
    let uv_factor = flver.header.uv_factor();
    let mut append: Option<HeaderAppend> = None;
    let mut lent: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for loan in loans {
        if blocks.iter().any(|b| b.material == loan.spare) {
            return Err(Error::new(format!(
                "spare material {} is drawn by a block",
                loan.spare
            )));
        }
        let index = materials
            .iter()
            .position(|m| m.name == loan.spare)
            .ok_or_else(|| Error::new(format!("donor has no material {}", loan.spare)))?;
        let lender = Flver::parse(&loan.lender)?;
        let lender_materials = parse_materials(&loan.lender, &lender)?;
        let from = lender_materials
            .iter()
            .position(|m| m.name == loan.material)
            .ok_or_else(|| Error::new(format!("lender has no material {}", loan.material)))?;
        let template = material_template(&loan.lender, from)?;
        let textures = parse_textures(&loan.lender, &lender)?;
        let own = &textures[lender_materials[from].texture_index..]
            [..lender_materials[from].texture_count];
        if append.is_none() {
            append = Some(HeaderAppend::for_donor(donor)?);
        }
        let keep = own.len().min(materials[index].texture_count);
        append.as_mut().unwrap().retarget_material(
            index,
            &loan.material,
            &template.mtd,
            &own[..keep],
            &template.gx,
            template.flags,
        )?;
        println!(
            "loan: {} drawn through donor material {index} ({})",
            loan.material, loan.spare
        );
        lent.entry(loan.material.clone()).or_default().push(index);
    }
    let mut slots = Vec::new();
    // An asset FLVER keeps one run of meshes per LOD (face set flags 0, 0x01000000,
    // 0x02000000 on AEG004_506), and shadows and distance draw the lower ones. So a block
    // fills every donor mesh drawing its material, at every LOD the donor has one.
    let mut owner_meshes: Vec<(usize, &mushroom_man_builder::flver::Mesh, &MeshBlock)> = Vec::new();
    for block in blocks {
        let before = owner_meshes.len();
        for (mesh_index, mesh) in flver.meshes.iter().enumerate() {
            let draws = match lent.get(&block.material) {
                Some(indices) => indices.contains(&(mesh.material_index as usize)),
                None => materials[mesh.material_index as usize].name == block.material,
            };
            if draws {
                owner_meshes.push((mesh_index, mesh, block));
            }
        }
        if owner_meshes.len() == before {
            return Err(Error::new(format!(
                "donor has no mesh drawing {}",
                block.material
            )));
        }
    }
    for (mesh_index, mesh, block) in owner_meshes {
        let buffer_index =
            parse_u32_list(donor, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)?[0] as usize;
        let buffer = flver.vertex_buffers[buffer_index];
        let start = flver.header.data_offset + buffer.buffer_offset;
        let template = &donor[start..start + buffer.vertex_size];
        let obb = Obb::around(block.vertices.iter().map(|v| Vec3 {
            x: v.position[0],
            y: v.position[1],
            z: v.position[2],
        }))
        .ok_or_else(|| Error::new(format!("{} has no vertices", block.material)))?;
        slots.push(Slot {
            donor_mesh: mesh_index,
            material: mesh.material_index,
            cull_backfaces: true,
            vertex_count: block.vertices.len(),
            vertex_bytes: encode(block, template, uv_factor),
            layout: None,
            lods: [
                block.triangles.clone(),
                block.triangles.clone(),
                block.triangles.clone(),
            ],
            obb,
        });
        println!(
            "slot: {} -> donor mesh {mesh_index}, {} vertices, {} triangles, stride {}",
            block.material,
            block.vertices.len(),
            block.triangles.len(),
            buffer.vertex_size
        );
    }
    let mut out = rebuild(donor, &slots, true, &append.unwrap_or_default())?;
    widen_bone_boxes(&mut out)?;
    Ok(out)
}

/// FLVER header layout: counts from 0x14, the model box at 0x28/0x34, then the header (0x80),
/// dummies (0x40 each) and materials (0x20 each) before the bone table, whose entries (0x80) keep
/// a box at 0x30/0x40. The engine culls a map asset by its bone boxes, not by the header box:
/// with the donor tower's bone box left as it was, the church vanished whenever the tower's
/// footprint left the view (user report 2026-10-16). So every bone box is grown to the model's.
fn widen_bone_boxes(bytes: &mut [u8]) -> Result<()> {
    let count = |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap()) as usize;
    let (dummies, materials, bones) = (count(0x14), count(0x18), count(0x1c));
    let model_min: Vec<f32> = (0..3).map(|i| get_f32(bytes, 0x28 + i * 4)).collect();
    let model_max: Vec<f32> = (0..3).map(|i| get_f32(bytes, 0x34 + i * 4)).collect();
    let table = 0x80 + dummies * 0x40 + materials * 0x20;
    for bone in 0..bones {
        let at = table + bone * 0x80;
        for i in 0..3 {
            let lo = get_f32(bytes, at + 0x30 + i * 4).min(model_min[i]);
            let hi = get_f32(bytes, at + 0x40 + i * 4).max(model_max[i]);
            put_f32(bytes, at + 0x30 + i * 4, lo);
            put_f32(bytes, at + 0x40 + i * 4, hi);
        }
    }
    if bones == 0 {
        return Err(Error::new("model has no bones to carry its cull box"));
    }
    println!("bone boxes: {bones} grown to {model_min:?}..{model_max:?}");
    Ok(())
}

fn strip(bytes: &mut [u8], prefix: &str) -> Result<usize> {
    let flver = Flver::parse(bytes)?;
    let materials = parse_materials(bytes, &flver)?;
    let mut emptied = 0;
    for mesh in &flver.meshes {
        if !materials[mesh.material_index as usize]
            .name
            .starts_with(prefix)
        {
            continue;
        }
        for face_set in parse_u32_list(bytes, mesh.face_set_offset, mesh.face_set_count)? {
            let range = index_range(bytes, &flver, &flver.face_sets[face_set as usize])?;
            bytes[range].fill(0);
            emptied += 1;
        }
    }
    Ok(emptied)
}

/// `AEG099_002_L.hkx`, the invisible wall's box, read at known offsets. Measured on the 1.17
/// extraction: half extents in the w of three axis rows at 0x350, the centre at 0x380, the eight
/// corners from 0x390, the six planes (normal, d = -max(n . v)) from 0x410, and the half extents
/// less the convex radius in the w of three rows at 0x4d0.
const BOX_AXES: usize = 0x350;
const BOX_CENTRE: usize = 0x380;
const BOX_CORNERS: usize = 0x390;
const BOX_PLANES: usize = 0x410;
const BOX_INNER: usize = 0x4d0;
const BOX_DONOR_CORNER: [f32; 3] = [7.459, 8.343, 0.065];

fn put_f32(bytes: &mut [u8], at: usize, value: f32) {
    bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
}

fn get_f32(bytes: &[u8], at: usize) -> f32 {
    f32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}

/// The donor box rewritten to span `min..max`, refusing a file whose first corner is not the one
/// the offsets above were measured on.
fn box_hkx(donor: &[u8], min: [f32; 3], max: [f32; 3]) -> Result<Vec<u8>> {
    for (i, want) in BOX_DONOR_CORNER.iter().enumerate() {
        let got = get_f32(donor, BOX_CORNERS + i * 4);
        if (got - want).abs() > 0.01 {
            return Err(Error::new(format!(
                "box donor corner {i} is {got}, not {want}: layout changed"
            )));
        }
    }
    let mut out = donor.to_vec();
    let half: Vec<f32> = (0..3).map(|i| (max[i] - min[i]) / 2.0).collect();
    let centre: Vec<f32> = (0..3).map(|i| (max[i] + min[i]) / 2.0).collect();
    for i in 0..3 {
        let donor_half = get_f32(donor, BOX_AXES + i * 16 + 12);
        let donor_inner = get_f32(donor, BOX_INNER + i * 16 + 12);
        put_f32(&mut out, BOX_AXES + i * 16 + 12, half[i]);
        put_f32(&mut out, BOX_CENTRE + i * 4, centre[i]);
        put_f32(
            &mut out,
            BOX_INNER + i * 16 + 12,
            donor_inner * half[i] / donor_half,
        );
    }
    // Corners in the donor's order: x high/low fastest, then y high/low, then z high/low.
    for corner in 0..8 {
        let x = if corner & 1 == 0 { max[0] } else { min[0] };
        let y = if corner & 2 == 0 { max[1] } else { min[1] };
        let z = if corner & 4 == 0 { max[2] } else { min[2] };
        for (i, v) in [x, y, z].into_iter().enumerate() {
            put_f32(&mut out, BOX_CORNERS + corner * 16 + i * 4, v);
        }
    }
    let ds = [-max[0], min[0], -max[1], min[1], -max[2], min[2]];
    for (plane, d) in ds.into_iter().enumerate() {
        put_f32(&mut out, BOX_PLANES + plane * 16 + 12, d);
    }
    Ok(out)
}

fn geombnd_path(asset: &str) -> String {
    let asset = asset.to_lowercase();
    format!("/asset/aeg/{}/{asset}.geombnd.dcx", &asset[..6])
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    let mut options: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    let mut current = "";
    for arg in &args {
        if let Some(name) = arg.strip_prefix("--") {
            current = name;
            options.entry(current).or_default();
        } else {
            options.entry(current).or_default().push(arg);
        }
    }
    let one = |name: &str| -> Result<&str> {
        options
            .get(name)
            .and_then(|v| v.first().copied())
            .ok_or_else(|| Error::new(format!("--{name} is required")))
    };
    let game = Path::new(one("game")?);
    let out = Path::new(one("out")?);
    let oodle = oodle::load(game)?;
    let archives = ArchiveSet::open(game)?;
    let write = |logical: &str, source: &[u8], binder: &Bnd4| -> Result<()> {
        let packed = dcx::recompress_like(source, &binder.write()?, Some(&oodle))?;
        let path = out.join(logical.trim_start_matches('/'));
        fs::create_dir_all(path.parent().unwrap())
            .map_err(|e| Error::new(format!("{}: {e}", path.display())))?;
        fs::write(&path, &packed).map_err(|e| Error::new(format!("{}: {e}", path.display())))?;
        println!("wrote {} ({} bytes)", path.display(), packed.len());
        Ok(())
    };

    let main_flver = |asset: &str| -> Result<Vec<u8>> {
        let binder = Bnd4::read(&dcx::decompress(
            &archives.read(&geombnd_path(asset))?,
            Some(&oodle),
        )?)?;
        Ok(binder
            .file_named(&format!("{}.flver", asset.to_uppercase()))
            .ok_or_else(|| Error::new(format!("{asset} binder has no main FLVER")))?
            .data
            .clone())
    };
    if let Some(files) = options.get("mesh") {
        // Several mesh files go into one model; blocks drawing the same material merge.
        let mut blocks: Vec<MeshBlock> = Vec::new();
        for file in files {
            for block in read_mesh(file)? {
                match blocks.iter_mut().find(|b| b.material == block.material) {
                    Some(into) => {
                        let base = into.vertices.len() as u32;
                        into.vertices.extend(block.vertices);
                        into.triangles
                            .extend(block.triangles.iter().map(|t| t.map(|i| i + base)));
                    }
                    None => blocks.push(block),
                }
            }
        }
        // `--loan <material> <lender asset> <spare donor material>`, repeatable as triples.
        let mut loans = Vec::new();
        for triple in options
            .get("loan")
            .map(Vec::as_slice)
            .unwrap_or_default()
            .chunks(3)
        {
            let [material, lender, spare] = triple else {
                return Err(Error::new("--loan takes material, lender, spare"));
            };
            loans.push(Loan {
                material: material.to_string(),
                lender: main_flver(lender)?,
                spare: spare.to_string(),
            });
        }
        let donor_name = one("donor")?;
        let carrier = one("carrier")?;
        let donor = &main_flver(donor_name)?;
        let built = build_flver(donor, &blocks, &loans)?;
        // The bare FLVER beside the package, for checking with a second parser.
        let check = out.with_extension("flver");
        fs::write(&check, &built).map_err(|e| Error::new(format!("{}: {e}", check.display())))?;
        let logical = geombnd_path(carrier);
        let source = archives.read(&logical)?;
        let mut binder = Bnd4::read(&dcx::decompress(&source, Some(&oodle))?)?;
        let name = format!("{}.flver", carrier.to_uppercase());
        binder
            .file_named_mut(&name)
            .ok_or_else(|| Error::new(format!("{carrier} binder has no {name}")))?
            .data = built;
        write(&logical, &source, &binder)?;
    }

    // `--hull <carrier> minx miny minz maxx maxy maxz`: every Havok file in the carrier's
    // collision binder becomes the invisible wall's box spanning those bounds, in the model's
    // frame. Placement can switch the collision off; the box is there so the engine's notion of
    // where the asset is matches the model drawn there.
    if let Some(list) = options.get("hull") {
        let (carrier, bounds) = list
            .split_first()
            .ok_or_else(|| Error::new("--hull needs a carrier"))?;
        let n: Vec<f32> = bounds
            .iter()
            .map(|s| s.parse())
            .collect::<std::result::Result<_, _>>()
            .map_err(|_| Error::new("--hull bounds are numbers"))?;
        let [x0, y0, z0, x1, y1, z1] = n[..] else {
            return Err(Error::new("--hull needs six bounds"));
        };
        let donor_logical = "/asset/aeg/aeg099/aeg099_002_l.geomhkxbnd.dcx";
        let donor_binder = Bnd4::read(&dcx::decompress(
            &archives.read(donor_logical)?,
            Some(&oodle),
        )?)?;
        let donor = &donor_binder
            .file_named("AEG099_002_L.hkx")
            .ok_or_else(|| Error::new("no AEG099_002_L.hkx"))?
            .data;
        let shape = box_hkx(donor, [x0, y0, z0], [x1, y1, z1])?;
        let lower = carrier.to_lowercase();
        let logical = format!("/asset/aeg/{}/{lower}_l.geomhkxbnd.dcx", &lower[..6]);
        let source = archives.read(&logical)?;
        let mut binder = Bnd4::read(&dcx::decompress(&source, Some(&oodle))?)?;
        for file in binder.files.iter_mut() {
            println!(
                "hull: {} <- box {:?}..{:?}",
                file.name,
                [x0, y0, z0],
                [x1, y1, z1]
            );
            file.data = shape.clone();
        }
        write(&logical, &source, &binder)?;
    }

    if let Some(list) = options.get("strip") {
        let (prefix, assets) = list
            .split_first()
            .ok_or_else(|| Error::new("--strip needs a material prefix"))?;
        for asset in assets {
            let logical = geombnd_path(asset);
            let source = archives.read(&logical)?;
            let mut binder = Bnd4::read(&dcx::decompress(&source, Some(&oodle))?)?;
            let mut emptied = 0;
            for file in binder
                .files
                .iter_mut()
                .filter(|f| f.name.to_lowercase().ends_with(".flver"))
            {
                emptied += strip(&mut file.data, prefix)?;
            }
            println!("{asset}: {emptied} face sets drawing {prefix}* emptied");
            write(&logical, &source, &binder)?;
        }
    }
    Ok(())
}
