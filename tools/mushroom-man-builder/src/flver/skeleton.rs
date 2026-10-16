//! What each bone of a Dark Souls Biped skeleton becomes on Elden Ring's player skeleton.
//!
//! 74 of DSR's chrbnds name their bones the 3ds Max Biped way, and the names drift between them:
//! `Jaw ` with a trailing space, `R weapon `, `L_Clavicle` against `L Clavicle`, `R UpperArm01`,
//! `Master Neck1`, `LCalfTwist` against `L ForeTwist`, `Bip01 L Finger0Nub`. So a name is first
//! [`normalise`]d -- trimmed, case-folded, spaces and underscores dropped, a leading `bip01`
//! stripped -- and then matched against one ordered rule table, the one measured over all 74
//! (`biped-to-er-name-rules.tsv` in the investigation data).
//!
//! The roles, in the order they are decided:
//!
//! - dummies, model-name roots, control-rig copies and an exact `master` are [`Role::Unused`];
//! - core Biped bones map [`Role::Direct`] by name, `L UpperArm` to `L_UpperArm`, the jaw to
//!   `Head` (the donor FLVER has no jaw bone), a fourth finger joint to the third;
//! - spine links are a [`Role::SpineChain`] rank, because Elden Ring's three spine bones are
//!   assigned by fitted height, not by name;
//! - twist links move with the core bone of the segment they lie on ([`Role::Twist`]), never with
//!   their hierarchy parent: in c2650's clips `L ForeTwist` stays within 11 degrees of
//!   `L Forearm` and up to 138 from its parent `L UpperArm`;
//! - held items and everything hanging from them are [`Role::Prop`], and their vertices are cut;
//! - everything else -- cloth chains, Nubs, tails -- goes to its nearest mapped ancestor
//!   ([`Role::Ancestor`]), and a bone with no such ancestor is [`Role::Unused`].
//!
//! FromSoft's own rigs name the same joints another way, and take the same roles. In c5310
//! Gwynevere, c5220 Nito and c4130 Scarecrow `LArm11` is the shoulder and `LArm21` (Nito's
//! `LArm2`) the elbow, with `LArm12`, `LArm22` and `LArm23` evenly spaced down their segments, so
//! twist links; `LArmPalm` is the hand and `LArmDigit<digit><joint>` a finger, the thumb first;
//! `LLeg1` is the hip, `LLeg2` the knee, `LLegAnkle` the foot, and Nito's `LLeg3`, a second shin
//! link, is a Biped horselink. The Bloatheads (c4150, c4160) spell the Biped words with a number,
//! `LUpperarm1` the shoulder and `LUpperarm2` halfway to `LForearm1`, with `LCollarbone`, `LPalm`
//! and `LDigit11`.
//!
//! No joint position is read here except to check a twist link against its core segment, and
//! then only when the twist bone has a parent: an orphaned bone's bind is a local offset, not a
//! place. The first link of a chain sits on the core joint itself (`L ForeTwist` on `L Forearm`);
//! a numbered one sits partway down the segment (c2370's `LUpArmTwist1` halfway from `L UpperArm`
//! to `L Forearm`, c3450's `L ForeTwist1` halfway to `L Hand`).

use super::{Bone, Frame, Vec3, bind_frames, bind_positions};
use crate::error::{Error, Result};

/// A twist link lies on its core segment; further off it than this and the name lied.
const TWIST_JOINT_TOLERANCE: f32 = 0.001;
const BIPED_ROOT_PREFIX: &str = "bip01";

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Role {
    /// Weighted to this Elden Ring bone.
    Direct(String),
    /// A spine link, 0 the lowest. Which of Elden Ring's `Spine`, `Spine1`, `Spine2` it feeds
    /// depends on where the fit puts each vertex.
    SpineChain(usize),
    /// A twist link, which moves with this source bone: the core bone at the same joint.
    Twist(usize),
    /// A held item, or a bone hanging from one. Its vertices are stripped.
    Prop,
    /// Nothing to map to. A weighted bone here is an error.
    Unused,
    /// Weighted as this source bone, its nearest ancestor with a mapping.
    Ancestor(usize),
}

impl Role {
    fn is_mapped(&self) -> bool {
        matches!(self, Self::Direct(_) | Self::SpineChain(_) | Self::Twist(_))
    }

    /// The Elden Ring bone this role weights to, or what decides it.
    pub fn er_target(&self, roles: &[Role]) -> Option<String> {
        match self {
            Self::Direct(name) => Some(name.clone()),
            Self::SpineChain(_) => Some("Spine/Spine1/Spine2 by height".to_owned()),
            Self::Twist(core) | Self::Ancestor(core) => match roles.get(*core) {
                Some(role @ (Self::Direct(_) | Self::SpineChain(_) | Self::Twist(_))) => {
                    role.er_target(roles)
                }
                _ => None,
            },
            Self::Prop | Self::Unused => None,
        }
    }
}

/// `L Finger0Nub` -> `lfinger0nub`, `Jaw ` -> `jaw`, `Bip01 L Toe0` -> `ltoe0`.
pub fn normalise(name: &str) -> String {
    let folded: String = name
        .trim()
        .chars()
        .filter(|c| *c != ' ' && *c != '_')
        .flat_map(char::to_lowercase)
        .collect();
    match folded.strip_prefix(BIPED_ROOT_PREFIX) {
        Some(rest) => rest.to_owned(),
        None => folded,
    }
}

/// Whether this skeleton uses Biped names: some bone is `L UpperArm` or `R UpperArm`, spaced or
/// underscored, with an optional `Bip01 ` in front and digits after. [`normalise`] drops the
/// underscore, so the `L_UpperArm` rigs (c2390 Darkwraith, the Black Knights, c2410) take the same
/// rules as the spaced ones; letting them through on 2026-10-15 added 6 rigs that fit.
///
/// FromSoft's own rigs count when they have a shoulder, a palm and a hip (`LArm11` or `LArm1`,
/// `LArmPalm`, `LLeg1`), and the Bloatheads' when they have `LUpperarm1`, `LPalm` and `LThigh1`.
/// Asking for all three keeps out the ones with no hands (c2330 Demonic Foliage), no legs (c2430
/// Demonic Statue) or a lettered limb set of each (c2700 Serpent Mage's four arms, `LArm_a_11` and
/// `LArm_b_11`).
pub fn is_biped(bones: &[Bone]) -> bool {
    let names: Vec<String> = bones.iter().map(|bone| normalise(&bone.name)).collect();
    let any = |wanted: &[&str]| names.iter().any(|name| wanted.contains(&name.as_str()));
    let fromsoft = any(&["larm1", "rarm1", "larm11", "rarm11"])
        && any(&["larmpalm", "rarmpalm"])
        && any(&["lleg1", "rleg1"]);
    let numbered = any(&["lupperarm1", "rupperarm1"])
        && any(&["lpalm", "rpalm"])
        && any(&["lthigh1", "rthigh1"]);
    fromsoft || numbered || biped_upper_arm(bones)
}

fn biped_upper_arm(bones: &[Bone]) -> bool {
    bones.iter().any(|bone| {
        let name = bone.name.trim();
        let name = name.strip_prefix("Bip01 ").unwrap_or(name);
        let Some(rest) = name
            .strip_prefix("L UpperArm")
            .or_else(|| name.strip_prefix("R UpperArm"))
            .or_else(|| name.strip_prefix("L_UpperArm"))
            .or_else(|| name.strip_prefix("R_UpperArm"))
        else {
            return false;
        };
        rest.chars().all(|c| c.is_ascii_digit())
    })
}

/// What a bone's name alone says, before the hierarchy is looked at.
#[derive(Clone, Debug, PartialEq, Eq)]
enum NameRule {
    Unused,
    Direct(String),
    Spine,
    /// A twist link and the Elden Ring name of the core bone it rides with.
    Twist(String),
    /// A FromSoft-named finger: its digit and joint, both counted from 1.
    Digit {
        side: &'static str,
        digit: u32,
        joint: u32,
    },
    Prop,
    Nub,
    Other,
}

/// `lupperarm01` with `upperarm` -> `("L", "01")`.
fn sided<'a>(name: &'a str, word: &str) -> Option<(&'static str, &'a str)> {
    let side = match name.as_bytes().first()? {
        b'l' => "L",
        b'r' => "R",
        _ => return None,
    };
    name[1..].strip_prefix(word).map(|rest| (side, rest))
}

fn digits(text: &str) -> bool {
    text.chars().all(|c| c.is_ascii_digit())
}

/// The rule for one normalised name. `raw` is the name as stored, for the held-item patterns
/// that need its separators.
fn name_rule(name: &str, raw: &str) -> NameRule {
    let core = |word: &str, suffixes: &[&str]| -> Option<String> {
        let (side, rest) = sided(name, word)?;
        suffixes
            .contains(&rest)
            .then(|| format!("{side}_{}", er_word(word)))
    };
    if is_dummy(name) {
        return NameRule::Unused;
    }
    if name == "pelvis" {
        return NameRule::Direct("Pelvis".to_owned());
    }
    if name.strip_prefix("spine").is_some_and(digits) {
        return NameRule::Spine;
    }
    if name
        .strip_prefix("master")
        .unwrap_or(name)
        .strip_prefix("neck")
        .is_some_and(digits)
    {
        return NameRule::Direct("Neck".to_owned());
    }
    if matches!(name, "head" | "head01" | "uhead") || name.strip_prefix("jaw").is_some_and(digits) {
        return NameRule::Direct("Head".to_owned());
    }
    for word in [
        "clavicle", "upperarm", "forearm", "hand", "thigh", "calf", "foot",
    ] {
        if let Some(target) = core(word, &["", "01"]) {
            return NameRule::Direct(target);
        }
    }
    // The Bloatheads' numbered long bones: the first starts the segment, the second is halfway.
    for word in ["upperarm", "forearm", "thigh", "calf"] {
        if let Some(target) = core(word, &["1"]) {
            return NameRule::Direct(target);
        }
        if let Some(target) = core(word, &["2"]) {
            return NameRule::Twist(target);
        }
    }
    if let Some(rule) = fromsoft_rule(name) {
        return rule;
    }
    for (word, twin, suffixes) in [
        ("uparmtwist", "upperarm", &["", "1"][..]),
        ("foretwist", "forearm", &["", "1"][..]),
        ("thightwist", "thigh", &[""][..]),
        ("calftwist", "calf", &[""][..]),
    ] {
        if let Some((side, rest)) = sided(name, word)
            && suffixes.contains(&rest)
        {
            return NameRule::Twist(format!("{side}_{}", er_word(twin)));
        }
    }
    if let Some((side, rest)) = sided(name, "finger") {
        let mut joints = rest.chars().map(|c| c.to_digit(10));
        match (joints.next(), joints.next(), joints.next()) {
            (Some(Some(finger @ 0..=4)), None, _) => {
                return NameRule::Direct(format!("{side}_Finger{finger}"));
            }
            (Some(Some(finger @ 0..=4)), Some(Some(joint @ 1..=9)), None) => {
                return NameRule::Direct(format!("{side}_Finger{finger}{}", joint.min(2)));
            }
            _ => {}
        }
    }
    if let Some((side, "")) = sided(name, "horselink") {
        return NameRule::Direct(format!("{side}_Calf"));
    }
    if let Some((side, rest)) = sided(name, "toe")
        && !rest.is_empty()
        && digits(rest)
    {
        return NameRule::Direct(format!("{side}_Toe0"));
    }
    if is_held_item(name, raw) {
        return NameRule::Prop;
    }
    if name.ends_with("nub") {
        return NameRule::Nub;
    }
    NameRule::Other
}

/// A FromSoft-named limb bone (`larm11`, `larmpalm`, `larmdigit23`, `lleg3`, `llegankle`) or a
/// Bloathead one (`lcollarbone`, `lpalm`, `ldigit11`), or `None`.
fn fromsoft_rule(name: &str) -> Option<NameRule> {
    let direct = |side: &str, joint: &str| Some(NameRule::Direct(format!("{side}_{joint}")));
    if let Some((side, rest)) = sided(name, "arm") {
        return match rest {
            "collarbone" => direct(side, "Clavicle"),
            "1" | "11" => direct(side, "UpperArm"),
            "12" => Some(NameRule::Twist(format!("{side}_UpperArm"))),
            "2" | "21" => direct(side, "Forearm"),
            "22" | "23" => Some(NameRule::Twist(format!("{side}_Forearm"))),
            "palm" => direct(side, "Hand"),
            _ => rest
                .strip_prefix("digit")
                .and_then(|digit| finger(side, digit)),
        };
    }
    if let Some((side, rest)) = sided(name, "leg") {
        return match rest {
            "1" => direct(side, "Thigh"),
            "2" | "3" => direct(side, "Calf"),
            "ankle" => direct(side, "Foot"),
            _ => rest
                .strip_prefix("digit")
                .filter(|toe| !toe.is_empty() && digits(toe))
                .and_then(|_| direct(side, "Toe0")),
        };
    }
    match sided(name, "collarbone").or_else(|| sided(name, "palm")) {
        Some((side, "")) if name.ends_with("collarbone") => return direct(side, "Clavicle"),
        Some((side, "")) => return direct(side, "Hand"),
        _ => {}
    }
    let (side, rest) = sided(name, "digit")?;
    finger(side, rest)
}

/// `digit` `23`: the second digit's third joint. Which Elden Ring finger it becomes is decided by
/// where it sits on the hand, in [`classify`].
fn finger(side: &'static str, digit: &str) -> Option<NameRule> {
    let mut parts = digit.chars().map(|c| c.to_digit(10));
    match (parts.next(), parts.next(), parts.next()) {
        (Some(Some(digit @ 1..=9)), Some(Some(joint @ 1..=9)), None) => {
            Some(NameRule::Digit { side, digit, joint })
        }
        _ => None,
    }
}

/// Elden Ring's finger for each numbered digit of one hand: the digit whose root lies least far
/// down the hand's X axis is the thumb, finger 0, and the rest take fingers 1 to 4 in their
/// numbered order. A lone digit is a mitten, finger 1. The numbering alone does not say which is
/// the thumb: digit 1 is in c5310 Gwynevere's five and the Bloatheads', but c4130 Scarecrow's
/// thumb is digit 2, 0.015 m along the palm against its mitten's 0.162 m.
fn digit_fingers(hand: Frame, roots: &[(u32, Vec3)]) -> Vec<(u32, u32)> {
    let along = |at: Vec3| {
        let axis = hand.axes[0];
        let offset = sub(at, hand.origin);
        offset.x * axis.x + offset.y * axis.y + offset.z * axis.z
    };
    let mut numbered: Vec<(u32, Vec3)> = roots.to_vec();
    numbered.sort_by_key(|(digit, _)| *digit);
    if let [(digit, _)] = numbered[..] {
        return vec![(digit, 1)];
    }
    let thumb = numbered
        .iter()
        .min_by(|a, b| along(a.1).total_cmp(&along(b.1)))
        .map(|(digit, _)| *digit);
    let mut next = 1;
    numbered
        .iter()
        .map(|&(digit, _)| {
            if Some(digit) == thumb {
                (digit, 0)
            } else {
                let finger = next.min(4);
                next += 1;
                (digit, finger)
            }
        })
        .collect()
}

fn er_word(word: &str) -> &'static str {
    match word {
        "clavicle" => "Clavicle",
        "upperarm" => "UpperArm",
        "forearm" => "Forearm",
        "hand" => "Hand",
        "thigh" => "Thigh",
        "calf" => "Calf",
        _ => "Foot",
    }
}

/// Root-level helpers that never carry weight in any of the 74: the model-name root (`c2650`),
/// `R_Light`, `Model_Dmy`, `sfx_dummy`, `固定dmy`, an exact `Master` (`Master Neck1` is a neck),
/// `CTRL_*` control-rig copies, `Dummy_*`, the HP-gauge and object anchors, a bare `arrow`.
fn is_dummy(name: &str) -> bool {
    let model_root = name.len() == 5 && name.starts_with('c') && digits(&name[1..]);
    model_root
        || name.is_empty()
        || matches!(
            name,
            "rlight" | "modeldmy" | "master" | "arrow" | "体力ゲージ"
        )
        || name.strip_prefix("sfxdummy").is_some_and(digits)
        || name.strip_prefix("dmy").is_some_and(digits)
        || name.strip_prefix("オブジェクト").is_some_and(digits)
        || name.starts_with("固定d")
        || name.starts_with("ctrl")
        || name.starts_with("dummy")
}

/// A weapon, shield, staff or bow, by the census's pattern (`weapon`, `wepon`, `wp_`, `shield`,
/// `sword`, `wand`, `stick`, `arrow`) and the rule table's (`bow`, `bpw`, `lance`, `knife`), plus
/// c2650's `R_W_Cloth_*`, the chain its lantern hangs from.
fn is_held_item(name: &str, raw: &str) -> bool {
    const WORDS: [&str; 11] = [
        "weapon", "wepon", "shield", "sword", "wand", "stick", "arrow", "bow", "bpw", "lance",
        "knife",
    ];
    let raw = raw.trim().to_lowercase();
    WORDS.iter().any(|word| name.contains(word))
        || raw.contains("wp_")
        || raw
            .split(|c: char| !c.is_ascii_alphanumeric())
            .any(|word| word == "wep")
        || raw.starts_with("r_w_")
        || raw.starts_with("l_w_")
}

/// The joint at the far end of a core segment: `L_UpperArm` -> `L_Forearm`.
fn segment_end(core: &str) -> Option<String> {
    let (side, joint) = core.split_once('_')?;
    let end = match joint {
        "UpperArm" => "Forearm",
        "Forearm" => "Hand",
        "Thigh" => "Calf",
        "Calf" => "Foot",
        _ => return None,
    };
    Some(format!("{side}_{end}"))
}

/// How far `point` is from the segment `from`..`to`.
fn distance_to_segment(point: Vec3, from: Vec3, to: Vec3) -> f32 {
    let along = sub(to, from);
    let span = along.x * along.x + along.y * along.y + along.z * along.z;
    let offset = sub(point, from);
    let t = if span > f32::EPSILON {
        ((offset.x * along.x + offset.y * along.y + offset.z * along.z) / span).clamp(0.0, 1.0)
    } else {
        0.0
    };
    length(sub(
        offset,
        Vec3 {
            x: along.x * t,
            y: along.y * t,
            z: along.z * t,
        },
    ))
}

/// Every bone's role. Fails when a twist link with a parent does not lie on its core segment.
pub fn classify(bones: &[Bone]) -> Result<Vec<Role>> {
    let rules: Vec<NameRule> = bones
        .iter()
        .map(|bone| name_rule(&normalise(&bone.name), &bone.name))
        .collect();
    let ancestors = |index: usize| {
        let mut chain = Vec::new();
        let mut parent = bones[index].parent_index;
        while let Ok(up) = usize::try_from(parent) {
            if up >= bones.len() || chain.contains(&up) || chain.len() > bones.len() {
                break;
            }
            chain.push(up);
            parent = bones[up].parent_index;
        }
        chain
    };
    let positions: Vec<Vec3> = bind_positions(bones)?;

    // The name rules, with held items carried down their subtrees.
    let mut roles: Vec<Option<Role>> = rules
        .iter()
        .enumerate()
        .map(|(index, rule)| match rule {
            NameRule::Unused => Some(Role::Unused),
            _ if rule == &NameRule::Prop
                || ancestors(index)
                    .iter()
                    .any(|&up| rules[up] == NameRule::Prop) =>
            {
                Some(Role::Prop)
            }
            NameRule::Direct(target) => Some(Role::Direct(target.clone())),
            NameRule::Spine => Some(Role::SpineChain(
                ancestors(index)
                    .iter()
                    .filter(|&&up| rules[up] == NameRule::Spine)
                    .count(),
            )),
            _ => None,
        })
        .collect();

    // Numbered digits become fingers by where they sit on their hand.
    let frames = bind_frames(bones)?;
    let hand_of = |roles: &[Option<Role>], index: usize, side: &str| {
        ancestors(index).into_iter().find(
            |&up| matches!(&roles[up], Some(Role::Direct(name)) if *name == format!("{side}_Hand")),
        )
    };
    let mut fingers: Vec<((usize, u32), u32)> = Vec::new();
    let mut hands: Vec<(usize, Vec<(u32, Vec3)>)> = Vec::new();
    for (index, rule) in rules.iter().enumerate() {
        if let NameRule::Digit {
            side,
            digit,
            joint: 1,
        } = *rule
            && roles[index].is_none()
            && let Some(hand) = hand_of(&roles, index, side)
        {
            match hands.iter_mut().find(|(at, _)| *at == hand) {
                Some((_, roots)) => roots.push((digit, positions[index])),
                None => hands.push((hand, vec![(digit, positions[index])])),
            }
        }
    }
    for (hand, roots) in &hands {
        for (digit, finger) in digit_fingers(frames[*hand], roots) {
            fingers.push(((*hand, digit), finger));
        }
    }
    for (index, rule) in rules.iter().enumerate() {
        let NameRule::Digit { side, digit, joint } = *rule else {
            continue;
        };
        if roles[index].is_some() {
            continue;
        }
        let finger = hand_of(&roles, index, side)
            .and_then(|hand| {
                fingers
                    .iter()
                    .find(|(key, _)| *key == (hand, digit))
                    .map(|(_, finger)| *finger)
            })
            .unwrap_or((digit - 1).min(4));
        let joint = match joint {
            1 => String::new(),
            more => (more - 1).min(2).to_string(),
        };
        roles[index] = Some(Role::Direct(format!("{side}_Finger{finger}{joint}")));
    }

    // Twist links ride the core bone of the segment they lie on.
    let first_direct = |roles: &[Option<Role>], target: &str| {
        roles
            .iter()
            .position(|role| matches!(role, Some(Role::Direct(name)) if name == target))
    };
    for (index, rule) in rules.iter().enumerate() {
        let NameRule::Twist(core_name) = rule else {
            continue;
        };
        if roles[index].is_some() {
            continue;
        }
        let Some(core) = first_direct(&roles, core_name) else {
            continue;
        };
        if bones[index].parent_index >= 0 && bones[core].parent_index >= 0 {
            let end = segment_end(core_name)
                .and_then(|end| first_direct(&roles, &end))
                .filter(|&end| bones[end].parent_index >= 0)
                .map_or(positions[core], |end| positions[end]);
            let distance = distance_to_segment(positions[index], positions[core], end);
            if distance > TWIST_JOINT_TOLERANCE {
                return Err(Error::new(format!(
                    "twist bone {:?} sits {:.1} mm off the segment from {:?} its name says it \
                     twists",
                    bones[index].name,
                    distance * 1000.0,
                    bones[core].name
                )));
            }
        }
        roles[index] = Some(Role::Twist(core));
    }

    // Everything left goes to its nearest mapped ancestor, or nowhere.
    let resolved: Vec<Role> = (0..bones.len())
        .map(|index| {
            roles[index].clone().unwrap_or_else(|| {
                ancestors(index)
                    .into_iter()
                    .find(|&up| roles[up].as_ref().is_some_and(Role::is_mapped))
                    .map_or(Role::Unused, Role::Ancestor)
            })
        })
        .collect();
    Ok(resolved)
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

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(raw: &str) -> NameRule {
        name_rule(&normalise(raw), raw)
    }

    fn direct(target: &str) -> NameRule {
        NameRule::Direct(target.to_owned())
    }

    #[test]
    fn variant_spellings_land_on_one_rule() {
        assert_eq!(normalise("Bip01 L Finger0Nub"), "lfinger0nub");
        assert_eq!(rule("Jaw "), direct("Head"));
        assert_eq!(rule("jaw"), direct("Head"));
        assert_eq!(rule("R weapon "), NameRule::Prop);
        assert_eq!(rule("R_weapon"), NameRule::Prop);
        assert_eq!(rule("R_W_Cloth_a3"), NameRule::Prop);
        assert_eq!(rule("L_Clavicle"), direct("L_Clavicle"));
        assert_eq!(rule("R_Clavicle01"), direct("R_Clavicle"));
        assert_eq!(rule("R UpperArm01"), direct("R_UpperArm"));
        assert_eq!(rule("Master Neck1"), direct("Neck"));
        assert_eq!(rule("Master"), NameRule::Unused);
        assert_eq!(rule("L Finger0"), direct("L_Finger0"));
        assert_eq!(rule("L Finger01"), direct("L_Finger01"));
        assert_eq!(rule("L Finger13"), direct("L_Finger12"));
        assert_eq!(rule("LCalfTwist"), NameRule::Twist("L_Calf".to_owned()));
        assert_eq!(rule("L ForeTwist"), NameRule::Twist("L_Forearm".to_owned()));
        assert_eq!(
            rule("LUpArmTwist1"),
            NameRule::Twist("L_UpperArm".to_owned())
        );
        assert_eq!(rule("RThighTwist"), NameRule::Twist("R_Thigh".to_owned()));
        assert_eq!(rule("HeadNub"), NameRule::Nub);
        assert_eq!(rule("Spine1"), NameRule::Spine);
        assert_eq!(rule("L Toe01"), direct("L_Toe0"));
        assert_eq!(rule("c2650"), NameRule::Unused);
        assert_eq!(rule("固定dmy"), NameRule::Unused);
        assert_eq!(rule("CTRL_L UpperArm"), NameRule::Unused);
        assert_eq!(rule("S_Cloth_food"), NameRule::Other);
        assert_eq!(rule("L Thigh"), direct("L_Thigh"));
        assert_eq!(rule("L Thightwist"), NameRule::Twist("L_Thigh".to_owned()));
    }

    fn bone(name: &str, parent: i16, at: [f32; 3]) -> Bone {
        Bone {
            name: name.to_owned(),
            parent_index: parent,
            translation: Vec3 {
                x: at[0],
                y: at[1],
                z: at[2],
            },
            rotation: Vec3::default(),
            scale: Vec3 {
                x: 1.0,
                y: 1.0,
                z: 1.0,
            },
        }
    }

    /// A small Biped: translations are relative to the parent, and nothing is rotated.
    fn skeleton() -> Vec<Bone> {
        vec![
            bone("Pelvis", -1, [0.0, 0.9, 0.0]),        // 0
            bone("Spine", 0, [0.0, 0.1, 0.0]),          // 1
            bone("Spine1", 1, [0.0, 0.2, 0.0]),         // 2
            bone("Neck", 2, [0.0, 0.25, 0.0]),          // 3
            bone("Head", 3, [0.0, 0.05, 0.0]),          // 4
            bone("Jaw ", 4, [0.0, 0.05, -0.02]),        // 5
            bone("L UpperArm", 2, [0.15, 0.2, 0.0]),    // 6
            bone("L Forearm", 6, [0.26, 0.0, 0.0]),     // 7
            bone("L ForeTwist", 6, [0.26, 0.0, 0.0]),   // 8
            bone("R Hand", 2, [-0.6, 0.2, 0.0]),        // 9
            bone("R_weapon", 9, [-0.1, 0.0, 0.0]),      // 10
            bone("R_W_Cloth_a1", 10, [0.0, -0.1, 0.0]), // 11
            bone("S_Cloth_a1", 2, [0.0, 0.1, -0.1]),    // 12
            bone("S_Cloth_a2", 12, [0.0, -0.1, 0.0]),   // 13
            bone("HeadNub", 4, [0.0, 0.2, 0.0]),        // 14
            bone("L Finger0Nub", -1, [0.03, 0.0, 0.0]), // 15
            bone("Master", -1, [0.0, 0.9, 0.0]),        // 16
            bone("tail_a", -1, [0.0, 0.0, 0.0]),        // 17
            bone("LCalfTwist", -1, [0.08, 0.0, 0.0]),   // 18
            bone("L Calf", 0, [0.08, -0.45, 0.0]),      // 19
            bone("L Hand", 7, [0.26, 0.0, 0.0]),        // 20
            bone("L ForeTwist1", 8, [0.13, 0.0, 0.0]),  // 21
        ]
    }

    #[test]
    fn a_skeleton_resolves_twist_cloth_props_and_nubs() {
        let roles = classify(&skeleton()).unwrap();
        let expected = [
            Role::Direct("Pelvis".to_owned()),
            Role::SpineChain(0),
            Role::SpineChain(1),
            Role::Direct("Neck".to_owned()),
            Role::Direct("Head".to_owned()),
            Role::Direct("Head".to_owned()),
            Role::Direct("L_UpperArm".to_owned()),
            Role::Direct("L_Forearm".to_owned()),
            // The twist rides the forearm at its own joint, not its parent the upper arm.
            Role::Twist(7),
            Role::Direct("R_Hand".to_owned()),
            Role::Prop,
            // Hanging from the weapon makes it part of the item.
            Role::Prop,
            // A two-deep cloth chain goes to its first mapped ancestor.
            Role::Ancestor(2),
            Role::Ancestor(2),
            Role::Ancestor(4),
            // A root-level Nub has no parent to resolve to.
            Role::Unused,
            Role::Unused,
            // An orphan with no mapped ancestor.
            Role::Unused,
            // An orphaned twist is placed by name; its bind is not a position, so it is not
            // checked against the calf.
            Role::Twist(19),
            Role::Direct("L_Calf".to_owned()),
            Role::Direct("L_Hand".to_owned()),
            // A numbered link halfway down the forearm rides the forearm too.
            Role::Twist(7),
        ];
        assert_eq!(roles, expected);
        assert_eq!(roles[8].er_target(&roles).as_deref(), Some("L_Forearm"));
        assert_eq!(
            roles[13].er_target(&roles).as_deref(),
            Some("Spine/Spine1/Spine2 by height")
        );
        assert_eq!(roles[10].er_target(&roles), None);
    }

    #[test]
    fn a_twist_off_its_joint_is_refused() {
        let mut bones = skeleton();
        bones[8].translation.x = 0.2;
        let error = classify(&bones).unwrap_err().to_string();
        assert!(
            error.contains("L ForeTwist") && error.contains("60.0 mm"),
            "{error}"
        );
        // A numbered link may sit anywhere along the segment, but not beside it.
        let mut bones = skeleton();
        bones[21].translation.y = 0.01;
        let error = classify(&bones).unwrap_err().to_string();
        assert!(
            error.contains("L ForeTwist1") && error.contains("10.0 mm"),
            "{error}"
        );
    }

    #[test]
    fn spaced_or_underscored_upper_arms_make_a_biped() {
        let named = |name: &str| vec![bone(name, -1, [0.0; 3])];
        assert!(is_biped(&named("L UpperArm")));
        assert!(is_biped(&named("Bip01 R UpperArm01")));
        assert!(is_biped(&named("L_UpperArm")));
        assert!(!is_biped(&named("L_UpperArmPad")));
        assert!(!is_biped(&named("CTRL_L UpperArm")));
        assert!(!is_biped(&named("LArm1")));
    }

    #[test]
    fn fromsoft_and_numbered_names_take_the_biped_roles() {
        let twist = |target: &str| NameRule::Twist(target.to_owned());
        assert_eq!(rule("LArmCollarbone"), direct("L_Clavicle"));
        assert_eq!(rule("LArm11"), direct("L_UpperArm"));
        assert_eq!(rule("RArm1"), direct("R_UpperArm"));
        assert_eq!(rule("LArm12"), twist("L_UpperArm"));
        assert_eq!(rule("LArm21"), direct("L_Forearm"));
        assert_eq!(rule("LArm2"), direct("L_Forearm"));
        assert_eq!(rule("LArm23"), twist("L_Forearm"));
        assert_eq!(rule("LArmPalm"), direct("L_Hand"));
        let digit = |side, digit, joint| NameRule::Digit { side, digit, joint };
        assert_eq!(rule("LArmDigit11"), digit("L", 1, 1));
        assert_eq!(rule("LArmDigit23"), digit("L", 2, 3));
        assert_eq!(rule("RArmDigit52"), digit("R", 5, 2));
        assert_eq!(rule("LLeg1"), direct("L_Thigh"));
        assert_eq!(rule("LLeg2"), direct("L_Calf"));
        assert_eq!(rule("LLeg3"), direct("L_Calf"));
        assert_eq!(rule("LLegAnkle"), direct("L_Foot"));
        assert_eq!(rule("RLegDigit21"), direct("R_Toe0"));
        assert_eq!(rule("LUpperarm1"), direct("L_UpperArm"));
        assert_eq!(rule("LUpperarm2"), twist("L_UpperArm"));
        assert_eq!(rule("RForearm2"), twist("R_Forearm"));
        assert_eq!(rule("LThigh1"), direct("L_Thigh"));
        assert_eq!(rule("LCalf2"), twist("L_Calf"));
        assert_eq!(rule("LCollarbone"), direct("L_Clavicle"));
        assert_eq!(rule("RPalm"), direct("R_Hand"));
        assert_eq!(rule("LDigit13"), digit("L", 1, 3));
        // Anything else that starts the same way is left to the later rules.
        assert_eq!(rule("LArm_a_11"), NameRule::Other);
        assert_eq!(rule("LLeg11"), NameRule::Other);
        assert_eq!(rule("LArmDigit11Nub"), NameRule::Nub);
        assert_eq!(rule("LArm3"), NameRule::Other);
        assert_eq!(rule("R_W_Arm1"), NameRule::Prop);

        let named = |names: &[&str]| -> Vec<Bone> {
            names.iter().map(|name| bone(name, -1, [0.0; 3])).collect()
        };
        assert!(is_biped(&named(&["LArm11", "LArmPalm", "LLeg1"])));
        assert!(is_biped(&named(&["RUpperarm1", "RPalm", "LThigh1"])));
        // No palm, no hip, or lettered limbs.
        assert!(!is_biped(&named(&["LArm1", "LArm2", "LLeg1"])));
        assert!(!is_biped(&named(&["LArm1", "LArmPalm"])));
        assert!(!is_biped(&named(&["LArm_a_11", "LArm_a_Palm", "LLeg1"])));
    }

    #[test]
    fn the_digit_nearest_the_wrist_is_the_thumb() {
        let finger = |roles: &[Role], index: usize| match &roles[index] {
            Role::Direct(name) => name.clone(),
            other => panic!("{other:?}"),
        };
        // Scarecrow's hand: a long mitten numbered first and a stub of a thumb numbered second.
        let scarecrow = vec![
            bone("Pelvis", -1, [0.0, 1.0, 0.0]),
            bone("LArmPalm", 0, [1.0, 0.0, 0.0]),
            bone("LArmDigit11", 1, [0.16, 0.0, 0.0]),
            bone("LArmDigit21", 1, [0.015, -0.04, 0.0]),
            bone("LArmDigit22", 3, [0.0, -0.03, 0.0]),
        ];
        let roles = classify(&scarecrow).unwrap();
        assert_eq!(finger(&roles, 2), "L_Finger1");
        assert_eq!(finger(&roles, 3), "L_Finger0");
        assert_eq!(finger(&roles, 4), "L_Finger01");
        // A five-digit hand with the thumb first, as Gwynevere's and the Bloatheads' are.
        let mut five = vec![
            bone("Pelvis", -1, [0.0, 1.0, 0.0]),
            bone("RArmPalm", 0, [-1.0, 0.0, 0.0]),
            bone("RArmDigit11", 1, [0.05, 0.0, 0.03]),
        ];
        for digit in 2..=5 {
            five.push(bone(
                &format!("RArmDigit{digit}1"),
                1,
                [0.1, 0.0, 0.02 * digit as f32],
            ));
        }
        five.push(bone("RArmDigit53", 6, [0.02, 0.0, 0.0]));
        let roles = classify(&five).unwrap();
        let names: Vec<String> = (2..=7).map(|index| finger(&roles, index)).collect();
        assert_eq!(
            names,
            [
                "R_Finger0",
                "R_Finger1",
                "R_Finger2",
                "R_Finger3",
                "R_Finger4",
                "R_Finger42"
            ]
        );
    }
}
