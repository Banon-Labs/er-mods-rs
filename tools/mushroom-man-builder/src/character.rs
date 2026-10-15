//! The Dark Souls characters this builder can turn the player into, and which of them have been
//! checked in game.
//!
//! An id that is not in [`CHARACTERS`] is refused outright, and that refusal -- not `map-report`
//! -- is what keeps a character whose rig cannot be fitted out of a package. A character that is
//! in the table but not yet `validated` is built only on request (`--unvalidated`), and is never
//! offered by the prompt or by a plain `--list-characters`, so an exe built between two in-game
//! checks never hands a player a body nobody has looked at.
//!
//! Everything else is derived from the id: the binder is `chr/<id>.chrbnd.dcx`, the model
//! `<id>.flver`, and the textures are whatever the materials of its kept meshes name, read out of
//! the binder of the character each path points at (c2900's body is drawn with c2909's).

use crate::{
    error::{Error, Result},
    fit::{biped::BipedParams, mushroom::FitParams},
};

/// Whose bone names the source skeleton uses, which decides how it is fitted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Skeleton {
    /// c2280's own child rig: one bone per leg, `LArm1`/`LArmPalm`/`LArmDigit12` arms.
    MushroomChild,
    /// 3ds Max Biped names: `L UpperArm`, `L Finger01`, `LCalfTwist`, cloth chains, props.
    Biped,
}

/// How the source mesh is laid onto Elden Ring's body.
#[derive(Clone, Copy, Debug)]
pub enum FitConfig {
    /// c2280's band-and-segment fit (`fit::mushroom`).
    Mushroom(FitParams),
    /// The bone-frame fit of a 3ds Max Biped skeleton (`fit::biped`).
    Biped(BipedParams),
}

/// How the fitted mesh gets into the donor FLVER.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Writer {
    /// Overwrite donor mesh 13's own buffers in place (`flver::patch`), within their capacity:
    /// what c2280 has shipped with, and what keeps its package byte-identical.
    Patch,
    /// Rebuild the FLVER's data section around buffers of any size (`flver::rebuild`).
    Rebuild,
}

#[derive(Debug)]
pub struct Character {
    /// The DSR model id, `c2280`.
    pub id: &'static str,
    /// What a player calls it.
    pub label: &'static str,
    pub skeleton: Skeleton,
    pub fit: FitConfig,
    pub writer: Writer,
    /// Checked in game and approved. Only these are offered without `--unvalidated`.
    pub validated: bool,
}

/// The Mushroom Man, as the user approved it in game: `FitParams::default()` on the patch path.
pub const C2280: Character = Character {
    id: "c2280",
    label: "Mushroom Man",
    skeleton: Skeleton::MushroomChild,
    fit: FitConfig::Mushroom(FitParams {
        scale: 1.225,
        arm_lean: 0.08,
        foot_spread: 0.08,
        shoulder_blend: 0.20,
    }),
    writer: Writer::Patch,
    validated: true,
};

/// The first Biped character. 2607 vertices with its lantern, 1745 without, so it needs the
/// rebuild writer: donor mesh 13 holds 1500. Fitted with the defaults it was measured with: the
/// girth scale derived from the pelvis heights (1.027), no arm lean, the thumb on the hand, the
/// lantern cut, cloth and twist links on their Biped bones.
pub const C2650: Character = Character {
    id: "c2650",
    label: "Dark Souls character c2650",
    skeleton: Skeleton::Biped,
    fit: FitConfig::Biped(BipedParams::DEFAULT),
    writer: Writer::Rebuild,
    validated: false,
};

/// The plain Skeleton: NpcParam rows 290000-290002, `スケルトン＿曲剣`, `＿弓` and `＿大曲剣`,
/// the one model with a curved sword, a bow or a curved greatsword. Its sword, greatsword, shield
/// and bow are four meshes of their own, all cut, so its three body meshes go in, drawn with
/// c2909's textures. Fitted as c2650 is: the girth scale derived from the pelvis heights (0.864,
/// its pelvis standing 1.16 times Elden Ring's), and nothing else changed. The fit probe's one
/// failing gate on it is the grip anchor, 0.036 m from Elden Ring's `R_Weapon` against 0.03: its
/// `R Weapon` bone sits nearer the wrist than Elden Ring's, mostly along the hand, so no girth
/// moves it (0.036 at 0.70, 0.037 at 1.05), and the sword on it is cut anyway.
pub const C2900: Character = Character {
    id: "c2900",
    label: "Skeleton",
    skeleton: Skeleton::Biped,
    fit: FitConfig::Biped(BipedParams::DEFAULT),
    writer: Writer::Rebuild,
    validated: false,
};

pub const CHARACTERS: &[Character] = &[C2280, C2650, C2900];

/// The character a run with no `--character` builds.
pub const DEFAULT_ID: &str = "c2280";

pub fn default_character() -> &'static Character {
    &CHARACTERS[0]
}

/// The table entry for `id`, ignoring case.
pub fn by_id(id: &str) -> Option<&'static Character> {
    CHARACTERS
        .iter()
        .find(|character| character.id.eq_ignore_ascii_case(id.trim()))
}

pub fn validated() -> impl Iterator<Item = &'static Character> {
    CHARACTERS.iter().filter(|character| character.validated)
}

/// The character `--character <id>` names, refused when it is not in the table, or when it has
/// not been checked in game and `allow_unvalidated` was not given.
pub fn resolve(id: &str, allow_unvalidated: bool) -> Result<&'static Character> {
    let Some(character) = by_id(id) else {
        let offered: Vec<String> = validated()
            .map(|character| format!("{} ({})", character.id, character.label))
            .collect();
        return Err(Error::new(format!(
            "there is no character {id} -- this builder makes {} (--list-characters --all lists \
             the ones not yet checked in game)",
            offered.join(", ")
        )));
    };
    if !character.validated && !allow_unvalidated {
        return Err(Error::new(format!(
            "{} ({}) has not been checked in game yet -- pass --unvalidated to build it anyway",
            character.id, character.label
        )));
    }
    Ok(character)
}

/// One line per character for `--list-characters`: only the validated ones, unless `all`.
pub fn listing(all: bool) -> String {
    CHARACTERS
        .iter()
        .filter(|character| all || character.validated)
        .map(|character| {
            let mut line = format!("{:<7} {}", character.id, character.label);
            if character.id == DEFAULT_ID {
                line.push_str(" (default)");
            }
            if !character.validated {
                line.push_str(" [not yet checked in game; needs --unvalidated]");
            }
            line + "\n"
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_unique_and_c2280_is_the_validated_default() {
        let mut ids: Vec<&str> = CHARACTERS.iter().map(|character| character.id).collect();
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), CHARACTERS.len());
        let default = default_character();
        assert_eq!(default.id, DEFAULT_ID);
        assert!(default.validated);
        assert_eq!(default.writer, Writer::Patch);
        assert!(
            CHARACTERS
                .iter()
                .all(|c| c.id.len() == 5 && c.id.starts_with('c'))
        );
    }

    #[test]
    fn c2280_keeps_the_fit_the_user_approved() {
        let FitConfig::Mushroom(params) = C2280.fit else {
            panic!("c2280 is fitted as the mushroom");
        };
        let approved = FitParams::default();
        assert_eq!(params.scale, approved.scale);
        assert_eq!(params.arm_lean, approved.arm_lean);
        assert_eq!(params.foot_spread, approved.foot_spread);
        assert_eq!(params.shoulder_blend, approved.shoulder_blend);
    }

    #[test]
    fn biped_characters_have_biped_skeletons_and_c2650_the_measured_fit() {
        for character in CHARACTERS {
            assert_eq!(
                matches!(character.fit, FitConfig::Biped(_)),
                character.skeleton == Skeleton::Biped,
                "{}",
                character.id
            );
        }
        let FitConfig::Biped(params) = C2650.fit else {
            panic!("c2650 is fitted as a Biped");
        };
        assert_eq!(params, BipedParams::default());
        assert_eq!(params.scale, None);
        // The Skeleton starts from c2650's fit, its girth derived the same way.
        let FitConfig::Biped(skeleton) = C2900.fit else {
            panic!("c2900 is fitted as a Biped");
        };
        assert_eq!(skeleton, params);
        assert_eq!((C2900.writer, C2900.validated), (Writer::Rebuild, false));
        assert!(C2900.label.contains("Skeleton"));
    }

    #[test]
    fn an_unvalidated_character_needs_the_flag_and_an_unknown_one_is_refused() {
        let unvalidated: Vec<&Character> = CHARACTERS.iter().filter(|c| !c.validated).collect();
        assert!(!unvalidated.is_empty());
        for character in unvalidated {
            assert!(resolve(character.id, false).is_err());
            assert_eq!(resolve(character.id, true).unwrap().id, character.id);
        }
        assert_eq!(resolve("C2280", false).unwrap().id, "c2280");
        let unknown = resolve("c9999", true).unwrap_err().to_string();
        assert!(unknown.contains("c9999") && unknown.contains("c2280"));
    }

    #[test]
    fn the_plain_listing_hides_what_has_not_been_checked() {
        let plain = listing(false);
        let all = listing(true);
        for character in CHARACTERS {
            assert!(all.contains(character.id));
            assert_eq!(plain.contains(character.id), character.validated);
        }
        assert!(all.contains("--unvalidated"));
    }
}
