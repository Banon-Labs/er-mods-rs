//! A 3ds Max Biped character laid onto Elden Ring's bind skeleton, bone frame by bone frame.
//!
//! A Biped and Elden Ring's player skeleton both point every limb bone's X axis down the limb, and
//! after turning X onto X the two rolls differ by under 5 degrees on every arm, leg and long finger
//! bone of c2650 (the thumb by 31). So each limb bone carries its vertices by a frame map
//! ([`LimbMap`]): a point's coordinates in the source bone's frame are stretched along the bone to
//! the Elden Ring bone's length, scaled across by the girth `k`, and read back in the Elden Ring
//! bone's frame. That one map turns c2650's T-pose into Elden Ring's A-pose (the arms swing 45
//! degrees) and straightens its pre-curled fingers onto Elden Ring's straight ones, which the
//! game's grip then curls; carried rigidly with the hand they curled twice.
//!
//! The rest, as measured against `scripts/dsr-er-fit-probe.py` on c2650:
//!
//! - `k` is Elden Ring's pelvis height over the source's, 1.027 for c2650, where c2280 needed a
//!   hand-tuned 1.225; every joint-on-joint stretch comes out near 1 with it;
//! - a bone's length runs from its joint to its mapped child's along X; the hand's runs to the
//!   knuckle line, the middle of the long fingers' roots; a last segment stretches by `k`;
//! - the pelvis, spine, neck and head are laid upright by [`AxialFit`], knotted at the pelvis and
//!   the shoulder, and spine weight is shared among Elden Ring's `Pelvis`, `Spine`, `Spine1` and
//!   `Spine2` by fitted height, blending 0.03 m either side of each joint: c2650's two spine links
//!   span 1.07..1.52 m against Elden Ring's three joints at 1.06..1.24 m, so knots by name would
//!   squash the belly to half;
//! - the thumb rides the hand map and is weighted to Elden Ring's thumb ([`Thumb::Hand`]):
//!   mapped frame by frame its root turned 14 triangles inside out at bind;
//! - a twist link moves with its core bone and, by default, weighs on it too ([`Twist`]); cloth
//!   moves and weighs with the Biped bone it hangs from ([`Cloth`]); a held item is cut
//!   ([`HeldItems`]), and a vertex left with a little of its weight is carried by the hand;
//! - the left side must be at +X and the toes at -Z of the ankles, as Elden Ring's are: nothing
//!   is mirrored.

use std::collections::HashMap;

use super::{
    Carried, Export, MapRole, Placement, SourceMesh, SourceRow, UP, WEIGHT_FLOOR, WeightRow, add,
    axial::AxialFit, dominant_bone, dot, mul, normalized, place_all, smoothstep, sub,
};
use crate::{
    error::{Error, Result},
    flver::{
        Bone, Flver, Frame, Vec3, bind_frames,
        skeleton::{self, Role},
        source::{self, DsrVertex},
    },
};

/// Metres either side of an Elden Ring spine joint over which two spine bones share weight.
const TRUNK_BLEND: f32 = 0.03;
/// Elden Ring's pelvis and spine bones, bottom up, that spine weight is shared among.
const TRUNK: [&str; 4] = ["Pelvis", "Spine", "Spine1", "Spine2"];
const SIDES: [&str; 2] = ["L", "R"];

/// What carries the thumb.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Thumb {
    /// The hand's frame map, with the weight on Elden Ring's thumb bones.
    Hand,
    /// Its own frame maps, like the long fingers.
    Frame,
}

/// What becomes of a held item and everything hanging from it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HeldItems {
    /// Cut vertex by vertex: c2650's lantern is 817 of its 2607 vertices.
    Strip,
    /// Kept, carried and weighted by the bone that holds it.
    Keep,
}

/// Where the weight of a cloth chain goes. It moves with the Biped bone it hangs from either way.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cloth {
    /// The bone it hangs from: c2650's coat hem stays on the calves, which is DSR's own look with
    /// its cloth physics frozen.
    Ancestor,
    /// A chain hanging from a calf weighs on the thigh above. Measured worse on c2650 (idle, R1
    /// and running R1 worst frames 109, 465 and 510 stretched edges, against 100, 451 and 482);
    /// kept as the other half of an A/B.
    Thigh,
}

/// Where a twist link's weight goes. It moves with its core bone either way.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Twist {
    /// The core bone itself: `L ForeTwist` weighs on `L_Forearm`.
    Main,
    /// Elden Ring's own twist bone of that segment: `L ForeTwist` weighs on `L_ForeArmTwist`.
    ErTwist,
}

/// How a Biped character is fitted.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BipedParams {
    /// The girth scale `k`. `None` derives it as Elden Ring's pelvis height over the source's.
    pub scale: Option<f32>,
    /// Metres the elbow leans up and out across Elden Ring's arm axis, fading to nothing at the
    /// shoulder and the hand, as the mushroom's does. c2650 needs none.
    pub arm_lean: f32,
    pub thumb: Thumb,
    pub held_items: HeldItems,
    pub cloth: Cloth,
    pub twist: Twist,
    /// Knot the body at the head joint too, so the neck and head land on Elden Ring's pivots.
    /// For c2650 that stretches the neck about 1.5 times; without it the head joint sits about
    /// 0.05 m below Elden Ring's.
    pub head_band: bool,
}

impl BipedParams {
    /// The fit c2650 was measured with.
    pub const DEFAULT: Self = Self {
        scale: None,
        arm_lean: 0.0,
        thumb: Thumb::Hand,
        held_items: HeldItems::Strip,
        cloth: Cloth::Ancestor,
        twist: Twist::Main,
        head_band: false,
    };

    /// The parameters as `fit.json` records them, with the girth this fit resolved.
    fn json(&self, girth: f32) -> Vec<(&'static str, String)> {
        let word = |text: &str| format!("\"{text}\"");
        vec![
            ("scale", girth.to_string()),
            ("scale_derived", self.scale.is_none().to_string()),
            ("arm_lean", self.arm_lean.to_string()),
            (
                "thumb",
                word(match self.thumb {
                    Thumb::Hand => "hand",
                    Thumb::Frame => "frame",
                }),
            ),
            (
                "held_items",
                word(match self.held_items {
                    HeldItems::Strip => "strip",
                    HeldItems::Keep => "keep",
                }),
            ),
            (
                "cloth",
                word(match self.cloth {
                    Cloth::Ancestor => "ancestor",
                    Cloth::Thigh => "thigh",
                }),
            ),
            (
                "twist",
                word(match self.twist {
                    Twist::Main => "main",
                    Twist::ErTwist => "er_twist",
                }),
            ),
            ("head_band", self.head_band.to_string()),
        ]
    }
}

impl Default for BipedParams {
    fn default() -> Self {
        Self::DEFAULT
    }
}

/// One source limb bone laid onto its Elden Ring bone.
#[derive(Clone, Copy, Debug, PartialEq)]
struct LimbMap {
    from: Vec3,
    /// The source bone's axes, unit length.
    from_axes: [Vec3; 3],
    onto: Vec3,
    onto_axes: [Vec3; 3],
    /// Along the bone, then across it twice.
    scale: [f32; 3],
    /// The side whose arm lean pushes what this map places: upper arms and forearms only.
    lean: Option<usize>,
}

impl LimbMap {
    fn new(source: Frame, er: Frame, stretch: f32, girth: f32) -> Option<Self> {
        let unit = |[x, y, z]: [Vec3; 3]| Some([normalized(x)?, normalized(y)?, normalized(z)?]);
        Some(Self {
            from: source.origin,
            from_axes: unit(source.axes)?,
            onto: er.origin,
            onto_axes: unit(er.axes)?,
            scale: [stretch, girth, girth],
            lean: None,
        })
    }

    fn place(&self, v: Vec3) -> Vec3 {
        let d = sub(v, self.from);
        (0..3).fold(self.onto, |at, i| {
            add(
                at,
                mul(self.onto_axes[i], dot(d, self.from_axes[i]) * self.scale[i]),
            )
        })
    }

    /// A normal carried by this map: turned with the frames, each component scaled by the inverse
    /// of the map's scale along it. Unnormalised.
    fn normal(&self, n: Vec3) -> Vec3 {
        (0..3).fold(Vec3::default(), |out, i| {
            add(
                out,
                mul(self.onto_axes[i], dot(n, self.from_axes[i]) / self.scale[i]),
            )
        })
    }
}

/// The arm lean of one side: up and out across Elden Ring's shoulder-to-hand axis, full at the
/// elbow and nothing at the shoulder or the hand.
#[derive(Clone, Copy, Debug)]
struct Lean {
    shoulder: Vec3,
    axis: Vec3,
    out: Vec3,
    elbow_at: f32,
    hand_at: f32,
    amount: f32,
}

impl Lean {
    fn new(shoulder: Vec3, elbow: Vec3, hand: Vec3, amount: f32) -> Result<Self> {
        let axis = normalized(sub(hand, shoulder)).ok_or("Elden Ring's arm has no length")?;
        let out =
            normalized(sub(UP, mul(axis, dot(UP, axis)))).ok_or("Elden Ring's arm is vertical")?;
        Ok(Self {
            shoulder,
            axis,
            out,
            elbow_at: dot(sub(elbow, shoulder), axis),
            hand_at: dot(sub(hand, shoulder), axis),
            amount,
        })
    }

    fn push(&self, at: Vec3) -> Vec3 {
        let along = dot(sub(at, self.shoulder), self.axis);
        let lean = smoothstep((self.hand_at - along) / (self.hand_at - self.elbow_at))
            * smoothstep(along / self.elbow_at);
        add(at, mul(self.out, self.amount * lean))
    }
}

/// What moves a source bone's vertices.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Carrier {
    /// The body fit.
    Axial,
    /// This limb map.
    Limb(usize),
}

/// Where a source bone's weight goes.
#[derive(Clone, Debug, PartialEq)]
enum WeightTo {
    Bone(String),
    /// Shared among Elden Ring's pelvis and spine by fitted height.
    Trunk,
}

#[derive(Clone, Debug, PartialEq)]
struct BoneFit {
    carrier: Carrier,
    weight: WeightTo,
}

/// The whole fit: every source bone's carrier and weight.
struct BipedFit {
    axial: AxialFit,
    maps: Vec<LimbMap>,
    leans: [Option<Lean>; 2],
    /// Per source bone; `None` for a bone with nowhere to go.
    bones: Vec<Option<BoneFit>>,
    names: Vec<String>,
    /// Elden Ring's pelvis and spine bones with their joint heights, rising.
    trunk: Vec<(&'static str, f32)>,
}

impl BipedFit {
    fn carry_point(&self, carrier: Carrier, p: Vec3) -> Vec3 {
        match carrier {
            Carrier::Axial => self.axial.place(p),
            Carrier::Limb(index) => {
                let map = &self.maps[index];
                let at = map.place(p);
                match map.lean.and_then(|side| self.leans[side]) {
                    Some(lean) => lean.push(at),
                    None => at,
                }
            }
        }
    }
}

impl Placement for BipedFit {
    fn rest(&self, vertex: &DsrVertex) -> (Vec3, Vec3) {
        (
            self.axial.place(vertex.position),
            self.axial.normal(vertex.position, vertex.normal),
        )
    }

    /// `vertex`'s bone slots are global bone indices here: [`export`] reads them through each
    /// mesh's palette first.
    fn carry(
        &self,
        vertex: &DsrVertex,
        slot: usize,
        (rest, rest_normal): (Vec3, Vec3),
    ) -> Result<Carried> {
        let bone = usize::from(vertex.bone_indices[slot]);
        let fit = self
            .bones
            .get(bone)
            .and_then(Option::as_ref)
            .ok_or_else(|| {
                Error::new(format!(
                    "bone {:?} carries weight, and the fit has nowhere to put it",
                    self.names
                        .get(bone)
                        .map_or("<out of range>", String::as_str)
                ))
            })?;
        let (at, normal) = match fit.carrier {
            Carrier::Axial => (rest, rest_normal),
            Carrier::Limb(index) => (
                self.carry_point(fit.carrier, vertex.position),
                self.maps[index].normal(vertex.normal),
            ),
        };
        let targets = match &fit.weight {
            WeightTo::Bone(name) => vec![(name.clone(), 1.0)],
            WeightTo::Trunk => trunk_split(&self.trunk, rest.y),
        };
        Ok(Carried {
            at,
            normal,
            targets,
        })
    }
}

/// Spine weight shared among Elden Ring's pelvis and spine bones at fitted height `y`: each takes
/// the band from its own joint to the next one up, and neighbours blend over [`TRUNK_BLEND`]
/// either side of the joint between them.
fn trunk_split(trunk: &[(&'static str, f32)], y: f32) -> Vec<(String, f32)> {
    let ramp = |x: f32| smoothstep((y - x + TRUNK_BLEND) / (2.0 * TRUNK_BLEND));
    let mut shares: Vec<(String, f32)> = trunk
        .iter()
        .enumerate()
        .filter_map(|(index, &(name, joint))| {
            let low = if index == 0 { 1.0 } else { ramp(joint) };
            let high = trunk
                .get(index + 1)
                .map_or(1.0, |&(_, next)| 1.0 - ramp(next));
            let share = low * high;
            (share > WEIGHT_FLOOR).then(|| (name.to_owned(), share))
        })
        .collect();
    let total: f32 = shares.iter().map(|(_, share)| share).sum();
    for (_, share) in &mut shares {
        *share /= total;
    }
    shares
}

/// Which joint a limb bone's length runs to.
enum Child {
    /// The Elden Ring bone of that name, and the source bone mapped to it below this one.
    Joint(String),
    /// The knuckle line: the middle of the long fingers' roots.
    Knuckles,
    /// A last segment.
    End,
}

/// `L_UpperArm` -> `L_Forearm`, `L_Finger1` -> `L_Finger11`, `L_Hand` -> the knuckles.
fn limb_child(target: &str) -> Child {
    let Some((side, joint)) = target.split_once('_') else {
        return Child::End;
    };
    let next = match joint {
        "Clavicle" => "UpperArm",
        "UpperArm" => "Forearm",
        "Forearm" => "Hand",
        "Hand" => return Child::Knuckles,
        "Thigh" => "Calf",
        "Calf" => "Foot",
        "Foot" => "Toe0",
        _ => {
            return match joint.strip_prefix("Finger").map(str::as_bytes) {
                Some([finger]) => Child::Joint(format!("{side}_Finger{}1", char::from(*finger))),
                Some([finger, b'1']) => {
                    Child::Joint(format!("{side}_Finger{}2", char::from(*finger)))
                }
                _ => Child::End,
            };
        }
    };
    Child::Joint(format!("{side}_{next}"))
}

/// Whether an Elden Ring bone is a limb bone, laid by a frame map rather than the body fit.
fn is_limb(target: &str) -> bool {
    let Some((side, joint)) = target.split_once('_') else {
        return false;
    };
    SIDES.contains(&side)
        && (matches!(
            joint,
            "Clavicle" | "UpperArm" | "Forearm" | "Hand" | "Thigh" | "Calf" | "Foot" | "Toe0"
        ) || joint.starts_with("Finger"))
}

fn is_thumb(target: &str) -> bool {
    target
        .split_once('_')
        .is_some_and(|(_, joint)| joint.starts_with("Finger0"))
}

/// The side index of an Elden Ring bone name, `L_` 0 and `R_` 1.
fn side_of(target: &str) -> Option<usize> {
    let (side, _) = target.split_once('_')?;
    SIDES.iter().position(|&s| s == side)
}

/// Elden Ring's twist bone of the segment a core bone starts: `L_Forearm` -> `L_ForeArmTwist`.
fn er_twist(core: &str) -> Option<String> {
    let (side, joint) = core.split_once('_')?;
    let twist = match joint {
        "UpperArm" => "UpArmTwist",
        "Forearm" => "ForeArmTwist",
        "Thigh" => "ThighTwist",
        "Calf" => "CalfTwist",
        _ => return None,
    };
    Some(format!("{side}_{twist}"))
}

/// What the fit makes of one skeleton, before any vertex is placed.
struct Built {
    fit: BipedFit,
    girth: f32,
    /// Each limb map's source bone and stretch along it, in bone order.
    stretch: Vec<(String, f32)>,
    grip_anchor: Option<Vec3>,
    /// The probe's class of each source bone.
    map_roles: Vec<MapRole>,
}

/// The fit of a source skeleton with these roles onto Elden Ring's.
fn build(bones: &[Bone], roles: &[Role], er_bones: &[Bone], params: BipedParams) -> Result<Built> {
    let frames = bind_frames(bones)?;
    let er_frames = bind_frames(er_bones)?;
    let mut er_index = HashMap::new();
    for (index, bone) in er_bones.iter().enumerate() {
        er_index.entry(bone.name.as_str()).or_insert(index);
    }
    let er_frame = |name: &str| -> Result<Frame> {
        er_index
            .get(name)
            .map(|&index| er_frames[index])
            .ok_or_else(|| Error::new(format!("the Elden Ring donor has no bone named {name}")))
    };
    // The first source bone mapped straight to `target` that has a parent, so its bind is a
    // place and not an offset.
    let direct = |target: &str| {
        (0..bones.len()).find(|&index| {
            bones[index].parent_index >= 0
                && matches!(&roles[index], Role::Direct(name) if name == target)
        })
    };
    let child_of = |parent: usize, target: &str| {
        (0..bones.len()).find(|&index| {
            usize::try_from(bones[index].parent_index) == Ok(parent)
                && matches!(&roles[index], Role::Direct(name) if name == target)
        })
    };
    let named = |index: usize| bones[index].name.as_str();

    // The pelvis is the root of a Biped skeleton, so its own transform is its place.
    let pelvis = (0..bones.len())
        .find(|&index| roles[index] == Role::Direct("Pelvis".to_owned()))
        .ok_or("the source has no pelvis")?;
    let er_pelvis = er_frame("Pelvis")?.origin;
    let girth = params
        .scale
        .unwrap_or(er_pelvis.y / frames[pelvis].origin.y);
    if !(girth.is_finite() && girth > 0.0) {
        return Err(Error::new(format!("girth scale {girth} is not usable")));
    }
    check_facing(&frames, &direct, named)?;

    let shoulder = direct("L_UpperArm").ok_or("the source has no left upper arm")?;
    let mut knots = vec![
        (frames[pelvis].origin, er_pelvis),
        (frames[shoulder].origin, er_frame("L_UpperArm")?.origin),
    ];
    if params.head_band {
        // The head itself, not the jaw that also maps to it.
        let head = (0..bones.len())
            .find(|&index| {
                roles[index] == Role::Direct("Head".to_owned())
                    && usize::try_from(bones[index].parent_index)
                        .is_ok_and(|up| roles[up] != Role::Direct("Head".to_owned()))
            })
            .ok_or("head_band needs a head bone")?;
        knots.push((frames[head].origin, er_frame("Head")?.origin));
    }
    let axial = AxialFit::new(knots, girth)?;

    let mut fits: Vec<Option<BoneFit>> = vec![None; bones.len()];
    let mut maps = Vec::new();
    let mut stretch = Vec::new();
    // Limb bones first: each gets a frame map of its own.
    for (index, role) in roles.iter().enumerate() {
        let Role::Direct(target) = role else {
            continue;
        };
        if !is_limb(target)
            || bones[index].parent_index < 0
            || (params.thumb == Thumb::Hand && is_thumb(target))
        {
            continue;
        }
        let er = er_frame(target)?;
        let along = |from: Frame, to: Vec3| {
            dot(
                sub(to, from.origin),
                normalized(from.axes[0]).unwrap_or_default(),
            )
        };
        let lengths = match limb_child(target) {
            Child::Joint(child) => match child_of(index, &child) {
                Some(below) => Some((
                    along(frames[index], frames[below].origin),
                    along(er, er_frame(&child)?.origin),
                )),
                None => None,
            },
            Child::Knuckles => {
                let side = target.split_once('_').map_or("", |(side, _)| side);
                let roots: Vec<(Vec3, Vec3)> = (1..=4)
                    .filter_map(|finger| {
                        let name = format!("{side}_Finger{finger}");
                        let below = child_of(index, &name)?;
                        Some(er_frame(&name).map(|er| (frames[below].origin, er.origin)))
                    })
                    .collect::<Result<_>>()?;
                (!roots.is_empty()).then(|| {
                    let mean = |pick: fn(&(Vec3, Vec3)) -> Vec3| {
                        let sum = roots.iter().map(pick).fold(Vec3::default(), add);
                        mul(sum, 1.0 / roots.len() as f32)
                    };
                    (
                        along(frames[index], mean(|root| root.0)),
                        along(er, mean(|root| root.1)),
                    )
                })
            }
            Child::End => None,
        };
        let bone_stretch = match lengths {
            Some((source, er)) if source > f32::EPSILON && er > f32::EPSILON => er / source,
            Some((source, er)) => {
                return Err(Error::new(format!(
                    "{} runs {source} m toward its child, and Elden Ring's {target} {er} m",
                    named(index)
                )));
            }
            None => girth,
        };
        let mut map = LimbMap::new(frames[index], er, bone_stretch, girth)
            .ok_or_else(|| Error::new(format!("{} or {target} has a zero axis", named(index))))?;
        if target.ends_with("_UpperArm") || target.ends_with("_Forearm") {
            map.lean = side_of(target);
        }
        fits[index] = Some(BoneFit {
            carrier: Carrier::Limb(maps.len()),
            weight: WeightTo::Bone(target.clone()),
        });
        maps.push(map);
        stretch.push((named(index).to_owned(), bone_stretch));
    }
    let mut leans = [None, None];
    for (side, name) in SIDES.iter().enumerate() {
        if params.arm_lean == 0.0 || !maps.iter().any(|map| map.lean == Some(side)) {
            continue;
        }
        let joint = |bone: &str| er_frame(&format!("{name}_{bone}")).map(|frame| frame.origin);
        leans[side] = Some(Lean::new(
            joint("UpperArm")?,
            joint("Forearm")?,
            joint("Hand")?,
            params.arm_lean,
        )?);
    }
    // The body, the thumbs on their hands, then the bones that follow another.
    for (index, role) in roles.iter().enumerate() {
        fits[index] = match role {
            Role::Direct(target) if matches!(target.as_str(), "Pelvis" | "Neck" | "Head") => {
                Some(BoneFit {
                    carrier: Carrier::Axial,
                    weight: WeightTo::Bone(target.clone()),
                })
            }
            Role::Direct(target) if params.thumb == Thumb::Hand && is_thumb(target) => {
                let hand = format!("{}_Hand", target.split_once('_').map_or("", |(s, _)| s));
                ancestors(bones, index)
                    .find(|&up| roles[up] == Role::Direct(hand.clone()))
                    .and_then(|up| fits[up].clone())
                    .map(|fit| BoneFit {
                        weight: WeightTo::Bone(target.clone()),
                        ..fit
                    })
            }
            Role::SpineChain(_) => Some(BoneFit {
                carrier: Carrier::Axial,
                weight: WeightTo::Trunk,
            }),
            _ => continue,
        };
    }
    for (index, role) in roles.iter().enumerate() {
        let Role::Twist(core) = role else {
            continue;
        };
        fits[index] = fits[*core]
            .clone()
            .map(|fit| match (&fit.weight, params.twist) {
                (WeightTo::Bone(core), Twist::ErTwist) => match er_twist(core) {
                    Some(twist) if er_index.contains_key(twist.as_str()) => BoneFit {
                        weight: WeightTo::Bone(twist),
                        ..fit
                    },
                    _ => fit,
                },
                _ => fit,
            });
    }
    for (index, role) in roles.iter().enumerate() {
        let followed = match role {
            Role::Ancestor(up) => fits[*up]
                .clone()
                .map(|fit| match (&fit.weight, params.cloth) {
                    (WeightTo::Bone(calf), Cloth::Thigh) if calf.ends_with("_Calf") => BoneFit {
                        weight: WeightTo::Bone(calf.replace("_Calf", "_Thigh")),
                        ..fit
                    },
                    _ => fit,
                }),
            // A vertex the strip left with some weight on the item, or a kept item, goes with the
            // bone that holds it.
            Role::Prop => ancestors(bones, index)
                .find(|&up| {
                    matches!(
                        roles[up],
                        Role::Direct(_) | Role::SpineChain(_) | Role::Twist(_)
                    )
                })
                .and_then(|up| fits[up].clone()),
            _ => continue,
        };
        fits[index] = followed;
    }

    let mut trunk = Vec::new();
    for name in TRUNK {
        trunk.push((name, er_frame(name)?.origin.y));
    }
    let fit = BipedFit {
        axial,
        maps,
        leans,
        bones: fits,
        names: bones.iter().map(|bone| bone.name.clone()).collect(),
        trunk,
    };

    // The grip: the right hand's held item, where the fit puts its root.
    let grip_anchor = (0..bones.len())
        .find(|&index| {
            roles[index] == Role::Prop
                && usize::try_from(bones[index].parent_index)
                    .is_ok_and(|up| roles[up] != Role::Prop)
                && fit.bones[index].as_ref().map(|fit| &fit.weight)
                    == Some(&WeightTo::Bone("R_Hand".to_owned()))
        })
        .and_then(|weapon| {
            let carrier = fit.bones[weapon].as_ref()?.carrier;
            Some(fit.carry_point(carrier, frames[weapon].origin))
        });

    Ok(Built {
        map_roles: (0..bones.len())
            .map(|index| map_role(bones, roles, index))
            .collect(),
        fit,
        girth,
        stretch,
        grip_anchor,
    })
}

/// A bone's ancestors, nearest first.
fn ancestors(bones: &[Bone], index: usize) -> impl Iterator<Item = usize> + '_ {
    let mut parent = bones[index].parent_index;
    let mut steps = 0;
    std::iter::from_fn(move || {
        let up = usize::try_from(parent)
            .ok()
            .filter(|&up| up < bones.len())?;
        steps += 1;
        if steps > bones.len() {
            return None;
        }
        parent = bones[up].parent_index;
        Some(up)
    })
}

/// Refuse a source that is mirrored or turned round against Elden Ring: its left upper arm and
/// thigh must be at +X and the right ones at -X, and each toe in front of its ankle, at -Z.
fn check_facing<'a>(
    frames: &[Frame],
    direct: &dyn Fn(&str) -> Option<usize>,
    named: impl Fn(usize) -> &'a str,
) -> Result<()> {
    for (side, sign) in [("L", 1.0), ("R", -1.0)] {
        for joint in ["UpperArm", "Thigh"] {
            if let Some(index) = direct(&format!("{side}_{joint}"))
                && frames[index].origin.x * sign <= 0.0
            {
                return Err(Error::new(format!(
                    "{} is at x {:.3}: Elden Ring has the left side at +X, and this source is \
                     mirrored",
                    named(index),
                    frames[index].origin.x
                )));
            }
        }
        if let (Some(foot), Some(toe)) = (
            direct(&format!("{side}_Foot")),
            direct(&format!("{side}_Toe0")),
        ) && frames[toe].origin.z >= frames[foot].origin.z
        {
            return Err(Error::new(format!(
                "{} is not in front of {} (z {:.3} against {:.3}): Elden Ring faces -Z",
                named(toe),
                named(foot),
                frames[toe].origin.z,
                frames[foot].origin.z
            )));
        }
    }
    Ok(())
}

/// The fit probe's class of a source bone.
fn map_role(bones: &[Bone], roles: &[Role], index: usize) -> MapRole {
    match &roles[index] {
        Role::Direct(target) => {
            let joint = target.split_once('_').map_or(target.as_str(), |(_, j)| j);
            match joint {
                "Pelvis" | "Neck" | "Head" => MapRole::Trunk,
                "Clavicle" | "UpperArm" | "Forearm" => MapRole::Arm,
                "Hand" => MapRole::Hand,
                "Thigh" | "Calf" | "Foot" | "Toe0" => MapRole::Leg,
                _ if joint.starts_with("Finger") => {
                    let segment_below = (0..bones.len()).any(|below| {
                        usize::try_from(bones[below].parent_index) == Ok(index)
                            && matches!(&roles[below], Role::Direct(t) if t.contains("_Finger"))
                    });
                    if segment_below {
                        MapRole::Finger
                    } else {
                        MapRole::FingerTip
                    }
                }
                _ => MapRole::Other,
            }
        }
        Role::SpineChain(_) => MapRole::Trunk,
        Role::Twist(core) => map_role(bones, roles, *core),
        Role::Ancestor(_) => MapRole::Cloth,
        Role::Prop => MapRole::Prop,
        Role::Unused => MapRole::Other,
    }
}

/// Each vertex's rows summed per target, in the order the targets first appear, and scaled so the
/// vertex's weights add up to one. `rows` comes grouped by vertex, as [`place_all`] writes it.
fn merged(rows: Vec<WeightRow>) -> Vec<WeightRow> {
    let mut out: Vec<WeightRow> = Vec::with_capacity(rows.len());
    let mut start = 0;
    for row in rows {
        match out[start..]
            .iter_mut()
            .find(|seen| seen.vertex == row.vertex && seen.target_bone == row.target_bone)
        {
            Some(seen) => seen.weight += row.weight,
            None => {
                if out
                    .get(start)
                    .is_some_and(|first| first.vertex != row.vertex)
                {
                    normalise(&mut out[start..]);
                    start = out.len();
                }
                out.push(row);
            }
        }
    }
    normalise(&mut out[start..]);
    out
}

fn normalise(rows: &mut [WeightRow]) {
    let total: f32 = rows.iter().map(|row| row.weight).sum();
    if total > 0.0 {
        for row in rows {
            row.weight /= total;
        }
    }
}

/// A Biped character's FLVER fitted to the donor's bind skeleton, its held items cut and its
/// meshes run together in file order, each keeping only what its LOD0 draws.
pub fn export(id: &'static str, flver: &[u8], donor: &[u8], params: BipedParams) -> Result<Export> {
    let source = source::parse(flver)?;
    if !skeleton::is_biped(&source.bones) {
        return Err(Error::new(format!(
            "{id} does not name its bones the Biped way"
        )));
    }
    let roles = skeleton::classify(&source.bones)?;
    let er_bones = Flver::parse(donor)?.bones;
    let built = build(&source.bones, &roles, &er_bones, params)?;
    let strip_roles: Vec<Role> = match params.held_items {
        HeldItems::Strip => roles,
        HeldItems::Keep => roles
            .into_iter()
            .map(|role| {
                if role == Role::Prop {
                    Role::Unused
                } else {
                    role
                }
            })
            .collect(),
    };
    let (kept, strip) = source::strip_props(&source, &strip_roles);

    let mut vertices = Vec::new();
    let mut triangles = Vec::new();
    let mut source_map = Vec::new();
    for mesh in &kept {
        let palette = &source.meshes[mesh.source_mesh];
        let base = u32::try_from(vertices.len()).map_err(|_| "too many vertices")?;
        for (vertex, &buffer_index) in mesh.vertices.iter().zip(&mesh.source_vertex) {
            let global = vertex.bone_indices.map(|slot| palette.bone(slot));
            let bone = dominant_bone(global, vertex.bone_weights);
            source_map.push(SourceRow {
                mesh: mesh.source_mesh,
                vertex: buffer_index,
                bone: source
                    .bones
                    .get(bone)
                    .map_or_else(String::new, |bone| bone.name.clone()),
                role: built.map_roles.get(bone).copied().unwrap_or(MapRole::Other),
            });
            let mut bone_indices = [0; 4];
            for (slot, index) in bone_indices.iter_mut().zip(global) {
                *slot = u16::try_from(index).map_err(|_| "bone index past 65535")?;
            }
            vertices.push(DsrVertex {
                bone_indices,
                ..*vertex
            });
        }
        triangles.extend(
            mesh.triangles
                .iter()
                .map(|triangle| triangle.map(|corner| corner + base)),
        );
    }
    let (placed, rows) = place_all(&built.fit, &vertices)?;
    Ok(Export {
        character: id,
        mesh: SourceMesh {
            vertices: placed,
            triangles,
            weights: merged(rows),
        },
        scale: built.girth,
        params: params.json(built.girth),
        stretch: built.stretch,
        grip_anchor: built.grip_anchor,
        source_map,
        strip: Some(strip),
    })
}

#[cfg(test)]
mod tests {
    use std::f32::consts::FRAC_PI_4;

    use super::*;

    fn v(x: f32, y: f32, z: f32) -> Vec3 {
        Vec3 { x, y, z }
    }

    fn close(a: Vec3, b: Vec3) -> bool {
        let d = sub(a, b);
        dot(d, d).sqrt() < 1e-5
    }

    /// A bone turned `turn` radians about Z, then offset `at` in its parent's frame.
    fn bone(name: &str, parent: i16, at: [f32; 3], turn: f32) -> Bone {
        Bone {
            name: name.to_owned(),
            parent_index: parent,
            translation: v(at[0], at[1], at[2]),
            rotation: v(0.0, 0.0, turn),
            scale: v(1.0, 1.0, 1.0),
        }
    }

    /// The left half of a Biped in a T-pose: the arm along +X, the leg down.
    fn source() -> Vec<Bone> {
        let down = -std::f32::consts::FRAC_PI_2;
        vec![
            bone("Pelvis", -1, [0.0, 0.9, 0.0], 0.0),           // 0
            bone("Spine", 0, [0.0, 0.1, 0.0], 0.0),             // 1
            bone("Spine1", 1, [0.0, 0.2, 0.0], 0.0),            // 2
            bone("Neck", 2, [0.0, 0.25, 0.0], 0.0),             // 3
            bone("Head", 3, [0.0, 0.1, 0.0], 0.0),              // 4
            bone("L Clavicle", 3, [0.02, 0.0, 0.0], 0.0),       // 5
            bone("L UpperArm", 5, [0.12, 0.0, 0.0], 0.0),       // 6
            bone("L Forearm", 6, [0.26, 0.0, 0.0], 0.0),        // 7
            bone("L Hand", 7, [0.26, 0.0, 0.0], 0.0),           // 8
            bone("L Finger0", 8, [0.05, -0.02, -0.03], 0.0),    // 9
            bone("L Finger1", 8, [0.1, 0.0, -0.02], 0.0),       // 10
            bone("L Finger11", 10, [0.04, 0.0, 0.0], 0.0),      // 11
            bone("L Finger2", 8, [0.1, 0.0, 0.02], 0.0),        // 12
            bone("L Finger21", 12, [0.04, 0.0, 0.0], 0.0),      // 13
            bone("L ForeTwist", 6, [0.26, 0.0, 0.0], 0.0),      // 14
            bone("L_FA_Cloth_a1", 7, [0.05, -0.05, 0.0], 0.0),  // 15
            bone("L_FA_Cloth_a2", 15, [0.05, -0.05, 0.0], 0.0), // 16
            bone("L Thigh", 1, [0.08, -0.1, 0.0], down),        // 17
            bone("L Calf", 17, [0.45, 0.0, 0.0], 0.0),          // 18
            bone("L Foot", 18, [0.37, 0.0, 0.0], 0.0),          // 19
            bone("L Toe0", 19, [0.07, 0.0, -0.1], 0.0),         // 20
            bone("S_Cloth_a1", 2, [0.0, 0.1, -0.1], 0.0),       // 21
        ]
    }

    /// Elden Ring's left half in an A-pose: the arm 45 degrees down, the hand longer.
    fn er() -> Vec<Bone> {
        let down = -std::f32::consts::FRAC_PI_2;
        vec![
            bone("Pelvis", -1, [0.0, 0.97, 0.0], 0.0),           // 0
            bone("Spine", 0, [0.0, 0.093, 0.0], 0.0),            // 1
            bone("Spine1", 1, [0.0, 0.088, 0.0], 0.0),           // 2
            bone("Spine2", 2, [0.0, 0.0885, 0.0], 0.0),          // 3
            bone("Neck", 3, [0.0, 0.21, 0.02], 0.0),             // 4
            bone("Head", 4, [0.0, 0.1, 0.0], 0.0),               // 5
            bone("L_Clavicle", 3, [0.02, 0.15, 0.03], 0.0),      // 6
            bone("L_UpperArm", 6, [0.15, 0.0, 0.0], -FRAC_PI_4), // 7
            bone("L_Forearm", 7, [0.28, 0.0, 0.0], 0.0),         // 8
            bone("L_Hand", 8, [0.23, 0.0, 0.0], 0.0),            // 9
            bone("L_Finger0", 9, [0.02, -0.02, -0.02], 0.0),     // 10
            bone("L_Finger1", 9, [0.09, 0.0, -0.02], 0.0),       // 11
            bone("L_Finger11", 11, [0.045, 0.0, 0.0], 0.0),      // 12
            bone("L_Finger2", 9, [0.09, 0.0, 0.02], 0.0),        // 13
            bone("L_Finger21", 13, [0.045, 0.0, 0.0], 0.0),      // 14
            bone("L_ForeArmTwist", 8, [0.2, 0.0, 0.0], 0.0),     // 15
            bone("L_Thigh", 0, [0.1, 0.0, 0.0], down),           // 16
            bone("L_Calf", 16, [0.44, 0.0, 0.0], 0.0),           // 17
            bone("L_Foot", 17, [0.42, 0.0, 0.0], 0.0),           // 18
            bone("L_Toe0", 18, [0.11, 0.0, -0.13], 0.0),         // 19
            bone("L_ForeArmTwist1", 8, [0.1, 0.0, 0.0], 0.0),    // 20
        ]
    }

    fn fitted(params: BipedParams) -> (Built, Vec<Role>) {
        let bones = source();
        let roles = skeleton::classify(&bones).unwrap();
        (build(&bones, &roles, &er(), params).unwrap(), roles)
    }

    fn joint(bones: &[Bone], name: &str) -> Vec3 {
        let index = bones.iter().position(|b| b.name == name).unwrap();
        bind_frames(bones).unwrap()[index].origin
    }

    fn carried(built: &Built, bone: usize, p: Vec3) -> Vec3 {
        let fit = built.fit.bones[bone].as_ref().unwrap();
        built.fit.carry_point(fit.carrier, p)
    }

    #[test]
    fn each_limb_joint_lands_on_its_elden_ring_joint() {
        let (built, _) = fitted(BipedParams::DEFAULT);
        let (s, e) = (source(), er());
        // A bone's map takes its own joint and its child's onto Elden Ring's two joints.
        for (bone, from, to, er_from, er_to) in [
            (6, "L UpperArm", "L Forearm", "L_UpperArm", "L_Forearm"),
            (7, "L Forearm", "L Hand", "L_Forearm", "L_Hand"),
            (17, "L Thigh", "L Calf", "L_Thigh", "L_Calf"),
            (18, "L Calf", "L Foot", "L_Calf", "L_Foot"),
            (10, "L Finger1", "L Finger11", "L_Finger1", "L_Finger11"),
        ] {
            assert!(
                close(carried(&built, bone, joint(&s, from)), joint(&e, er_from)),
                "{from}"
            );
            assert!(
                close(carried(&built, bone, joint(&s, to)), joint(&e, er_to)),
                "{to}"
            );
        }
        // The hand runs to the knuckle line.
        let knuckles = mul(add(joint(&s, "L Finger1"), joint(&s, "L Finger2")), 0.5);
        let er_knuckles = mul(add(joint(&e, "L_Finger1"), joint(&e, "L_Finger2")), 0.5);
        assert!(close(carried(&built, 8, knuckles), er_knuckles));
        // Across the bone the girth scale holds: 0.01 m above the T-posed upper arm lands 0.01 k
        // m off Elden Ring's, square to its slant.
        let k = built.girth;
        assert!((k - 0.97 / 0.9).abs() < 1e-6);
        let off = sub(
            carried(&built, 6, add(joint(&s, "L UpperArm"), v(0.0, 0.01, 0.0))),
            joint(&e, "L_UpperArm"),
        );
        assert!(close(
            off,
            mul(v(FRAC_PI_4.sin(), FRAC_PI_4.cos(), 0.0), 0.01 * k)
        ));
        let upper = built.stretch.iter().find(|(name, _)| name == "L UpperArm");
        assert!((upper.unwrap().1 - 0.28 / 0.26).abs() < 1e-5);
        // A last segment stretches by the girth.
        let tip = built.stretch.iter().find(|(name, _)| name == "L Finger11");
        assert!((tip.unwrap().1 - k).abs() < 1e-6);
    }

    #[test]
    fn a_twist_link_moves_with_its_core_bone_and_weighs_where_asked() {
        let (built, roles) = fitted(BipedParams::DEFAULT);
        assert_eq!(roles[14], Role::Twist(7));
        let twist = built.fit.bones[14].clone().unwrap();
        assert_eq!(Some(twist.clone()), built.fit.bones[7].clone());
        assert_eq!(twist.weight, WeightTo::Bone("L_Forearm".to_owned()));
        let (er_twist, _) = fitted(BipedParams {
            twist: Twist::ErTwist,
            ..BipedParams::DEFAULT
        });
        let twist = er_twist.fit.bones[14].clone().unwrap();
        assert_eq!(twist.weight, WeightTo::Bone("L_ForeArmTwist".to_owned()));
        assert_eq!(
            Some(twist.carrier),
            er_twist.fit.bones[7].as_ref().map(|f| f.carrier)
        );
    }

    #[test]
    fn a_two_deep_cloth_chain_goes_with_its_first_biped_ancestor() {
        let (built, roles) = fitted(BipedParams::DEFAULT);
        assert_eq!(
            (roles[15].clone(), roles[16].clone()),
            (Role::Ancestor(7), Role::Ancestor(7))
        );
        assert_eq!(built.fit.bones[16], built.fit.bones[7]);
        // Cloth on the spine follows the spine into the trunk split.
        assert_eq!(
            built.fit.bones[21],
            Some(BoneFit {
                carrier: Carrier::Axial,
                weight: WeightTo::Trunk
            })
        );
        assert_eq!(built.map_roles[16], MapRole::Cloth);
        assert_eq!(built.map_roles[14], MapRole::Arm);
        assert_eq!(built.map_roles[10], MapRole::Finger);
        assert_eq!(built.map_roles[11], MapRole::FingerTip);
        assert_eq!(built.map_roles[9], MapRole::FingerTip);
    }

    #[test]
    fn the_thumb_rides_the_hand_unless_asked_to_map_its_own_frame() {
        let (built, _) = fitted(BipedParams::DEFAULT);
        let thumb = built.fit.bones[9].clone().unwrap();
        assert_eq!(
            Some(thumb.carrier),
            built.fit.bones[8].as_ref().map(|f| f.carrier)
        );
        assert_eq!(thumb.weight, WeightTo::Bone("L_Finger0".to_owned()));
        assert!(!built.stretch.iter().any(|(name, _)| name == "L Finger0"));
        let (framed, _) = fitted(BipedParams {
            thumb: Thumb::Frame,
            ..BipedParams::DEFAULT
        });
        let thumb = framed.fit.bones[9].clone().unwrap();
        assert_ne!(
            Some(thumb.carrier),
            framed.fit.bones[8].as_ref().map(|f| f.carrier)
        );
        assert!(close(
            carried(&framed, 9, joint(&source(), "L Finger0")),
            joint(&er(), "L_Finger0")
        ));
    }

    #[test]
    fn spine_weight_is_shared_by_fitted_height() {
        let trunk = [
            ("Pelvis", 0.97),
            ("Spine", 1.063),
            ("Spine1", 1.151),
            ("Spine2", 1.2395),
        ];
        let share = |y: f32, bone: &str| {
            trunk_split(&trunk, y)
                .into_iter()
                .find(|(name, _)| name == bone)
                .map_or(0.0, |(_, share)| share)
        };
        for y in [0.5, 1.0, 1.063, 1.1, 1.2, 1.5] {
            let total: f32 = trunk_split(&trunk, y).iter().map(|(_, s)| s).sum();
            assert!((total - 1.0).abs() < 1e-6, "{y}");
        }
        assert_eq!(share(1.0, "Pelvis"), 1.0);
        assert!((share(1.063, "Pelvis") - 0.5).abs() < 1e-6);
        assert!((share(1.063, "Spine") - 0.5).abs() < 1e-6);
        assert_eq!(share(1.1, "Spine"), 1.0);
        assert_eq!(share(1.5, "Spine2"), 1.0);
    }

    #[test]
    fn the_lean_pushes_the_elbow_and_spares_the_shoulder_and_hand() {
        let (still, _) = fitted(BipedParams::DEFAULT);
        let (leaned, _) = fitted(BipedParams {
            arm_lean: 0.05,
            ..BipedParams::DEFAULT
        });
        let s = source();
        let moved = |bone: usize, name: &str| {
            sub(
                carried(&leaned, bone, joint(&s, name)),
                carried(&still, bone, joint(&s, name)),
            )
        };
        assert!(close(moved(6, "L UpperArm"), v(0.0, 0.0, 0.0)));
        assert!(close(moved(7, "L Hand"), v(0.0, 0.0, 0.0)));
        let elbow = moved(7, "L Forearm");
        assert!((dot(elbow, elbow).sqrt() - 0.05).abs() < 1e-5);
        assert!(elbow.x > 0.0 && elbow.y > 0.0);
    }

    #[test]
    fn a_mirrored_or_backward_source_is_refused() {
        let er = er();
        let mut mirrored = source();
        for bone in &mut mirrored {
            if bone.parent_index < 0 || bone.name.starts_with("L ") {
                bone.translation.x = -bone.translation.x;
            }
        }
        let roles = skeleton::classify(&mirrored).unwrap();
        let error = build(&mirrored, &roles, &er, BipedParams::DEFAULT)
            .err()
            .unwrap();
        assert!(error.to_string().contains("+X"), "{error}");
        let mut backward = source();
        backward[20].translation.z = 0.1;
        let roles = skeleton::classify(&backward).unwrap();
        let error = build(&backward, &roles, &er, BipedParams::DEFAULT)
            .err()
            .unwrap();
        assert!(error.to_string().contains("-Z"), "{error}");
    }

    #[test]
    fn rows_merge_per_target_and_add_up_to_one() {
        let row = |vertex: usize, bone: &str, weight: f32| WeightRow {
            vertex,
            target_bone: bone.to_owned(),
            weight,
        };
        let merged = merged(vec![
            row(0, "Spine", 0.25),
            row(0, "Spine1", 0.25),
            row(0, "Spine", 0.5),
            row(1, "L_Hand", 2.0),
            row(1, "L_Finger0", 2.0),
        ]);
        assert_eq!(
            merged,
            [
                row(0, "Spine", 0.75),
                row(0, "Spine1", 0.25),
                row(1, "L_Hand", 0.5),
                row(1, "L_Finger0", 0.5),
            ]
        );
    }

    #[test]
    fn limb_children_follow_the_chain() {
        let name = |target: &str| match limb_child(target) {
            Child::Joint(name) => name,
            Child::Knuckles => "knuckles".to_owned(),
            Child::End => "end".to_owned(),
        };
        assert_eq!(name("L_Clavicle"), "L_UpperArm");
        assert_eq!(name("R_Hand"), "knuckles");
        assert_eq!(name("L_Finger1"), "L_Finger11");
        assert_eq!(name("L_Finger01"), "L_Finger02");
        assert_eq!(name("R_Finger22"), "end");
        assert_eq!(name("L_Toe0"), "end");
        assert_eq!(er_twist("R_Calf").as_deref(), Some("R_CalfTwist"));
        assert_eq!(er_twist("L_Hand"), None);
        assert!(is_limb("L_Finger21") && !is_limb("Neck") && is_thumb("R_Finger01"));
    }
}
