//! `map-report`: what the builder would make of a DSR character's skeleton and materials, before
//! any fit is attempted.
//!
//! Per character it lists each weighted bone's [`Role`], the class of each material, what the
//! held-item strip removes, and the bind posture -- torso lean, upper-arm angle, toe direction,
//! pelvis height against Elden Ring's -- the four numbers that sorted the 74 Biped characters into
//! tiers. Two findings are errors: weight on a bone that maps to nothing, and a kept mesh whose
//! material has no Elden Ring route. The posture is a diagnostic only. Whether a character can be
//! built at all is decided by [`crate::character::CHARACTERS`], not by this report.

use std::fmt::{self, Write as _};

use crate::{
    error::Result,
    flver::{
        Vec3, bind_positions,
        mtd::{self, MtdClass},
        skeleton::{self, Role},
        source::{self, StripReport},
    },
};

/// Elden Ring's player pelvis height in its bind pose (`FC_M_0000.flver`, `Pelvis`).
const ER_PELVIS_HEIGHT: f32 = 0.97;

#[derive(Debug)]
pub struct WeightedBone {
    pub name: String,
    /// Total weight over every vertex of every buffer, in vertex-equivalents.
    pub weight: f32,
    pub role: Role,
    /// The Elden Ring bone it lands on, or what decides that.
    pub target: Option<String>,
}

#[derive(Debug)]
pub struct MaterialRow {
    pub mtd: String,
    pub class: MtdClass,
    /// Whether any mesh using it keeps a triangle after the strip.
    pub kept: bool,
}

/// The bind pose, measured off the left side.
#[derive(Clone, Copy, Debug, Default)]
pub struct Posture {
    /// Degrees between pelvis-to-neck and straight up.
    pub torso_lean: Option<f32>,
    /// Degrees the upper arm points below horizontal: about 0 for a T-pose, 45 for Elden Ring.
    pub upper_arm: Option<f32>,
    /// The toe's z against the ankle's; negative is forward.
    pub toe_dz: Option<f32>,
    /// Pelvis height over Elden Ring's.
    pub pelvis_scale: Option<f32>,
}

#[derive(Debug)]
pub struct MapReport {
    pub id: String,
    pub bones: usize,
    pub meshes: usize,
    pub vertices: usize,
    pub lod0_vertices: usize,
    pub lod0_triangles: usize,
    pub weighted: Vec<WeightedBone>,
    pub materials: Vec<MaterialRow>,
    pub strip: StripReport,
    pub posture: Posture,
    /// Weighted bones that map to nothing.
    pub unmapped: Vec<String>,
    /// Kept materials with no Elden Ring route.
    pub unrouted: Vec<String>,
    /// A skeleton the classifier refused outright.
    pub refused: Option<String>,
}

impl MapReport {
    pub fn has_geometry(&self) -> bool {
        self.vertices > 0
    }

    pub fn error_count(&self) -> usize {
        self.unmapped.len() + self.unrouted.len() + usize::from(self.refused.is_some())
    }
}

/// The report for one character's FLVER.
pub fn map_report(id: &str, flver: &[u8]) -> Result<MapReport> {
    let source = source::parse(flver)?;
    let mut report = MapReport {
        id: id.to_owned(),
        bones: source.bones.len(),
        meshes: source.meshes.len(),
        vertices: source.vertex_count(),
        lod0_vertices: source.lod0_vertex_count(),
        lod0_triangles: source.lod0_triangle_count(),
        weighted: Vec::new(),
        materials: Vec::new(),
        strip: StripReport::default(),
        posture: Posture::default(),
        unmapped: Vec::new(),
        unrouted: Vec::new(),
        refused: None,
    };
    let roles = match skeleton::classify(&source.bones) {
        Ok(roles) => roles,
        Err(error) => {
            report.refused = Some(error.to_string());
            return Ok(report);
        }
    };
    for (index, weight) in source.bone_weights().into_iter().enumerate() {
        if weight <= 0.0 {
            continue;
        }
        let role = roles[index].clone();
        let name = source.bones[index].name.clone();
        if role == Role::Unused {
            report.unmapped.push(format!(
                "{name:?} carries {weight:.1} vertex-weights and has no mapped ancestor"
            ));
        }
        report.weighted.push(WeightedBone {
            target: role.er_target(&roles),
            name,
            weight,
            role,
        });
    }
    let (kept, strip) = source::strip_props(&source, &roles);
    report.strip = strip;
    report.materials = source
        .materials
        .iter()
        .enumerate()
        .map(|(index, material)| MaterialRow {
            mtd: material
                .mtd
                .rsplit(['\\', '/'])
                .next()
                .unwrap_or_default()
                .to_owned(),
            class: mtd::classify(&material.mtd),
            kept: kept.iter().any(|mesh| mesh.material_index == index),
        })
        .collect();
    for (index, row) in report.materials.iter().enumerate() {
        if row.kept && row.class.route().is_none() {
            report.unrouted.push(format!(
                "material {index} ({}) is {:?}, which has no Elden Ring route",
                row.mtd, row.class
            ));
        }
    }
    report.posture = posture(&source.bones, &roles);
    Ok(report)
}

fn posture(bones: &[crate::flver::Bone], roles: &[Role]) -> Posture {
    let Ok(positions) = bind_positions(bones) else {
        return Posture::default();
    };
    let at = |target: &str| -> Option<Vec3> {
        roles
            .iter()
            .position(|role| matches!(role, Role::Direct(name) if name == target))
            .map(|index| positions[index])
    };
    let pelvis = at("Pelvis");
    let neck = at("Neck").or_else(|| at("Head"));
    let degrees = |radians: f32| radians.to_degrees();
    Posture {
        torso_lean: pelvis.zip(neck).and_then(|(pelvis, neck)| {
            let up = sub(neck, pelvis);
            let length = length(up);
            (length > f32::EPSILON).then(|| degrees((up.y / length).clamp(-1.0, 1.0).acos()))
        }),
        upper_arm: at("L_UpperArm")
            .zip(at("L_Forearm"))
            .and_then(|(shoulder, elbow)| {
                let arm = sub(elbow, shoulder);
                let length = length(arm);
                (length > f32::EPSILON).then(|| degrees((-arm.y / length).clamp(-1.0, 1.0).asin()))
            }),
        toe_dz: at("L_Foot")
            .zip(at("L_Toe0"))
            .map(|(foot, toe)| toe.z - foot.z),
        pelvis_scale: pelvis.map(|pelvis| pelvis.y / ER_PELVIS_HEIGHT),
    }
}

fn sub(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.x - b.x,
        y: a.y - b.y,
        z: a.z - b.z,
    }
}

fn length(a: Vec3) -> f32 {
    (a.x * a.x + a.y * a.y + a.z * a.z).sqrt()
}

impl Posture {
    /// `lean 0 arm 0 toe -Z pelvis 0.97`, with `?` for what the skeleton does not have.
    pub fn summary(&self) -> String {
        let number = |value: Option<f32>, decimals: usize| {
            value.map_or_else(|| "?".to_owned(), |v| format!("{v:.decimals$}"))
        };
        let toe = match self.toe_dz {
            Some(dz) if dz < 0.0 => "-Z",
            Some(_) => "+Z",
            None => "?",
        };
        format!(
            "lean {} arm {} toe {toe} pelvis {}",
            number(self.torso_lean, 0),
            number(self.upper_arm, 0),
            number(self.pelvis_scale, 2)
        )
    }
}

impl MapReport {
    /// The one line `--all-biped` prints per character.
    pub fn summary_line(&self) -> String {
        if !self.has_geometry() {
            return format!("{:<6} no geometry", self.id);
        }
        let verdict = match self.error_count() {
            0 => "clean".to_owned(),
            n => format!("{n} error{}", if n == 1 { "" } else { "s" }),
        };
        let mut kinds = Vec::new();
        if !self.unmapped.is_empty() {
            kinds.push(format!("unmapped {}", self.unmapped.len()));
        }
        if !self.unrouted.is_empty() {
            kinds.push(format!("unrouted {}", self.unrouted.len()));
        }
        if self.refused.is_some() {
            kinds.push("refused".to_owned());
        }
        format!(
            "{:<6} {:<9} {:<34} kept {}v/{}t of {}v/{}t {}",
            self.id,
            verdict,
            self.posture.summary(),
            self.strip.kept_vertices,
            self.strip.kept_triangles,
            self.lod0_vertices,
            self.lod0_triangles,
            kinds.join(", ")
        )
    }
}

impl fmt::Display for MapReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut out = String::new();
        let _ = writeln!(
            out,
            "{}: {} bones, {} meshes, {} vertices ({} used by LOD0), {} LOD0 triangles",
            self.id,
            self.bones,
            self.meshes,
            self.vertices,
            self.lod0_vertices,
            self.lod0_triangles
        );
        let _ = writeln!(out, "  posture: {}", self.posture.summary());
        if let Some(refused) = &self.refused {
            let _ = writeln!(out, "  error: skeleton refused: {refused}");
            return f.write_str(&out);
        }
        let s = self.strip;
        let _ = writeln!(
            out,
            "  strip: {} held-item vertices, {} LOD0 triangles dropped; kept {} vertices, {} \
             triangles ({} LOD1/LOD2-only vertices dropped)",
            s.stripped_vertices,
            s.dropped_triangles,
            s.kept_vertices,
            s.kept_triangles,
            s.unreferenced_vertices
        );
        let _ = writeln!(out, "  weighted bones:");
        for bone in &self.weighted {
            let _ = writeln!(
                out,
                "    {:<24} {:>8.1}  {:<16} {}",
                format!("{:?}", bone.name),
                bone.weight,
                role_label(&bone.role),
                bone.target.as_deref().unwrap_or("-")
            );
        }
        let _ = writeln!(out, "  materials:");
        for (index, material) in self.materials.iter().enumerate() {
            let _ = writeln!(
                out,
                "    {index:<3} {:<40} {:<12} {}{}",
                material.mtd,
                format!("{:?}", material.class),
                material.class.route().unwrap_or("no route"),
                if material.kept { "" } else { " (stripped)" }
            );
        }
        for error in self.unmapped.iter().chain(&self.unrouted) {
            let _ = writeln!(out, "  error: {error}");
        }
        f.write_str(&out)
    }
}

fn role_label(role: &Role) -> String {
    match role {
        Role::Direct(_) => "direct".to_owned(),
        Role::SpineChain(rank) => format!("spine {rank}"),
        Role::Twist(core) => format!("twist of #{core}"),
        Role::Prop => "held item".to_owned(),
        Role::Unused => "unused".to_owned(),
        Role::Ancestor(index) => format!("ancestor #{index}"),
    }
}
