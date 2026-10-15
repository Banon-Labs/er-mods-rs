//! A Dark Souls Remastered character laid onto Elden Ring's bind skeleton and weighted to Elden
//! Ring bone names: the [`SourceMesh`] the donor writers take.
//!
//! Every fit has the same shape. Each source vertex is carried by every bone that holds weight on
//! it, the way the game itself skins: each of those bones puts the vertex somewhere on Elden
//! Ring's body and names the Elden Ring bones its weight goes to, and the vertex lands at the
//! weighted mean of those places ([`place_all`]). What differs is how one bone carries a vertex,
//! which is a [`Placement`]:
//!
//! - [`mushroom`]: c2280's own child rig, one bone per leg and three-digit hands, laid onto the
//!   human joints in bands and arm segments, as the user approved it in game;
//! - [`biped`]: a 3ds Max Biped skeleton, mapped bone frame by bone frame onto Elden Ring's, with
//!   the trunk in [`axial`] bands.
//!
//! [`export`] picks the fit a [`Character`] names and also returns what the fit probe
//! (`scripts/dsr-er-fit-probe.py`) reads beside the OBJ and weight TSV: the resolved parameters
//! (`fit.json`) and, for every exported vertex, the source vertex it came from
//! (`source-map.tsv`).

pub mod axial;
pub mod biped;
pub mod mushroom;

use std::fmt::Write as _;

use crate::{
    character::{Character, FitConfig},
    error::{Error, Result},
    flver::{
        Vec2, Vec3,
        source::{DsrVertex, StripReport},
    },
};

/// Weights at or below this are noise in the source skin and are dropped.
const WEIGHT_FLOOR: f32 = 0.0001;
const UP: Vec3 = Vec3 {
    x: 0.0,
    y: 1.0,
    z: 0.0,
};
/// The sidecar names the fit probe looks for in an export directory.
pub const FIT_JSON: &str = "fit.json";
pub const SOURCE_MAP: &str = "source-map.tsv";
const FIT_JSON_FORMAT: u32 = 1;
const SOURCE_MAP_HEADER: &str = "export_vertex\tsource_mesh\tsource_vertex\tsource_bone\trole";

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SourceVertex {
    pub position: Vec3,
    pub normal: Vec3,
    pub uv: Vec2,
}

/// One weight of one vertex, against an Elden Ring bone name.
#[derive(Clone, Debug, PartialEq)]
pub struct WeightRow {
    pub vertex: usize,
    pub target_bone: String,
    pub weight: f32,
}

/// What the donor patcher consumes: final positions, triangles and Elden Ring bone weights.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SourceMesh {
    pub vertices: Vec<SourceVertex>,
    pub triangles: Vec<[u32; 3]>,
    pub weights: Vec<WeightRow>,
}

/// Where one source bone's weight carries a vertex.
#[derive(Clone, Debug)]
pub struct Carried {
    pub at: Vec3,
    /// The vertex normal turned with it, not yet normalised.
    pub normal: Vec3,
    /// The Elden Ring bones the weight goes to, and the share of it each takes.
    pub targets: Vec<(String, f32)>,
}

/// How one fit carries a source vertex by one of its bones.
pub trait Placement {
    /// Where `vertex` lands, and its normal, when no bone carries it. Passed to
    /// [`Placement::carry`] for every weight of the vertex, so a fit can work it out once.
    fn rest(&self, vertex: &DsrVertex) -> (Vec3, Vec3);

    /// Where the weight in bone slot `slot` of `vertex` carries it, given where it rests.
    fn carry(&self, vertex: &DsrVertex, slot: usize, rest: (Vec3, Vec3)) -> Result<Carried>;
}

/// Every vertex at the weighted mean of where its bones carry it, its normal the weighted mean of
/// theirs, and one weight row per bone and target, in the order the bones hold the vertex.
pub fn place_all(
    placement: &impl Placement,
    source: &[DsrVertex],
) -> Result<(Vec<SourceVertex>, Vec<WeightRow>)> {
    let mut vertices = Vec::with_capacity(source.len());
    let mut weights = Vec::new();
    for (vertex_index, vertex) in source.iter().enumerate() {
        let (rest, rest_normal) = placement.rest(vertex);
        let mut placed = Vec3::default();
        let mut normal = Vec3::default();
        let mut total = 0.0;
        for slot in 0..4 {
            let weight = vertex.bone_weights[slot];
            if weight <= WEIGHT_FLOOR {
                continue;
            }
            let carried = placement.carry(vertex, slot, (rest, rest_normal))?;
            placed = add(placed, mul(carried.at, weight));
            normal = add(
                normal,
                mul(normalized(carried.normal).unwrap_or(carried.normal), weight),
            );
            total += weight;
            for (target, share) in carried.targets {
                weights.push(WeightRow {
                    vertex: vertex_index,
                    target_bone: target,
                    weight: weight * share,
                });
            }
        }
        vertices.push(SourceVertex {
            position: if total > 0.0 {
                mul(placed, 1.0 / total)
            } else {
                rest
            },
            normal: normalized(normal)
                .or_else(|| normalized(rest_normal))
                .unwrap_or(vertex.normal),
            uv: vertex.uv,
        });
    }
    Ok((vertices, weights))
}

/// What the bone that dominates a source vertex is, as the fit probe classes it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MapRole {
    /// Pelvis, spine, neck, head, jaw.
    Trunk,
    /// Clavicle, upper arm, forearm and their twist bones.
    Arm,
    Hand,
    /// A finger or digit segment with another below it.
    Finger,
    /// The last segment of a finger or digit; a Nub is not a segment.
    FingerTip,
    /// Thigh, calf, foot, toe and their twist bones.
    Leg,
    /// A cloth or other helper bone the fit collapses onto an ancestor.
    Cloth,
    /// A held item's subtree.
    Prop,
    Other,
}

impl MapRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Trunk => "trunk",
            Self::Arm => "arm",
            Self::Hand => "hand",
            Self::Finger => "finger",
            Self::FingerTip => "finger_tip",
            Self::Leg => "leg",
            Self::Cloth => "cloth",
            Self::Prop => "prop",
            Self::Other => "other",
        }
    }
}

/// Where one exported vertex came from.
#[derive(Clone, Debug, PartialEq)]
pub struct SourceRow {
    /// The mesh of the source FLVER, every mesh counted in file order.
    pub mesh: usize,
    /// The vertex in that mesh's buffer, before any compaction.
    pub vertex: usize,
    /// The source bone holding the most weight on it.
    pub bone: String,
    pub role: MapRole,
}

/// A fitted character and what the fit probe needs to measure it.
#[derive(Clone, Debug)]
pub struct Export {
    pub character: &'static str,
    pub mesh: SourceMesh,
    /// The girth scale: a source edge times this is what the probe compares the fitted edge to.
    pub scale: f32,
    /// The fit's resolved parameters, each value already JSON.
    pub params: Vec<(&'static str, String)>,
    /// How far the fit stretched each source bone along its length.
    pub stretch: Vec<(String, f32)>,
    /// Where the fit put the source's right-hand weapon bone, when it has one.
    pub grip_anchor: Option<Vec3>,
    /// One row per vertex of `mesh`, in order.
    pub source_map: Vec<SourceRow>,
    /// What cutting the held items took out, for a fit that cuts them (the Biped one).
    pub strip: Option<StripReport>,
}

impl Export {
    /// `fit.json`, naming the OBJ and weight TSV written beside it.
    pub fn fit_json(&self, obj: &str, weights: &str) -> String {
        let params: Vec<String> = self
            .params
            .iter()
            .map(|(key, value)| format!("{}: {value}", json_string(key)))
            .collect();
        let stretch: Vec<String> = self
            .stretch
            .iter()
            .map(|(bone, stretch)| format!("{}: {}", json_string(bone), json_number(*stretch)))
            .collect();
        let grip = self.grip_anchor.map_or_else(
            || "null".to_owned(),
            |p| {
                format!(
                    "[{}, {}, {}]",
                    json_number(p.x),
                    json_number(p.y),
                    json_number(p.z)
                )
            },
        );
        format!(
            "{{\"format\": {FIT_JSON_FORMAT}, \"character\": {}, \"scale\": {}, \"params\": {{{}}}, \
             \"stretch\": {{{}}}, \"grip_anchor\": {grip}, \"obj\": {}, \"weights\": {}}}\n",
            json_string(self.character),
            json_number(self.scale),
            params.join(", "),
            stretch.join(", "),
            json_string(obj),
            json_string(weights),
        )
    }

    /// `source-map.tsv`: a header, then one row per exported vertex.
    pub fn source_map_tsv(&self) -> String {
        let mut out = format!("{SOURCE_MAP_HEADER}\n");
        for (index, row) in self.source_map.iter().enumerate() {
            let _ = writeln!(
                out,
                "{index}\t{}\t{}\t{}\t{}",
                row.mesh,
                row.vertex,
                row.bone,
                row.role.as_str()
            );
        }
        out
    }
}

/// Fit `character`'s FLVER to the donor's bind skeleton (Elden Ring's `FC_M_0000.flver`) the way
/// its table entry says.
pub fn export(character: &Character, flver: &[u8], donor: &[u8]) -> Result<Export> {
    let fitted = match character.fit {
        FitConfig::Mushroom(params) => mushroom::export(character.id, flver, donor, params),
        FitConfig::Biped(params) => biped::export(character.id, flver, donor, params),
    };
    let export = fitted.map_err(|e| e.context(format!("fitting {}", character.id)))?;
    if export.source_map.len() != export.mesh.vertices.len() {
        return Err(Error::new(format!(
            "{}: {} source-map rows for {} vertices",
            character.id,
            export.source_map.len(),
            export.mesh.vertices.len()
        )));
    }
    Ok(export)
}

/// The global bone holding the most weight on a vertex whose slots are already global, the
/// higher index on a tie: the fit probe's reading, so its cross-check of the source map agrees.
fn dominant_bone(bones: [usize; 4], weights: [f32; 4]) -> usize {
    let mut best = (weights[0], bones[0]);
    for (&weight, &bone) in weights.iter().zip(&bones).skip(1) {
        if weight > best.0 || (weight == best.0 && bone > best.1) {
            best = (weight, bone);
        }
    }
    best.1
}

fn json_string(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if u32::from(c) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", u32::from(c));
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn json_number(value: f32) -> String {
    if value.is_finite() {
        value.to_string()
    } else {
        "null".to_owned()
    }
}

fn add(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.x + b.x,
        y: a.y + b.y,
        z: a.z + b.z,
    }
}

fn sub(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.x - b.x,
        y: a.y - b.y,
        z: a.z - b.z,
    }
}

fn mul(a: Vec3, k: f32) -> Vec3 {
    Vec3 {
        x: a.x * k,
        y: a.y * k,
        z: a.z * k,
    }
}

fn dot(a: Vec3, b: Vec3) -> f32 {
    a.x * b.x + a.y * b.y + a.z * b.z
}

fn cross(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.y * b.z - a.z * b.y,
        y: a.z * b.x - a.x * b.z,
        z: a.x * b.y - a.y * b.x,
    }
}

fn normalized(a: Vec3) -> Option<Vec3> {
    let length = dot(a, a).sqrt();
    (length > f32::EPSILON).then(|| mul(a, 1.0 / length))
}

/// The shortest rotation taking unit vector `from` onto unit vector `to`, as a quaternion.
fn arc_rotation(from: Vec3, to: Vec3) -> [f32; 4] {
    let axis = cross(from, to);
    let w = 1.0 + dot(from, to);
    let length = (dot(axis, axis) + w * w).sqrt();
    [
        axis.x / length,
        axis.y / length,
        axis.z / length,
        w / length,
    ]
}

fn rotate(q: [f32; 4], v: Vec3) -> Vec3 {
    let [x, y, z, w] = q;
    let u = Vec3 { x, y, z };
    let t = mul(cross(u, v), 2.0);
    add(add(v, mul(t, w)), cross(u, t))
}

/// 0 at or below 0, 1 at or above 1, and a smooth ramp between.
fn smoothstep(x: f32) -> f32 {
    let t = x.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

impl SourceMesh {
    /// The OBJ the old exporter wrote (geometry lines only; its comment header is dropped).
    pub fn to_obj(&self) -> String {
        let mut out = String::new();
        for vertex in &self.vertices {
            let p = vertex.position;
            let _ = writeln!(out, "v {:.9} {:.9} {:.9}", p.x, p.y, p.z);
        }
        for vertex in &self.vertices {
            let _ = writeln!(out, "vt {:.9} {:.9}", vertex.uv.x, vertex.uv.y);
        }
        for vertex in &self.vertices {
            let n = vertex.normal;
            let _ = writeln!(out, "vn {:.9} {:.9} {:.9}", n.x, n.y, n.z);
        }
        for tri in &self.triangles {
            let _ = writeln!(
                out,
                "f {0}/{0}/{0} {1}/{1}/{1} {2}/{2}/{2}",
                tri[0] + 1,
                tri[1] + 1,
                tri[2] + 1
            );
        }
        out
    }

    /// The weight TSV's two columns the patcher reads, under the old header.
    pub fn to_weights_tsv(&self) -> String {
        let mut out = String::from(
            "vertex\tslot\tsource_mesh_bone_slot\tsource_global_bone_index\tsource_bone\ter_target_bone\tweight\n",
        );
        for row in &self.weights {
            let _ = writeln!(
                out,
                "{}\t-\t-\t-\t-\t{}\t{:.9}",
                row.vertex, row.target_bone, row.weight
            );
        }
        out
    }

    /// An OBJ (with `v`, `vt`, `vn` counts equal, one per vertex) and a weight TSV, as the
    /// Blender authoring export and the old Route A exporter both write them.
    pub fn from_obj_and_weights(obj: &str, weights_tsv: &str) -> Result<Self> {
        let mut positions = Vec::new();
        let mut normals = Vec::new();
        let mut uvs = Vec::new();
        let mut triangles = Vec::new();
        for line in obj.lines() {
            let mut parts = line.split_whitespace();
            let tag = parts.next();
            let mut float = |label: &str| -> Result<f32> {
                parts
                    .next()
                    .ok_or_else(|| Error::new(format!("OBJ line missing {label}: {line}")))?
                    .parse()
                    .map_err(|_| Error::new(format!("OBJ {label} is not a number: {line}")))
            };
            match tag {
                Some("v") => {
                    positions.push(Vec3 {
                        x: float("x")?,
                        y: float("y")?,
                        z: float("z")?,
                    });
                }
                Some("vn") => {
                    normals.push(Vec3 {
                        x: float("x")?,
                        y: float("y")?,
                        z: float("z")?,
                    });
                }
                Some("vt") => {
                    uvs.push(Vec2 {
                        x: float("u")?,
                        y: float("v")?,
                    });
                }
                Some("f") => {
                    let corners: Vec<u32> = line
                        .split_whitespace()
                        .skip(1)
                        .map(|token| {
                            token
                                .split('/')
                                .next()
                                .and_then(|index| index.parse::<u32>().ok())
                                .and_then(|index| index.checked_sub(1))
                                .ok_or_else(|| Error::new(format!("bad OBJ face: {line}")))
                        })
                        .collect::<Result<_>>()?;
                    if corners.len() != 3 {
                        return Err(Error::new(format!("OBJ face is not a triangle: {line}")));
                    }
                    triangles.push([corners[0], corners[1], corners[2]]);
                }
                _ => {}
            }
        }
        if positions.is_empty() || positions.len() != normals.len() || positions.len() != uvs.len()
        {
            return Err(Error::new(format!(
                "OBJ needs matching v/vn/vt counts, got v={} vn={} vt={}",
                positions.len(),
                normals.len(),
                uvs.len()
            )));
        }
        let vertices = positions
            .into_iter()
            .zip(normals)
            .zip(uvs)
            .map(|((position, normal), uv)| SourceVertex {
                position,
                normal,
                uv,
            })
            .collect();

        let mut weights = Vec::new();
        for (line_index, line) in weights_tsv.lines().enumerate() {
            if line_index == 0 || line.trim().is_empty() {
                continue;
            }
            let cols: Vec<&str> = line.split('\t').collect();
            if cols.len() < 7 {
                return Err(Error::new(format!(
                    "weight TSV line {} has {} columns, needs 7: {line}",
                    line_index + 1,
                    cols.len()
                )));
            }
            weights.push(WeightRow {
                vertex: cols[0]
                    .parse()
                    .map_err(|_| Error::new(format!("bad vertex index: {line}")))?,
                target_bone: cols[5].to_owned(),
                weight: cols[6]
                    .parse()
                    .map_err(|_| Error::new(format!("bad weight: {line}")))?,
            });
        }
        Ok(Self {
            vertices,
            triangles,
            weights,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn obj_and_weights_round_trip_through_the_text_the_scripts_wrote() {
        let mesh = SourceMesh {
            vertices: vec![
                SourceVertex {
                    position: Vec3 {
                        x: 0.5,
                        y: 1.0,
                        z: -0.25
                    },
                    normal: Vec3 {
                        x: 0.0,
                        y: 1.0,
                        z: 0.0
                    },
                    uv: Vec2 { x: 0.125, y: 0.75 },
                };
                3
            ],
            triangles: vec![[0, 1, 2]],
            weights: vec![WeightRow {
                vertex: 2,
                target_bone: "L_Hand".to_owned(),
                weight: 0.5,
            }],
        };
        let back =
            SourceMesh::from_obj_and_weights(&mesh.to_obj(), &mesh.to_weights_tsv()).unwrap();
        assert_eq!(back, mesh);
    }

    fn v(x: f32, y: f32, z: f32) -> Vec3 {
        Vec3 { x, y, z }
    }

    fn close(a: Vec3, b: Vec3) -> bool {
        let d = sub(a, b);
        dot(d, d).sqrt() < 1e-5
    }

    #[test]
    fn the_shortest_arc_takes_one_direction_onto_the_other() {
        let from = normalized(v(1.0, -0.4, 0.1)).unwrap();
        let to = normalized(v(0.7, -0.7, 0.05)).unwrap();
        assert!(close(rotate(arc_rotation(from, to), from), to));
    }

    /// Bone 0 lifts a vertex by 1 and weights it to `A`; bone 1 lifts it by 3 and splits its
    /// weight between `B` and `C`; bone 2 is not mapped.
    struct Lift;

    impl Placement for Lift {
        fn rest(&self, vertex: &DsrVertex) -> (Vec3, Vec3) {
            (vertex.position, v(0.0, 0.0, 1.0))
        }

        fn carry(&self, vertex: &DsrVertex, slot: usize, rest: (Vec3, Vec3)) -> Result<Carried> {
            let (lift, targets) = match vertex.bone_indices[slot] {
                0 => (1.0, vec![("A".to_owned(), 1.0)]),
                1 => (3.0, vec![("B".to_owned(), 0.25), ("C".to_owned(), 0.75)]),
                other => return Err(Error::new(format!("bone {other} is not mapped"))),
            };
            Ok(Carried {
                at: add(rest.0, v(0.0, lift, 0.0)),
                normal: v(0.0, lift, 0.0),
                targets,
            })
        }
    }

    #[test]
    fn a_vertex_lands_at_the_weighted_mean_of_where_its_bones_carry_it() {
        let vertex = |bones: [u16; 4], weights: [f32; 4]| DsrVertex {
            bone_indices: bones,
            bone_weights: weights,
            ..DsrVertex::default()
        };
        let (vertices, rows) = place_all(
            &Lift,
            &[
                vertex([0, 1, 2, 0], [0.5, 0.5, 0.0, 0.0]),
                // No weight at all: it stays where it rests.
                vertex([2, 2, 2, 2], [0.0; 4]),
            ],
        )
        .unwrap();
        assert!(close(vertices[0].position, v(0.0, 2.0, 0.0)));
        assert!(close(vertices[0].normal, v(0.0, 1.0, 0.0)));
        assert!(close(vertices[1].position, v(0.0, 0.0, 0.0)));
        assert!(close(vertices[1].normal, v(0.0, 0.0, 1.0)));
        let rows: Vec<(usize, &str, f32)> = rows
            .iter()
            .map(|row| (row.vertex, row.target_bone.as_str(), row.weight))
            .collect();
        assert_eq!(rows, [(0, "A", 0.5), (0, "B", 0.125), (0, "C", 0.375)]);
        // A weighted bone with nowhere to go is refused.
        assert!(place_all(&Lift, &[vertex([2, 0, 0, 0], [1.0, 0.0, 0.0, 0.0])]).is_err());
    }

    #[test]
    fn the_dominant_bone_is_the_heaviest_and_the_higher_index_on_a_tie() {
        assert_eq!(dominant_bone([3, 7, 0, 0], [0.25, 0.75, 0.0, 0.0]), 7);
        assert_eq!(dominant_bone([9, 4, 0, 0], [0.5, 0.5, 0.0, 0.0]), 9);
        assert_eq!(dominant_bone([2, 0, 0, 0], [1.0, 0.0, 0.0, 0.0]), 2);
    }

    #[test]
    fn the_sidecars_are_the_text_the_probe_reads() {
        let export = Export {
            character: "c2280",
            mesh: SourceMesh::default(),
            scale: 1.225,
            params: vec![
                ("scale", "1.225".to_owned()),
                ("thumb", "\"hand\"".to_owned()),
            ],
            stretch: vec![("L \"Hand\"".to_owned(), 0.5)],
            grip_anchor: None,
            source_map: vec![SourceRow {
                mesh: 1,
                vertex: 4,
                bone: "L Finger21".to_owned(),
                role: MapRole::FingerTip,
            }],
            strip: None,
        };
        assert_eq!(
            export.fit_json("a.obj", "a_weights.tsv"),
            "{\"format\": 1, \"character\": \"c2280\", \"scale\": 1.225, \"params\": \
             {\"scale\": 1.225, \"thumb\": \"hand\"}, \"stretch\": {\"L \\\"Hand\\\"\": 0.5}, \
             \"grip_anchor\": null, \"obj\": \"a.obj\", \"weights\": \"a_weights.tsv\"}\n"
        );
        let anchored = Export {
            grip_anchor: Some(v(-0.5, 1.0, 0.25)),
            ..export.clone()
        };
        assert!(
            anchored
                .fit_json("a", "b")
                .contains("\"grip_anchor\": [-0.5, 1, 0.25]")
        );
        assert_eq!(
            export.source_map_tsv(),
            "export_vertex\tsource_mesh\tsource_vertex\tsource_bone\trole\n\
             0\t1\t4\tL Finger21\tfinger_tip\n"
        );
    }
}
